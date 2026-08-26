#!/usr/bin/env python3
"""Download pinned SPAR3D assets without caching the gated model.

No Hugging Face cache is created and the token is read only from HF_TOKEN.
The token is never printed, persisted, placed in a URL, or passed as a command
line argument. The large weights can be streamed directly into release-sized
parts so the Windows runner never stores a second complete model file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


MODEL_REPOSITORY = "stabilityai/stable-point-aware-3d"
RUNTIME_MODEL_FILES = ("config.yaml", "LICENSE.md", "README.md")
MODEL_WEIGHT_NAME = "model.safetensors"
BACKGROUND_URL = (
    "https://github.com/plemeri/transparent-background/"
    "releases/download/1.2.12/ckpt_base.pth"
)
BACKGROUND_MD5 = "d692e3dd5fa1b9658949d452bebf1cda"


def stream_download(
    url: str,
    destination: Path,
    *,
    headers: dict[str, str] | None = None,
    expected_md5: str | None = None,
    minimum_size: int = 1,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    partial.unlink(missing_ok=True)

    sha256 = hashlib.sha256()
    md5 = hashlib.md5(usedforsecurity=False)
    written = 0
    report_at = 512 * 1024 * 1024

    request_headers = {
        "User-Agent": "MujassamAI-GitHub-Actions/1.0",
        "Accept": "application/octet-stream",
    }
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, headers=request_headers)
    print(f"downloading {destination.name}", flush=True)

    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            content_length = int(response.headers.get("Content-Length", "0"))
            etag = response.headers.get("ETag", "").strip('"')

            with partial.open("wb") as output:
                while True:
                    block = response.read(8 * 1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    sha256.update(block)
                    md5.update(block)
                    written += len(block)

                    if written >= report_at:
                        if content_length:
                            print(
                                f"  {written / 1_000_000_000:.2f}/"
                                f"{content_length / 1_000_000_000:.2f} GB",
                                flush=True,
                            )
                        else:
                            print(
                                f"  {written / 1_000_000_000:.2f} GB",
                                flush=True,
                            )
                        report_at += 512 * 1024 * 1024

        if written < minimum_size:
            raise RuntimeError(
                f"{destination.name} was unexpectedly small: {written} bytes"
            )

        actual_md5 = md5.hexdigest()
        if expected_md5 and actual_md5.lower() != expected_md5.lower():
            raise RuntimeError(
                f"MD5 mismatch for {destination.name}: {actual_md5}"
            )

        partial.replace(destination)
        return {
            "file": destination.name,
            "bytes": written,
            "sha256": sha256.hexdigest(),
            "md5": actual_md5,
            "etag": etag,
        }
    except urllib.error.HTTPError as error:
        partial.unlink(missing_ok=True)
        if error.code in (401, 403):
            raise RuntimeError(
                "Hugging Face denied access. Verify that the HF_TOKEN owner "
                "accepted the gated SPAR3D license and that the token has read access."
            ) from None
        raise RuntimeError(
            f"download failed with HTTP status {error.code}: {destination.name}"
        ) from None
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def download_huggingface_file(
    filename: str,
    destination: Path,
    revision: str,
    token: str,
) -> dict[str, Any]:
    encoded_revision = urllib.parse.quote(revision, safe="")
    encoded_filename = urllib.parse.quote(filename, safe="/")
    url = (
        f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/"
        f"{encoded_revision}/{encoded_filename}?download=true"
    )
    minimum_size = 6_000_000_000 if filename == MODEL_WEIGHT_NAME else 1
    return stream_download(
        url,
        destination,
        headers={"Authorization": f"Bearer {token}"},
        minimum_size=minimum_size,
    )


def stream_download_parts(
    url: str,
    destination_directory: Path,
    *,
    prefix: str,
    part_size: int,
    headers: dict[str, str] | None = None,
    minimum_size: int = 1,
) -> dict[str, Any]:
    if part_size < 1024 * 1024:
        raise ValueError("part size must be at least 1 MiB")

    destination_directory.mkdir(parents=True, exist_ok=True)
    if list(destination_directory.glob(f"{prefix}.part*")):
        raise RuntimeError(f"refusing to overwrite existing {prefix} parts")

    request_headers = {
        "User-Agent": "MujassamAI-GitHub-Actions/1.0",
        "Accept": "application/octet-stream",
    }
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, headers=request_headers)
    complete_paths: list[Path] = []
    partial_paths: list[Path] = []
    part_records: list[dict[str, Any]] = []
    whole_sha256 = hashlib.sha256()
    total_written = 0
    report_at = 512 * 1024 * 1024

    print(f"downloading {MODEL_WEIGHT_NAME} as {part_size}-byte parts", flush=True)
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            content_length = int(response.headers.get("Content-Length", "0"))
            part_number = 0

            while content_length == 0 or total_written < content_length:
                part_number += 1
                part_name = f"{prefix}.part{part_number:03d}"
                final_path = destination_directory / part_name
                partial_path = final_path.with_name(final_path.name + ".partial")
                partial_paths.append(partial_path)
                part_sha256 = hashlib.sha256()
                part_written = 0

                with partial_path.open("xb") as output:
                    while part_written < part_size:
                        wanted = min(8 * 1024 * 1024, part_size - part_written)
                        block = response.read(wanted)
                        if not block:
                            break
                        output.write(block)
                        part_sha256.update(block)
                        whole_sha256.update(block)
                        part_written += len(block)
                        total_written += len(block)

                        if total_written >= report_at:
                            if content_length:
                                print(
                                    f"  {total_written / 1_000_000_000:.2f}/"
                                    f"{content_length / 1_000_000_000:.2f} GB",
                                    flush=True,
                                )
                            else:
                                print(
                                    f"  {total_written / 1_000_000_000:.2f} GB",
                                    flush=True,
                                )
                            report_at += 512 * 1024 * 1024

                if part_written == 0:
                    partial_path.unlink(missing_ok=True)
                    partial_paths.remove(partial_path)
                    break

                partial_path.replace(final_path)
                partial_paths.remove(partial_path)
                complete_paths.append(final_path)
                part_records.append(
                    {
                        "name": part_name,
                        "bytes": part_written,
                        "sha256": part_sha256.hexdigest(),
                    }
                )

                if part_written < part_size:
                    break

        if total_written < minimum_size:
            raise RuntimeError(
                f"{MODEL_WEIGHT_NAME} was unexpectedly small: {total_written} bytes"
            )
        if content_length and total_written != content_length:
            raise RuntimeError(
                f"download length mismatch: expected {content_length}, got {total_written}"
            )

        return {
            "file_name": MODEL_WEIGHT_NAME,
            "destination": "models/spar3d/model.safetensors",
            "bytes": total_written,
            "sha256": whole_sha256.hexdigest(),
            "parts": part_records,
        }
    except urllib.error.HTTPError as error:
        for path in partial_paths + complete_paths:
            path.unlink(missing_ok=True)
        if error.code in (401, 403):
            raise RuntimeError(
                "Hugging Face denied access. Verify that the HF_TOKEN owner "
                "accepted the gated SPAR3D license and that the token has read access."
            ) from None
        raise RuntimeError(
            f"download failed with HTTP status {error.code}: {MODEL_WEIGHT_NAME}"
        ) from None
    except BaseException:
        for path in partial_paths + complete_paths:
            path.unlink(missing_ok=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True, choices=("runtime-assets", "weights"))
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--background-dir", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--parts-dir", type=Path)
    parser.add_argument("--part-prefix", default="MujassamAI-model.safetensors")
    parser.add_argument("--part-size", type=int, default=1_890_000_000)
    parser.add_argument("--record", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise RuntimeError("HF_TOKEN is required")

    if args.mode == "weights":
        if args.parts_dir is None or args.record is None:
            raise RuntimeError("weights mode requires --parts-dir and --record")
        encoded_revision = urllib.parse.quote(args.revision, safe="")
        url = (
            f"https://huggingface.co/{MODEL_REPOSITORY}/resolve/"
            f"{encoded_revision}/{MODEL_WEIGHT_NAME}?download=true"
        )
        record = stream_download_parts(
            url,
            args.parts_dir,
            prefix=args.part_prefix,
            part_size=args.part_size,
            headers={"Authorization": f"Bearer {token}"},
            minimum_size=6_000_000_000,
        )
        args.record.parent.mkdir(parents=True, exist_ok=True)
        args.record.write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"weights record written: {args.record}")
        return 0

    if args.model_dir is None or args.background_dir is None or args.manifest is None:
        raise RuntimeError(
            "runtime-assets mode requires --model-dir, --background-dir, and --manifest"
        )

    args.model_dir.mkdir(parents=True, exist_ok=True)
    args.background_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    for filename in RUNTIME_MODEL_FILES:
        records.append(
            download_huggingface_file(
                filename,
                args.model_dir / filename,
                args.revision,
                token,
            )
        )

    records.append(
        stream_download(
            BACKGROUND_URL,
            args.background_dir / "ckpt_base.pth",
            expected_md5=BACKGROUND_MD5,
            minimum_size=100_000_000,
        )
    )

    manifest = {
        "model_repository": MODEL_REPOSITORY,
        "model_revision": args.revision,
        "external_weights": "models/spar3d/model.safetensors",
        "files": records,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"model manifest written: {args.manifest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"model download error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
