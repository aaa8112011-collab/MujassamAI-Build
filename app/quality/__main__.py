from __future__ import annotations

import argparse
import json

from .realesrgan_x2 import self_test


def main() -> int:
    parser = argparse.ArgumentParser(description="Mujassam AI texture-quality utilities")
    parser.add_argument("--self-test", action="store_true", help="run tests that need no model")
    args = parser.parse_args()
    if not args.self_test:
        parser.error("choose --self-test")
    print(json.dumps(self_test(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
