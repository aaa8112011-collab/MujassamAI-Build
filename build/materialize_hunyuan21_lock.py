#!/usr/bin/env python3
"""Materialize one-platform pip hash locks from resolved Windows wheels."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from email.parser import Parser
from pathlib import Path


REQUIREMENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s]+)$")


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def read_resolved(path: Path) -> dict[str, tuple[str, str]]:
    resolved: dict[str, tuple[str, str]] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = REQUIREMENT_RE.fullmatch(line)
        if match is None:
            raise ValueError(f"unsupported resolved requirement at {path}:{number}: {line}")
        display_name, version = match.groups()
        name = canonical_name(display_name)
        if name in resolved:
            raise ValueError(f"duplicate resolved requirement: {name}")
        resolved[name] = (display_name, version)
    if not resolved:
        raise ValueError(f"resolved requirement set is empty: {path}")
    return resolved


def filter_freeze(source: Path, output: Path, excluded: set[str]) -> None:
    resolved = read_resolved(source)
    selected = {
        name: item for name, item in resolved.items() if name not in excluded
    }
    if not selected:
        raise ValueError("every resolved requirement was excluded")
    lines = [
        f"{selected[name][0]}=={selected[name][1]}" for name in sorted(selected)
    ]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def wheel_identity(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path, "r") as wheel:
        metadata_names = [
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_names) != 1:
            raise ValueError(f"wheel must contain exactly one METADATA file: {path.name}")
        metadata_bytes = wheel.read(metadata_names[0])
    metadata = Parser().parsestr(metadata_bytes.decode("utf-8"))
    name = metadata.get("Name")
    version = metadata.get("Version")
    if not name or not version:
        raise ValueError(f"wheel metadata lacks Name/Version: {path.name}")
    return canonical_name(name), version


def materialize_lock(resolved_path: Path, wheelhouse: Path, output: Path, title: str) -> None:
    resolved = read_resolved(resolved_path)
    wheels = sorted(wheelhouse.glob("*.whl"))
    if not wheels:
        raise ValueError(f"wheelhouse is empty: {wheelhouse}")

    hashes: dict[str, list[tuple[str, str]]] = {}
    for wheel in wheels:
        name, version = wheel_identity(wheel)
        digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
        hashes.setdefault(name, []).append((version, digest))

    unexpected = sorted(set(hashes) - set(resolved))
    missing = sorted(set(resolved) - set(hashes))
    if unexpected or missing:
        raise ValueError(f"wheel set mismatch; missing={missing}, unexpected={unexpected}")

    body: list[str] = [
        f"# {title}",
        "# Generated for CPython 3.11.9 x64 on Windows; one accepted wheel per package.",
        "# Regenerate and verify before changing any direct dependency pin.",
    ]
    for name in sorted(resolved):
        display_name, expected_version = resolved[name]
        candidates = hashes[name]
        if len(candidates) != 1:
            raise ValueError(f"expected one wheel for {name}, found {len(candidates)}")
        actual_version, digest = candidates[0]
        if actual_version != expected_version:
            raise ValueError(
                f"wheel version mismatch for {name}: {actual_version} != {expected_version}"
            )
        body.append(f"{display_name}=={expected_version} --hash=sha256:{digest}")
    output.write_text("\n".join(body) + "\n", encoding="utf-8", newline="\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    filter_parser = subparsers.add_parser("filter")
    filter_parser.add_argument("--freeze", type=Path, required=True)
    filter_parser.add_argument("--output", type=Path, required=True)
    filter_parser.add_argument("--exclude", action="append", default=[])

    lock_parser = subparsers.add_parser("lock")
    lock_parser.add_argument("--resolved", type=Path, required=True)
    lock_parser.add_argument("--wheelhouse", type=Path, required=True)
    lock_parser.add_argument("--output", type=Path, required=True)
    lock_parser.add_argument("--title", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "filter":
            excluded = {canonical_name(value) for value in args.exclude}
            filter_freeze(args.freeze, args.output, excluded)
        else:
            materialize_lock(
                args.resolved, args.wheelhouse, args.output, args.title
            )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"lock materialization failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
