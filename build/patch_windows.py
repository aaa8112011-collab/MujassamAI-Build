#!/usr/bin/env python3
"""Apply the narrowly scoped Windows compiler patch required by SPAR3D.

The pinned upstream commit passes GCC flags to MSVC in both native extension
setup files. This script replaces only those argument dictionaries and fails
closed if the expected upstream text has changed.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


COMPILE_BLOCK = re.compile(
    r"^    extra_compile_args = \{.*?^    \}\r?\n(?=    if debug_mode:)",
    flags=re.MULTILINE | re.DOTALL,
)

TEXTURE_REPLACEMENT = """    # MUJASSAM_WINDOWS_PATCH
    extra_compile_args = {
        "cxx": ["/O2" if not debug_mode else "/Od", "/std:c++17", "/openmp"],
        "nvcc": ["-O3" if not debug_mode else "-O0", "-std=c++17"],
    }
"""

UV_REPLACEMENT = """    # MUJASSAM_WINDOWS_PATCH
    extra_compile_args = {
        "cxx": ["/O2" if not debug_mode else "/Od", "/std:c++17", "/openmp"],
    }
"""


def patch_one(path: Path, replacement: str) -> None:
    original = path.read_text(encoding="utf-8")

    if "MUJASSAM_WINDOWS_PATCH" in original:
        print(f"already patched: {path}")
        return

    patched, count = COMPILE_BLOCK.subn(replacement, original, count=1)
    if count != 1:
        raise RuntimeError(
            f"expected exactly one compile-argument block in {path}; "
            "the pinned upstream source may have changed"
        )

    path.write_text(patched, encoding="utf-8", newline="\n")
    print(f"patched: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source_root",
        type=Path,
        help="Root of the pinned stable-point-aware-3d checkout",
    )
    args = parser.parse_args()

    root = args.source_root.resolve()
    texture_setup = root / "texture_baker" / "setup.py"
    uv_setup = root / "uv_unwrapper" / "setup.py"

    for required in (texture_setup, uv_setup):
        if not required.is_file():
            raise FileNotFoundError(required)

    patch_one(texture_setup, TEXTURE_REPLACEMENT)
    patch_one(uv_setup, UV_REPLACEMENT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
