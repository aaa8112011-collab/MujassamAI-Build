#!/usr/bin/env python3
"""Hunyuan3D-2.1 Shape + Paint PBR worker for Mujassam AI.

The ultimate engine deliberately favours the official high-quality settings.
Shape, Paint, and final GLB construction run in isolated processes so a stage
cannot retain CUDA allocations needed by the next one.  Model snapshots are
downloaded once, pinned to immutable revisions, verified before loading, and
used with every Hugging Face network fallback disabled.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ENGINE_SCHEMA_VERSION = 1
ENGINE_MODE = "hunyuan3d_2_1_pbr"
SHAPE_RESUME_SCHEMA_VERSION = 1
SHAPE_RESUME_MAX_BYTES = 2 * 1024**3
SHAPE_RESUME_PIPELINE_REVISION = "mujassam-hy21-shape-cleanup-v1"
REQUIRED_PYTHON = (3, 11, 9)
REQUIRED_TORCH = "2.5.1+cu124"
REQUIRED_TORCHVISION = "0.20.1+cu124"
REQUIRED_CUDA_RUNTIME = "12.4"
SOURCE_COMMIT = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
MODEL_REPOSITORY = "tencent/Hunyuan3D-2.1"
MODEL_REVISION = "0b94677654c57bb9a6b6845cd7b704ccf551d327"
DINO_REPOSITORY = "facebook/dinov2-giant"
DINO_REVISION = "611a9d42f2335e0f921f1e313ad3c1b7178d206d"
SHAPE_SUBFOLDER = "hunyuan3d-dit-v2-1"
SHAPE_VAE_SUBFOLDER = "hunyuan3d-vae-v2-1"
PAINT_SUBFOLDER = "hunyuan3d-paintpbr-v2-1"

# Ultimate mode is intentionally fixed.  An OOM is reported rather than
# silently replacing these settings with a lower-quality profile.
SHAPE_INFERENCE_STEPS = 50
SHAPE_GUIDANCE_SCALE = 5.0
SHAPE_OCTREE_RESOLUTION = 512
SHAPE_NUM_CHUNKS = 8000
PAINT_MAX_VIEWS = 12
PAINT_VIEW_RESOLUTION = 768
PAINT_RENDER_SIZE = 2048
PBR_TEXTURE_SIZE = 4096
ROBLOX_READY_FACES = 20_000
ROBLOX_MASTER_FACES = 100_000

MAX_DOWNLOAD_ATTEMPTS = 6
DOWNLOAD_STALL_SECONDS = 180
DOWNLOAD_POLL_SECONDS = 5
DOWNLOAD_STATUS_SECONDS = 30
MIN_FREE_DOWNLOAD_BYTES = 32 * 1024**3
LICENSE_SHA256 = "20b7e73b7996a815226ae4c08d18a7891c417749f2de687d1db90b4e36b78789"
UPSTREAM_NOTICE_SHA256 = "7b24e4a03640ff92ef564bd67419eaa181a1e23ae834cf14176b31247348859c"
PERSONAL_LOCAL_USAGE_SCOPE = "personal_local_only"
THIRD_PARTY_USAGE_SCOPE = "third_party_provider"
CI_PROVIDER_SENTINEL = "CI validation build — Hunyuan3D 2.1 disabled"
REALESRGAN_X4_BYTES = 67_040_989
REALESRGAN_X4_SHA256 = "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"
REALESRGAN_X2_BYTES = 67_061_725
REALESRGAN_X2_SHA256 = "49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb"
BACKGROUND_SHA256 = "0a6fe2a73ab0532d6d0b8d82849a9760a226df719e3063d09b4149ece6f80fcd"
PAINT_MODEL_INDEX_BYTES = 617
PAINT_MODEL_INDEX_SHA256 = "5b4d5d7cf32171ae93cbed10e2b775419cc2a7cd23c2c2442657c357f454801c"
MAX_IMAGE_PIXELS = 100_000_000
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

_SENSITIVE_PATTERN = re.compile(
    r"(?i)(?:hf_[a-z0-9]{12,}|bearer\s+[a-z0-9._~+/=-]{12,}|"
    r"(?:token|secret|password|authorization|api[_-]?key)\s*[:=]\s*[^\s|]+)"
)
_URL_USERINFO_PATTERN = re.compile(
    r"(?i)\b(https?://)[^/@\s:]+:[^/@\s]+@"
)

PINNED_MODEL_WEIGHTS: dict[str, tuple[int, str]] = {
    f"{SHAPE_SUBFOLDER}/model.fp16.ckpt": (
        7_366_389_768,
        "6b519fc7242f78e9b5f47ea4d55668fe3d944a2d27332f4ca68d29a6ff603f5e",
    ),
    f"{SHAPE_VAE_SUBFOLDER}/model.fp16.ckpt": (
        655_648_152,
        "5cbe97f25e6e7abd4bccc80ab07524ec0c86d24118486a9ba49bb5dfb070288a",
    ),
    f"{PAINT_SUBFOLDER}/text_encoder/pytorch_model.bin": (
        1_361_671_895,
        "c3e254d7b61353497ea0be2c4013df4ea8f739ee88cffa0ba58cd085459ed565",
    ),
    f"{PAINT_SUBFOLDER}/unet/diffusion_pytorch_model.bin": (
        3_925_293_863,
        "675a1b5cd0098b2002637c443946529c03c5cd54427f40245263350feb3dd5b8",
    ),
    f"{PAINT_SUBFOLDER}/vae/diffusion_pytorch_model.bin": (
        334_707_217,
        "1b4889b6b1d4ce7ae320a02dedaeff1780ad77d415ea0d744b476155c6377ddc",
    ),
    f"{PAINT_SUBFOLDER}/image_encoder/model.safetensors": (
        1_264_217_240,
        "ae616c24393dd1854372b0639e5541666f7521cbe219669255e865cb7f89466a",
    ),
}
DINO_WEIGHT = (
    4_546_005_432,
    "917d3c470db999d32a312f8542149be91c7cbac61ee8fb4b67ae3d82b79ce21f",
)

MODEL_REQUIRED_FILES = (
    f"{SHAPE_SUBFOLDER}/config.yaml",
    f"{SHAPE_SUBFOLDER}/model.fp16.ckpt",
    f"{SHAPE_VAE_SUBFOLDER}/config.yaml",
    f"{SHAPE_VAE_SUBFOLDER}/model.fp16.ckpt",
    f"{PAINT_SUBFOLDER}/model_index.json",
    f"{PAINT_SUBFOLDER}/feature_extractor/preprocessor_config.json",
    f"{PAINT_SUBFOLDER}/image_encoder/config.json",
    f"{PAINT_SUBFOLDER}/image_encoder/model.safetensors",
    f"{PAINT_SUBFOLDER}/scheduler/scheduler_config.json",
    f"{PAINT_SUBFOLDER}/text_encoder/config.json",
    f"{PAINT_SUBFOLDER}/text_encoder/pytorch_model.bin",
    f"{PAINT_SUBFOLDER}/tokenizer/merges.txt",
    f"{PAINT_SUBFOLDER}/tokenizer/special_tokens_map.json",
    f"{PAINT_SUBFOLDER}/tokenizer/tokenizer_config.json",
    f"{PAINT_SUBFOLDER}/tokenizer/vocab.json",
    f"{PAINT_SUBFOLDER}/unet/config.json",
    f"{PAINT_SUBFOLDER}/unet/diffusion_pytorch_model.bin",
    f"{PAINT_SUBFOLDER}/vae/config.json",
    f"{PAINT_SUBFOLDER}/vae/diffusion_pytorch_model.bin",
)
DINO_REQUIRED_FILES = (
    "config.json",
    "preprocessor_config.json",
    "model.safetensors",
)

ENGINE_ROOT = Path(__file__).resolve().parent
APP_ROOT = ENGINE_ROOT.parent.parent
BUNDLE_ROOT = APP_ROOT.parent
VENDOR_ROOT = ENGINE_ROOT / "vendor" / "Hunyuan3D-2.1"
SHAPE_VENDOR_ROOT = VENDOR_ROOT / "hy3dshape"
PAINT_VENDOR_ROOT = VENDOR_ROOT / "hy3dpaint"
PYTHON_PACKAGES = ENGINE_ROOT / "python_packages"
REALESRGAN_X4 = ENGINE_ROOT / "models" / "RealESRGAN_x4plus.pth"
REALESRGAN_X2 = BUNDLE_ROOT / "models" / "realesrgan" / "RealESRGAN_x2plus.pth"
BASE_WORKER_PATH = APP_ROOT / "worker.py"
TRUSTED_PAINT_UNET_ROOT = PAINT_VENDOR_ROOT / "hunyuanpaintpbr" / "unet"
BACKGROUND_CHECKPOINT = BUNDLE_ROOT / "models" / "transparent-background" / "ckpt_base.pth"
SPAR_VENDOR_ROOT = APP_ROOT / "vendor" / "stable-point-aware-3d"


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class StageProcessResult(int):
    """An int-compatible child exit code with its last structured error."""

    child_error: tuple[str, str] | None

    def __new__(
        cls, code: int, child_error: tuple[str, str] | None = None
    ) -> "StageProcessResult":
        value = int.__new__(cls, code)
        value.child_error = child_error
        return value


def _sanitize(value: object, limit: int = 900) -> str:
    text = str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
    text = text.replace("|", "/")
    text = _URL_USERINFO_PATTERN.sub(r"\1[redacted]@", text)
    text = _SENSITIVE_PATTERN.sub("[redacted]", text)
    text = " ".join(text.split())
    return text[:limit] or "unknown error"


def _progress(percent: int, message: str) -> None:
    print(f"MJPROGRESS|{max(0, min(100, int(percent)))}|{_sanitize(message, 300)}", flush=True)


def _error(code: str, message: object) -> None:
    print(f"MJERROR|{_sanitize(code, 80)}|{_sanitize(message)}", flush=True)


def _artifact(path: Path) -> None:
    print(f"MJARTIFACT|{_sanitize(path, 1800)}", flush=True)


def _sha256(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _load_json_file(path: Path, *, max_bytes: int = 256 * 1024) -> dict[str, Any]:
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size > max_bytes:
            raise ValueError("invalid file size")
        value = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise EngineError("request-json", f"Could not read JSON input: {exc}") from exc
    if not isinstance(value, dict):
        raise EngineError("request-json", "JSON input must be an object")
    return value


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _crop_foreground(image: Any, *, ratio: float = 1.22) -> Any:
    from PIL import Image

    rgba = image.convert("RGBA")
    bbox = rgba.getchannel("A").getbbox()
    if bbox is None:
        raise EngineError("image-alpha", "The source image is fully transparent")
    cropped = rgba.crop(bbox)
    width, height = cropped.size
    side = max(64, int(round(max(width, height) * ratio)))
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    canvas.alpha_composite(cropped, ((side - width) // 2, (side - height) // 2))
    return canvas


def _prepare_image(image_path: Path, staging: Path) -> dict[str, Any]:
    from PIL import Image, ImageOps

    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    with Image.open(image_path) as opened:
        opened.load()
        source = ImageOps.exif_transpose(opened)
        width, height = source.size
        if width < 64 or height < 64 or width * height > MAX_IMAGE_PIXELS:
            raise EngineError("image-dimensions", "The source image dimensions are unsupported")
        useful_alpha = (
            "A" in source.getbands()
            and source.convert("RGBA").getchannel("A").getextrema()[0] < 255
        )
        image = source.convert("RGBA")
    removed = False
    if useful_alpha:
        _progress(10, "Using the image's transparent background")
    else:
        _progress(8, "Removing the background locally")
        if not BACKGROUND_CHECKPOINT.is_file() or not SPAR_VENDOR_ROOT.is_dir():
            raise EngineError("background-component", "The background-removal component is missing")
        if _sha256(BACKGROUND_CHECKPOINT) != BACKGROUND_SHA256:
            raise EngineError(
                "background-component",
                "The bundled background-removal checkpoint failed verification",
            )
        import torch

        _force_weights_only_torch_load(
            torch,
            {
                BACKGROUND_CHECKPOINT: (
                    int(BACKGROUND_CHECKPOINT.stat().st_size),
                    BACKGROUND_SHA256,
                )
            },
        )
        vendor = str(SPAR_VENDOR_ROOT)
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        from spar3d.utils import remove_background
        from transparent_background import Remover

        remover = Remover(mode="base", device="cpu", ckpt=str(BACKGROUND_CHECKPOINT))
        try:
            image = remove_background(image, remover).convert("RGBA")
            removed = True
        finally:
            del remover
            gc.collect()
    prepared = _crop_foreground(image)
    prepared_path = staging / "input_prepared.png"
    prepared.save(prepared_path, format="PNG", optimize=True)
    return {
        "original_width": int(width),
        "original_height": int(height),
        "had_useful_alpha": bool(useful_alpha),
        "background_removed": removed,
        "prepared_width": int(prepared.width),
        "prepared_height": int(prepared.height),
        "prepared_path": str(prepared_path),
    }


def _load_state(path: Path) -> dict[str, Any]:
    value = _load_json_file(path)
    staging_value = value.get("staging")
    if not isinstance(staging_value, str):
        raise EngineError("stage-state", "Stage state has no staging path")
    staging_lexical = Path(staging_value)
    if not staging_lexical.is_absolute() or _is_reparse_point(staging_lexical):
        raise EngineError("stage-state", "Stage state uses an unsafe staging path")
    staging = staging_lexical.resolve(strict=True)
    if (
        not staging.is_dir()
        or not staging.name.startswith(".mujassam-")
        or not staging.name.endswith(".partial")
        or path.resolve(strict=True).parent != staging
    ):
        raise EngineError("stage-state", "Stage state is outside its Mujassam staging folder")
    job = value.get("job")
    if not isinstance(job, dict):
        raise EngineError("stage-state", "Stage state has no validated job")
    validated_job = _validate_job(job)
    if staging.parent != Path(validated_job["output_dir"]):
        raise EngineError("stage-state", "Stage folder is outside the selected output folder")
    value["job"] = validated_job
    expected_artifacts = {
        "prepared_image": "input_prepared.png",
        "shape_mesh": "shape.ply",
        "paint_obj": "pbr_mesh.obj",
        "paint_base": "pbr_mesh.png",
        "paint_metallic": "pbr_mesh_metallic.png",
        "paint_roughness": "pbr_mesh_roughness.png",
        "final_glb": "model.glb",
    }
    for key, filename in expected_artifacts.items():
        raw = value.get(key)
        if not isinstance(raw, str):
            raise EngineError("stage-state", f"Stage state has no {key} path")
        candidate = Path(raw)
        lexical = Path(os.path.abspath(str(candidate)))
        if (
            not candidate.is_absolute()
            or lexical != staging / filename
            or (candidate.exists() and _is_reparse_point(candidate))
        ):
            raise EngineError("stage-state", f"Stage state has an unsafe {key} path")
    return value


def _seed_torch(torch: Any, seed: int = 42) -> Any:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    return torch.Generator(device="cuda").manual_seed(seed)


def _local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if not value:
        raise EngineError(
            "engine-state", "Windows LocalAppData could not be located"
        )
    return Path(value).expanduser().resolve()


def _engine_state_root() -> Path:
    base = _local_app_data()
    root = base
    for component in ("MujassamAI", "Engines", "Hunyuan3D-2.1"):
        root = root / component
        root.mkdir(exist_ok=True)
        if _is_reparse_point(root):
            raise EngineError(
                "engine-state", "The Hunyuan engine state cannot use a link or junction"
            )
    return root


def _shape_resume_identity_from_hash(
    prepared_image_sha256: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Describe every input that can change the completed Shape mesh."""

    if not re.fullmatch(r"[0-9a-f]{64}", prepared_image_sha256):
        raise EngineError("shape-resume", "Prepared-image hash is invalid")
    identity: dict[str, Any] = {
        "schema_version": SHAPE_RESUME_SCHEMA_VERSION,
        "pipeline_revision": SHAPE_RESUME_PIPELINE_REVISION,
        "engine_schema_version": ENGINE_SCHEMA_VERSION,
        "engine_mode": ENGINE_MODE,
        "source_commit": SOURCE_COMMIT,
        "model_repository": MODEL_REPOSITORY,
        "model_revision": MODEL_REVISION,
        "shape_subfolder": SHAPE_SUBFOLDER,
        "shape_vae_subfolder": SHAPE_VAE_SUBFOLDER,
        "python_runtime": ".".join(map(str, REQUIRED_PYTHON)),
        "torch_runtime": REQUIRED_TORCH,
        "cuda_runtime": REQUIRED_CUDA_RUNTIME,
        "prepared_image_sha256": prepared_image_sha256,
        "target": payload["target"],
        "geometry_mode": payload["geometry_mode"],
        "seed": 42,
        "inference_steps": SHAPE_INFERENCE_STEPS,
        "guidance_scale": SHAPE_GUIDANCE_SCALE,
        "octree_resolution": SHAPE_OCTREE_RESOLUTION,
        "num_chunks": SHAPE_NUM_CHUNKS,
        "target_ready_faces": ROBLOX_READY_FACES,
        "max_detail_faces": ROBLOX_MASTER_FACES,
    }
    encoded = json.dumps(
        identity, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "key": hashlib.sha256(encoded).hexdigest(),
        "identity": identity,
    }


def _shape_resume_identity(
    prepared_image: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    if _is_reparse_point(prepared_image) or not prepared_image.is_file():
        raise EngineError("shape-resume", "Prepared image is unsafe or missing")
    return _shape_resume_identity_from_hash(_sha256(prepared_image), payload)


def _shape_resume_root(*, create: bool) -> Path:
    state_root = _engine_state_root()
    root = state_root / "shape-resume"
    if _is_reparse_point(root):
        raise EngineError(
            "shape-resume", "The Shape resume checkpoint cannot use a link or junction"
        )
    if root.exists():
        if not root.is_dir():
            raise EngineError(
                "shape-resume", "The Shape resume checkpoint cannot use a link or junction"
            )
    elif create:
        root.mkdir(mode=0o700, exist_ok=True)
        if _is_reparse_point(root) or not root.is_dir():
            raise EngineError("shape-resume", "Could not create a safe Shape checkpoint")
    return root


def _remove_shape_resume_file(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if _is_reparse_point(path) or not path.is_file():
        raise EngineError("shape-resume", "The Shape checkpoint contains an unsafe entry")
    path.unlink()


def _invalidate_shape_resume(root: Path) -> None:
    """Invalidate the commit marker first, then remove only our bounded files."""

    for path in (root / "manifest.json", root / "shape.ply"):
        _remove_shape_resume_file(path)
    try:
        temporary_files = tuple(root.glob(".shape-resume-*.tmp"))
    except OSError as exc:
        raise EngineError("shape-resume", "Could not inspect the Shape checkpoint") from exc
    for path in temporary_files:
        _remove_shape_resume_file(path)


def _copy_shape_resume_payload(source: Path, temporary: Path) -> tuple[int, str]:
    """Copy and hash one regular mesh through the same already-open handles."""

    if _is_reparse_point(source) or not source.is_file():
        raise EngineError("shape-resume", "The Shape checkpoint mesh is unsafe or missing")
    digest = hashlib.sha256()
    total = 0
    try:
        with source.open("rb") as reader:
            source_stat = os.fstat(reader.fileno())
            if (
                not stat.S_ISREG(source_stat.st_mode)
                or source_stat.st_size < 100
                or source_stat.st_size > SHAPE_RESUME_MAX_BYTES
            ):
                raise EngineError(
                    "shape-resume", "The Shape checkpoint mesh has an invalid size"
                )
            with temporary.open("xb") as writer:
                for chunk in iter(lambda: reader.read(4 * 1024 * 1024), b""):
                    total += len(chunk)
                    if total > SHAPE_RESUME_MAX_BYTES:
                        raise EngineError(
                            "shape-resume", "The Shape checkpoint exceeded its 2 GiB limit"
                        )
                    digest.update(chunk)
                    writer.write(chunk)
                writer.flush()
                os.fsync(writer.fileno())
        if total != source_stat.st_size:
            raise EngineError("shape-resume", "The Shape checkpoint changed while copying")
        return total, digest.hexdigest()
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _read_shape_resume_manifest(root: Path) -> dict[str, Any]:
    path = root / "manifest.json"
    if _is_reparse_point(path) or not path.is_file():
        raise ValueError("manifest is missing or unsafe")
    size = path.stat().st_size
    if size < 32 or size > 64 * 1024:
        raise ValueError("manifest size is invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("manifest is not an object")
    return value


def _save_shape_resume(
    identity_record: dict[str, Any], shape_mesh: Path
) -> bool:
    """Commit the one allowed Shape checkpoint; manifest replacement is last."""

    root = _shape_resume_root(create=True)
    _invalidate_shape_resume(root)
    token = uuid.uuid4().hex
    shape_temporary = root / f".shape-resume-{token}.tmp"
    manifest_temporary = root / f".shape-resume-{token}-manifest.tmp"
    try:
        size, digest = _copy_shape_resume_payload(shape_mesh, shape_temporary)
        manifest = {
            "schema_version": SHAPE_RESUME_SCHEMA_VERSION,
            "key": identity_record["key"],
            "identity": identity_record["identity"],
            "shape": {
                "name": "shape.ply",
                "size": size,
                "sha256": digest,
            },
            "saved_utc": datetime.now(timezone.utc).isoformat(),
        }
        with manifest_temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(manifest, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(shape_temporary, root / "shape.ply")
        # The manifest is the commit marker.  A crash before this replacement
        # leaves no valid pair; a crash after it leaves the fully hashed mesh.
        os.replace(manifest_temporary, root / "manifest.json")
        return True
    finally:
        shape_temporary.unlink(missing_ok=True)
        manifest_temporary.unlink(missing_ok=True)


def _restore_shape_resume(
    identity_record: dict[str, Any], destination: Path
) -> bool:
    """Restore only a same-identity mesh whose size and SHA-256 still match."""

    root = _shape_resume_root(create=False)
    if not root.exists():
        return False
    try:
        manifest = _read_shape_resume_manifest(root)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        _invalidate_shape_resume(root)
        return False
    if (
        manifest.get("schema_version") != SHAPE_RESUME_SCHEMA_VERSION
        or manifest.get("key") != identity_record.get("key")
        or manifest.get("identity") != identity_record.get("identity")
    ):
        return False
    shape_record = manifest.get("shape")
    if not isinstance(shape_record, dict) or shape_record.get("name") != "shape.ply":
        _invalidate_shape_resume(root)
        return False
    expected_size = shape_record.get("size")
    expected_sha256 = shape_record.get("sha256")
    if (
        not isinstance(expected_size, int)
        or expected_size < 100
        or expected_size > SHAPE_RESUME_MAX_BYTES
        or not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
    ):
        _invalidate_shape_resume(root)
        return False
    if _is_reparse_point(destination.parent) or not destination.parent.is_dir():
        raise EngineError("shape-resume", "The Shape restore destination is unsafe")
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        actual_size, actual_sha256 = _copy_shape_resume_payload(
            root / "shape.ply", temporary
        )
        if actual_size != expected_size or actual_sha256 != expected_sha256:
            temporary.unlink(missing_ok=True)
            _invalidate_shape_resume(root)
            return False
        os.replace(temporary, destination)
        return True
    except (OSError, EngineError):
        temporary.unlink(missing_ok=True)
        _invalidate_shape_resume(root)
        return False


def _delete_shape_resume(identity_record: dict[str, Any]) -> None:
    """Delete this job's committed checkpoint without deleting a newer job's."""

    try:
        root = _shape_resume_root(create=False)
        if not root.exists():
            return
        manifest = _read_shape_resume_manifest(root)
        if manifest.get("key") != identity_record.get("key"):
            return
        _invalidate_shape_resume(root)
        try:
            root.rmdir()
        except OSError:
            pass
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, EngineError):
        # Publishing already succeeded.  A cleanup problem must not turn a
        # valid final asset into a reported failure, and unsafe links are never followed.
        return


def _validate_license_acceptance() -> Path:
    """Enforce the 2.1 license/territory gate independently of the GUI."""

    path = _local_app_data() / "MujassamAI" / "Licenses" / "acceptance-v2-1.txt"
    try:
        if path.is_symlink() or not path.is_file():
            raise ValueError("acceptance record is missing")
        size = path.stat().st_size
        if size < 128 or size > 16_384:
            raise ValueError("acceptance record has an invalid size")
        acceptance_lines = path.read_text(encoding="utf-8-sig").splitlines()
        lines = set(acceptance_lines)
    except (OSError, UnicodeError, ValueError) as exc:
        raise EngineError(
            "license-not-accepted",
            "Hunyuan3D 2.1 cannot start until its license and territory "
            "confirmation are accepted in Mujassam AI",
        ) from exc

    required = {
        "MujassamAI Hunyuan acceptance v2.1",
        "acceptance_version=hunyuan3d-2.1-v1",
        f"source_commit={SOURCE_COMMIT}",
        "license=Tencent Hunyuan 3D 2.1 Community License Agreement",
        f"license_sha256={LICENSE_SHA256}",
        "territory_confirmation=outside EU, UK, and South Korea",
        "license_terms_acknowledged=true",
        "acceptable_use_policy_acknowledged=true",
        "tencent_non_affiliation_acknowledged=true",
    }
    scope_lines = [
        line for line in acceptance_lines if line.startswith("usage_scope=")
    ]
    usage_scope = (
        scope_lines[0].partition("=")[2].strip() if len(scope_lines) == 1 else ""
    )
    provider_lines = [
        line for line in acceptance_lines if line.startswith("provider_legal_name=")
    ]
    provider = (
        provider_lines[0].partition("=")[2].strip()
        if len(provider_lines) == 1
        else ""
    )
    provider_disclosure_lines = [
        line
        for line in acceptance_lines
        if line.startswith("provider_disclosure_acknowledged=")
    ]
    distribution_lines = [
        line
        for line in acceptance_lines
        if line.startswith("distribution_authorized=")
    ]
    notice_path = ENGINE_ROOT / "NOTICE.txt"
    try:
        notice_text = notice_path.read_text(encoding="utf-8-sig")
        notice_text_lines = notice_text.splitlines()
        scope_prefix = "Configured usage scope: "
        notice_scope_lines = [
            line for line in notice_text_lines if line.startswith(scope_prefix)
        ]
        notice_scope = (
            notice_scope_lines[0][len(scope_prefix) :].strip()
            if len(notice_scope_lines) == 1
            else ""
        )
        provider_prefix = "Actual provider of this integration: "
        notice_provider_lines = [
            line for line in notice_text_lines if line.startswith(provider_prefix)
        ]
        notice_provider = (
            notice_provider_lines[0][len(provider_prefix) :].strip()
            if len(notice_provider_lines) == 1
            else ""
        )
    except (OSError, UnicodeError):
        notice_scope = ""
        notice_provider = ""
    accepted_lines = [
        line for line in acceptance_lines if line.startswith("accepted_utc=")
    ]
    accepted_utc = (
        accepted_lines[0].partition("=")[2].strip()
        if len(accepted_lines) == 1
        else ""
    )
    try:
        parsed_accepted = datetime.fromisoformat(accepted_utc.replace("Z", "+00:00"))
        timestamp_valid = (
            parsed_accepted.tzinfo is not None
            and parsed_accepted.astimezone(timezone.utc)
            <= datetime.now(timezone.utc) + timedelta(minutes=5)
        )
    except ValueError:
        timestamp_valid = False
    common_valid = (
        required.issubset(lines)
        and usage_scope == notice_scope
        and usage_scope in {PERSONAL_LOCAL_USAGE_SCOPE, THIRD_PARTY_USAGE_SCOPE}
        and timestamp_valid
    )
    if usage_scope == PERSONAL_LOCAL_USAGE_SCOPE:
        scope_valid = (
            not provider_lines
            and not provider_disclosure_lines
            and distribution_lines == ["distribution_authorized=false"]
        )
    else:
        scope_valid = (
            usage_scope == THIRD_PARTY_USAGE_SCOPE
            and provider_disclosure_lines == ["provider_disclosure_acknowledged=true"]
            and len(provider_lines) == 1
            and bool(provider)
            and provider == notice_provider
            and not distribution_lines
            and "@@" not in notice_provider
            and notice_provider != CI_PROVIDER_SENTINEL
        )
    if not common_valid or not scope_valid:
        raise EngineError(
            "license-not-accepted",
            "The saved Hunyuan3D 2.1 acceptance is missing the current license, "
            "territory, usage-scope, or provider confirmation; accept it again "
            "in Mujassam AI",
        )
    return path


def _activate_engine_paths() -> None:
    ordered = (
        APP_ROOT,
        PYTHON_PACKAGES,
        SHAPE_VENDOR_ROOT,
        PAINT_VENDOR_ROOT,
        VENDOR_ROOT,
    )
    for path in reversed(ordered):
        value = str(path)
        sys.path[:] = [entry for entry in sys.path if entry != value]
        sys.path.insert(0, value)


def _verify_file(path: Path, expected_size: int, expected_sha256: str) -> bool:
    try:
        return (
            not path.is_symlink()
            and path.is_file()
            and path.stat().st_size == expected_size
            and _sha256(path) == expected_sha256
        )
    except OSError:
        return False


def _validate_engine_pack() -> None:
    required = (
        ENGINE_ROOT / "ENGINE-MANIFEST.json",
        ENGINE_ROOT / "NOTICE.txt",
        ENGINE_ROOT / "LICENSE-HUNYUAN3D-2.1.txt",
        ENGINE_ROOT / "NOTICE-HUNYUAN3D-2.1.txt",
        SHAPE_VENDOR_ROOT / "hy3dshape" / "pipelines.py",
        PAINT_VENDOR_ROOT / "textureGenPipeline.py",
        PAINT_VENDOR_ROOT / "utils" / "multiview_utils.py",
        PAINT_VENDOR_ROOT / "utils" / "torchvision_fix.py",
        PAINT_VENDOR_ROOT / "DifferentiableRenderer" / "mesh_utils.py",
        PAINT_VENDOR_ROOT / "cfgs" / "hunyuan-paint-pbr.yaml",
        TRUSTED_PAINT_UNET_ROOT / "attn_processor.py",
        TRUSTED_PAINT_UNET_ROOT / "model.py",
        TRUSTED_PAINT_UNET_ROOT / "modules.py",
        BASE_WORKER_PATH,
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing or not PYTHON_PACKAGES.is_dir():
        detail = ", ".join(missing) if missing else str(PYTHON_PACKAGES)
        raise EngineError(
            "engine-component-missing",
            "The Hunyuan3D 2.1 engine update is incomplete. Missing: " + detail,
        )
    manifest = _load_json_file(ENGINE_ROOT / "ENGINE-MANIFEST.json")
    source = manifest.get("source") if isinstance(manifest.get("source"), dict) else {}
    models = manifest.get("models") if isinstance(manifest.get("models"), dict) else {}
    runtime_abi = (
        manifest.get("runtime_abi")
        if isinstance(manifest.get("runtime_abi"), dict)
        else {}
    )
    model_record = (
        models.get("hunyuan3d_2_1")
        if isinstance(models.get("hunyuan3d_2_1"), dict)
        else {}
    )
    dino_record = (
        models.get("dinov2_giant")
        if isinstance(models.get("dinov2_giant"), dict)
        else {}
    )
    if (
        source.get("commit") != SOURCE_COMMIT
        or model_record.get("revision") != MODEL_REVISION
        or dino_record.get("revision") != DINO_REVISION
        or runtime_abi.get("python") != ".".join(map(str, REQUIRED_PYTHON))
        or runtime_abi.get("pytorch") != REQUIRED_TORCH
        or runtime_abi.get("torchvision") != REQUIRED_TORCHVISION
        or runtime_abi.get("cuda_runtime") != REQUIRED_CUDA_RUNTIME
        or runtime_abi.get("platform") != "win_amd64"
    ):
        raise EngineError(
            "engine-component-invalid",
            "The Hunyuan3D 2.1 engine manifest does not match the pinned source/models",
        )
    if (
        _sha256(ENGINE_ROOT / "LICENSE-HUNYUAN3D-2.1.txt") != LICENSE_SHA256
        or _sha256(ENGINE_ROOT / "NOTICE-HUNYUAN3D-2.1.txt")
        != UPSTREAM_NOTICE_SHA256
    ):
        raise EngineError(
            "engine-component-invalid",
            "The bundled Hunyuan3D 2.1 license or upstream notice was modified",
        )
    if not _verify_file(REALESRGAN_X4, REALESRGAN_X4_BYTES, REALESRGAN_X4_SHA256):
        raise EngineError(
            "engine-component-invalid",
            "The bundled RealESRGAN_x4plus Paint checkpoint is missing or invalid",
        )
    try:
        texture_source = (PAINT_VENDOR_ROOT / "textureGenPipeline.py").read_text(
            encoding="utf-8-sig"
        )
        multiview_source = (
            PAINT_VENDOR_ROOT / "utils" / "multiview_utils.py"
        ).read_text(encoding="utf-8-sig")
        mesh_source = (
            PAINT_VENDOR_ROOT / "DifferentiableRenderer" / "mesh_utils.py"
        ).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise EngineError("engine-component-invalid", "Could not audit the Paint patches") from exc
    if (
        'len(enhance_images["albedo"])' not in texture_source
        or "self.render.save_mesh(output_mesh_path, downsample=False)" not in texture_source
        or "local_files_only=True" not in multiview_source
        or "huggingface_hub.snapshot_download" in multiview_source
        or 'image_format: str = ".png"' not in mesh_source
    ):
        raise EngineError(
            "engine-component-invalid",
            "The reviewed local-only/native-4K Hunyuan Paint patches are missing",
        )


def _validate_base_runtime(texture_mode: str) -> None:
    """Fail before the large model download on an incompatible base install."""

    if sys.version_info[:3] != REQUIRED_PYTHON or struct.calcsize("P") != 8:
        raise EngineError(
            "runtime-abi",
            "Hunyuan3D 2.1 requires the Mujassam Python 3.11.9 x64 runtime; "
            "install the current portable base before this engine update",
        )
    _activate_engine_paths()
    try:
        from importlib.metadata import version as distribution_version

        import torch
        torchvision_version = distribution_version("torchvision")
    except Exception as exc:
        raise EngineError(
            "runtime-abi",
            f"The pinned CUDA runtime could not be imported: {_sanitize(exc, 400)}",
        ) from exc
    if (
        str(torch.__version__) != REQUIRED_TORCH
        or torchvision_version != REQUIRED_TORCHVISION
        or str(torch.version.cuda) != REQUIRED_CUDA_RUNTIME
    ):
        raise EngineError(
            "runtime-abi",
            "Hunyuan3D 2.1 requires exact torch 2.5.1+cu124, torchvision "
            "0.20.1+cu124, and CUDA runtime 12.4 from the current portable base",
        )
    if not torch.cuda.is_available():
        raise EngineError(
            "cuda-required",
            "Hunyuan3D 2.1 requires a working NVIDIA CUDA device; no models were downloaded",
        )

    try:
        base = _load_base_worker()
        bundle = base._validate_bundle()
    except Exception as exc:
        raise EngineError(
            "base-runtime",
            "The portable SPAR/background base is incomplete; reinstall the current "
            f"portable base before Hunyuan3D 2.1 ({_sanitize(exc, 300)})",
        ) from exc
    if Path(bundle.background_checkpoint).resolve() != BACKGROUND_CHECKPOINT.resolve():
        raise EngineError("base-runtime", "The background checkpoint path is invalid")
    try:
        background_valid = (
            not BACKGROUND_CHECKPOINT.is_symlink()
            and BACKGROUND_CHECKPOINT.is_file()
            and _sha256(BACKGROUND_CHECKPOINT) == BACKGROUND_SHA256
        )
    except OSError:
        background_valid = False
    if not background_valid:
        raise EngineError(
            "base-runtime",
            "The portable background-removal checkpoint failed exact verification",
        )

    if texture_mode == "export_8k":
        quality_files = (
            APP_ROOT / "quality" / "__init__.py",
            APP_ROOT / "quality" / "realesrgan_x2.py",
            APP_ROOT / "quality" / "models.json",
        )
        if any(path.is_symlink() or not path.is_file() for path in quality_files):
            raise EngineError(
                "texture-ai-runtime",
                "Ultimate 8K requires the current bundled texture-quality module",
            )
        try:
            quality = base._import_bundled_quality()
            specification = quality.REALESRGAN_X2PLUS
            specification_valid = (
                specification.byte_count == REALESRGAN_X2_BYTES
                and specification.sha256 == REALESRGAN_X2_SHA256
                and specification.file_name == REALESRGAN_X2.name
                and specification.scale == 2
            )
        except Exception as exc:
            raise EngineError(
                "texture-ai-runtime",
                f"The bundled 8K texture-quality module is invalid: {_sanitize(exc, 300)}",
            ) from exc
        if not specification_valid or not _verify_file(
            REALESRGAN_X2, REALESRGAN_X2_BYTES, REALESRGAN_X2_SHA256
        ):
            raise EngineError(
                "texture-ai-runtime",
                "Ultimate 8K requires the exact bundled RealESRGAN_x2plus checkpoint",
            )


def _rasterizer_cuda_preflight() -> None:
    """Execute Tencent's real CUDA entry point before the expensive Shape stage."""

    _activate_engine_paths()
    try:
        import torch
        import custom_rasterizer_kernel

        vertices = torch.tensor(
            (
                (-0.5, -0.5, 0.0, 1.0),
                (0.5, -0.5, 0.0, 1.0),
                (0.0, 0.5, 0.0, 1.0),
            ),
            dtype=torch.float32,
            device="cuda",
        ).contiguous()
        faces = torch.tensor(
            ((0, 1, 2),), dtype=torch.int32, device="cuda"
        ).contiguous()
        depth = torch.empty((0,), dtype=torch.float32, device="cuda")
        result = custom_rasterizer_kernel.rasterize_image(
            vertices, faces, depth, 8, 8, 1e-6, 0
        )
        torch.cuda.synchronize()
        if (
            not isinstance(result, (tuple, list))
            or len(result) != 2
            or tuple(result[0].shape) != (8, 8)
            or tuple(result[1].shape) != (8, 8, 3)
            or result[0].dtype != torch.int32
            or result[1].dtype != torch.float32
            or not result[0].is_cuda
            or not result[1].is_cuda
        ):
            raise RuntimeError("the rasterizer returned an invalid tensor contract")
    except Exception as exc:
        raise EngineError(
            "paint-rasterizer-runtime",
            "Hunyuan Paint CUDA rasterizer self-check failed before Shape: "
            f"{_sanitize(exc, 500)}",
        ) from exc
    finally:
        try:
            del result, vertices, faces, depth
        except UnboundLocalError:
            pass


def _validate_job(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 3:
        raise EngineError("schema-version", "Hunyuan3D 2.1 requires job schema version 3")
    if payload.get("engine_mode") != ENGINE_MODE:
        raise EngineError("engine-mode", "This worker supports Hunyuan3D 2.1 PBR only")
    if payload.get("target") not in {"roblox", "unreal"}:
        raise EngineError("request-schema", "target must be roblox or unreal")
    if payload.get("texture_mode") not in {"native_2k", "ai_4k", "export_8k"}:
        raise EngineError("request-schema", "Unsupported texture mode")
    if payload.get("geometry_mode") not in {"target_ready", "max_detail", "original"}:
        raise EngineError("request-schema", "Unsupported geometry mode")
    if payload.get("hardware_preset") not in {"auto", "vram_8gb", "vram_16gb_plus"}:
        raise EngineError("request-schema", "Unsupported hardware preset")

    image_value, output_value = payload.get("image_path"), payload.get("output_dir")
    if not isinstance(image_value, str) or not isinstance(output_value, str):
        raise EngineError("request-schema", "image_path and output_dir are required")
    try:
        image_path = Path(image_value).expanduser().resolve(strict=True)
    except OSError as exc:
        raise EngineError("image-not-found", "The source image was not found") from exc
    if not image_path.is_file() or image_path.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise EngineError("image-format", "Use a PNG, JPEG, or WebP source image")
    output_dir = Path(output_value).expanduser()
    if not output_dir.is_absolute():
        raise EngineError("output-path", "output_dir must be an absolute path")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir = output_dir.resolve(strict=True)
    if output_dir == Path(output_dir.anchor) or _is_within(output_dir, BUNDLE_ROOT):
        raise EngineError("output-path", "Choose an output folder outside the program")
    result = dict(payload)
    result["image_path"] = str(image_path)
    result["output_dir"] = str(output_dir)
    return result


def _snapshot_ready(root: Path, required: tuple[str, ...]) -> bool:
    try:
        if _tree_has_reparse_point(root):
            return False
        return all(
            not (root / relative).is_symlink()
            and (root / relative).is_file()
            and (root / relative).stat().st_size > 0
            for relative in required
        )
    except OSError:
        return False


def _weights_ready(root: Path, *, verify_hashes: bool) -> bool:
    for relative, (size, digest) in PINNED_MODEL_WEIGHTS.items():
        path = root / relative
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
                return False
            if verify_hashes and _sha256(path) != digest:
                path.unlink(missing_ok=True)
                return False
        except OSError:
            return False
    return True


def _dino_ready(root: Path, *, verify_hash: bool) -> bool:
    if not _snapshot_ready(root, DINO_REQUIRED_FILES):
        return False
    size, digest = DINO_WEIGHT
    path = root / "model.safetensors"
    try:
        if path.stat().st_size != size:
            return False
        if verify_hash and _sha256(path) != digest:
            path.unlink(missing_ok=True)
            return False
    except OSError:
        return False
    return True


def _install_trusted_paint_modules(model_root: Path) -> None:
    destination_root = model_root / PAINT_SUBFOLDER / "unet"
    destination_root.mkdir(parents=True, exist_ok=True)
    for name in ("attn_processor.py", "model.py", "modules.py"):
        source = TRUSTED_PAINT_UNET_ROOT / name
        if not source.is_file() or source.is_symlink():
            raise EngineError(
                "engine-component-missing", f"Reviewed Paint module is missing: {name}"
            )
        payload = source.read_bytes()
        destination = destination_root / name
        if destination.is_file() and _sha256(destination) == hashlib.sha256(payload).hexdigest():
            continue
        temporary = destination.with_name(f".{name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(payload)
        os.replace(temporary, destination)


def _trusted_modules_ready(model_root: Path) -> bool:
    destination_root = model_root / PAINT_SUBFOLDER / "unet"
    try:
        modules_ready = all(
            (destination_root / name).is_file()
            and not (destination_root / name).is_symlink()
            and _sha256(destination_root / name) == _sha256(TRUSTED_PAINT_UNET_ROOT / name)
            for name in ("attn_processor.py", "model.py", "modules.py")
        )
        allowed_python = {
            f"{PAINT_SUBFOLDER}/unet/attn_processor.py",
            f"{PAINT_SUBFOLDER}/unet/model.py",
            f"{PAINT_SUBFOLDER}/unet/modules.py",
        }
        actual_python = {
            path.relative_to(model_root).as_posix()
            for path in model_root.rglob("*.py")
            if path.is_file()
        }
        index_path = model_root / PAINT_SUBFOLDER / "model_index.json"
        if not _verify_file(
            index_path, PAINT_MODEL_INDEX_BYTES, PAINT_MODEL_INDEX_SHA256
        ):
            return False
        index = _load_json_file(index_path)
        expected_index = {
            "_class_name": "HunyuanPaintPipeline",
            "feature_extractor": ["transformers", "CLIPImageProcessor"],
            "safety_checker": [None, None],
            "scheduler": ["diffusers", "DDIMScheduler"],
            "text_encoder": ["transformers", "CLIPTextModel"],
            "tokenizer": ["transformers", "CLIPTokenizer"],
            "unet": ["modules", "UNet2p5DConditionModel"],
            "vae": ["diffusers", "AutoencoderKL"],
            "image_encoder": ["transformers", "CLIPVisionModelWithProjection"],
            "requires_safety_checker": False,
        }
        return (
            modules_ready
            and actual_python == allowed_python
            and all(index.get(key) == value for key, value in expected_index.items())
        )
    except (OSError, EngineError):
        return False


def _download_patterns(repo_id: str) -> tuple[list[str], list[str]]:
    if repo_id == MODEL_REPOSITORY:
        return (
            list(MODEL_REQUIRED_FILES) + ["LICENSE", "NOTICE", "README.md"],
            ["*.pt", "*.pth", "*.pkl", "*.safetensors.index.json"],
        )
    if repo_id == DINO_REPOSITORY:
        return (
            list(DINO_REQUIRED_FILES) + ["LICENSE", "README.md"],
            ["pytorch_model.bin", "*.msgpack", "*.h5"],
        )
    raise EngineError("model-download", "Refusing an unpinned model repository")


def _download_snapshot_once(repo_id: str, revision: str, local_dir: Path) -> None:
    state_root = _engine_state_root()
    expected = {
        MODEL_REPOSITORY: (MODEL_REVISION, state_root / "models" / "Hunyuan3D-2.1"),
        DINO_REPOSITORY: (DINO_REVISION, state_root / "models" / "dinov2-giant"),
    }.get(repo_id)
    allowed_destinations: set[Path] = set()
    if expected is not None:
        final = expected[1].resolve()
        allowed_destinations = {final, final.with_name(final.name + ".partial")}
    if expected is None or revision != expected[0] or local_dir.resolve() not in allowed_destinations:
        raise EngineError("model-download", "Refusing an unpinned model revision or destination")
    _validate_cache_path(local_dir)
    _validate_engine_pack()
    _validate_license_acceptance()
    # This CLI mode is normally an isolated orchestrator child, but it is also
    # callable directly.  Fail before network access on an obsolete base ABI.
    _validate_base_runtime("ai_4k")
    os.environ.update(
        {
            "HF_HUB_DOWNLOAD_TIMEOUT": "60",
            "HF_HUB_ETAG_TIMEOUT": "30",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_ENDPOINT": "https://huggingface.co",
            "HF_HUB_ENABLE_HF_TRANSFER": "0",
            "HF_HUB_DISABLE_XET": "1",
            "DO_NOT_TRACK": "1",
        }
    )
    for name in (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HF_INFERENCE_ENDPOINT",
        "HUGGINGFACE_CO_RESOLVE_ENDPOINT",
    ):
        os.environ.pop(name, None)
    _activate_engine_paths()
    from huggingface_hub import snapshot_download

    allow, ignore = _download_patterns(repo_id)
    local_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        revision=revision,
        local_dir=str(local_dir),
        allow_patterns=allow,
        ignore_patterns=ignore,
        max_workers=1,
    )


def _download_signature(root: Path) -> tuple[int, int, int]:
    files = total = newest = 0
    try:
        candidates = root.rglob("*") if root.exists() else ()
        for candidate in candidates:
            try:
                if not candidate.is_file():
                    continue
                stat = candidate.stat()
                files += 1
                total += stat.st_size
                newest = max(newest, stat.st_mtime_ns)
            except OSError:
                continue
    except OSError:
        pass
    return files, total, newest


def _stop_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass


def _run_download_attempt(
    *, repo_id: str, revision: str, local_dir: Path, progress_percent: int
) -> None:
    command = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(Path(__file__).resolve()),
        "--download-repository",
        repo_id,
        "--download-revision",
        revision,
        "--download-dir",
        str(local_dir),
    ]
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "HF_HUB_DOWNLOAD_TIMEOUT": "60",
            "HF_HUB_ETAG_TIMEOUT": "30",
            "HF_HUB_DISABLE_PROGRESS_BARS": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "HF_ENDPOINT": "https://huggingface.co",
            "HF_HUB_ENABLE_HF_TRANSFER": "0",
            "HF_HUB_DISABLE_XET": "1",
            "DO_NOT_TRACK": "1",
        }
    )
    for name in (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HF_HOME",
        "HF_HUB_CACHE",
        "HUGGINGFACE_HUB_CACHE",
        "HF_INFERENCE_ENDPOINT",
        "HUGGINGFACE_CO_RESOLVE_ENDPOINT",
    ):
        environment.pop(name, None)
    process = subprocess.Popen(
        command,
        cwd=str(BUNDLE_ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_tail: deque[str] = deque(maxlen=80)
    stderr_tail: deque[str] = deque(maxlen=160)

    def drain(stream: Any, target: deque[str]) -> None:
        for line in iter(stream.readline, ""):
            cleaned = _sanitize(line, 1200)
            if cleaned:
                target.append(cleaned)

    stdout_thread = threading.Thread(
        target=drain, args=(process.stdout, stdout_tail), daemon=True
    )
    stderr_thread = threading.Thread(
        target=drain, args=(process.stderr, stderr_tail), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    signature = _download_signature(local_dir)
    last_change = last_status = time.monotonic()
    try:
        while process.poll() is None:
            time.sleep(DOWNLOAD_POLL_SECONDS)
            now = time.monotonic()
            current = _download_signature(local_dir)
            if current != signature:
                signature, last_change = current, now
            if now - last_status >= DOWNLOAD_STATUS_SECONDS:
                _progress(
                    progress_percent,
                    f"Pinned model download active; {current[1] / float(1024**3):.2f} GiB cached",
                )
                last_status = now
            if now - last_change >= DOWNLOAD_STALL_SECONDS:
                _stop_process(process)
                raise TimeoutError(
                    f"No downloaded byte changed for {DOWNLOAD_STALL_SECONDS} seconds"
                )
        process.wait()
    finally:
        if process.poll() is None:
            _stop_process(process)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
    if process.returncode != 0:
        tail = list(stderr_tail) or list(stdout_tail)
        detail = " / ".join(tail[-12:]) if tail else f"exit code {process.returncode}"
        raise RuntimeError(_sanitize(detail))


def _download_snapshot_resumable(
    *, repo_id: str, revision: str, local_dir: Path, progress_percent: int
) -> None:
    last_error: Exception | None = None
    for attempt in range(1, MAX_DOWNLOAD_ATTEMPTS + 1):
        try:
            _run_download_attempt(
                repo_id=repo_id,
                revision=revision,
                local_dir=local_dir,
                progress_percent=progress_percent,
            )
            return
        except Exception as exc:
            last_error = exc
            if attempt >= MAX_DOWNLOAD_ATTEMPTS:
                break
            _progress(
                progress_percent,
                f"Download interrupted; resuming automatically ({attempt}/{MAX_DOWNLOAD_ATTEMPTS})",
            )
            time.sleep(min(15, attempt * 2))
    raise EngineError(
        "model-download",
        "The official pinned model download was interrupted after automatic "
        f"retries: {last_error}",
    )


def _remove_invalid_weight(path: Path, expected_size: int) -> None:
    try:
        if path.is_symlink() or (path.is_file() and path.stat().st_size != expected_size):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _is_reparse_point(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or os.path.islink(path) or bool(attributes & reparse_flag)


def _tree_has_reparse_point(root: Path) -> bool:
    if not root.exists():
        return False
    if _is_reparse_point(root):
        return True
    try:
        for directory, names, files in os.walk(root, followlinks=False):
            parent = Path(directory)
            for name in (*names, *files):
                if _is_reparse_point(parent / name):
                    return True
    except OSError:
        return True
    return False


def _validate_cache_path(path: Path) -> None:
    """Reject symlink/junction redirection of the verified model cache."""

    models_root = _engine_state_root() / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(models_root):
        raise EngineError("model-path", "The Hunyuan model cache cannot be a link or junction")
    lexical_parent = Path(os.path.abspath(str(path.parent)))
    lexical_models = Path(os.path.abspath(str(models_root)))
    allowed_names = {
        "Hunyuan3D-2.1",
        "Hunyuan3D-2.1.partial",
        "dinov2-giant",
        "dinov2-giant.partial",
    }
    if lexical_parent != lexical_models or path.name not in allowed_names:
        raise EngineError("model-path", "Unexpected Hunyuan model cache path")
    if path.exists() and _is_reparse_point(path):
        raise EngineError("model-path", "The Hunyuan model cache cannot be a link or junction")


def _download_candidate(final_root: Path) -> Path:
    """Return a persistent sibling staging directory used for safe resume."""

    partial = final_root.with_name(final_root.name + ".partial")
    _validate_cache_path(final_root)
    _validate_cache_path(partial)
    if final_root.exists() and not partial.exists():
        try:
            os.replace(final_root, partial)
        except OSError as exc:
            raise EngineError(
                "model-files", "Could not stage the incomplete model cache"
            ) from exc
    partial.mkdir(parents=True, exist_ok=True)
    if _tree_has_reparse_point(partial):
        raise EngineError(
            "model-path", "The incomplete model cache contains a link or junction"
        )
    return partial


def _promote_verified(candidate: Path, final_root: Path) -> None:
    """Atomically promote a fully verified sibling model directory."""

    _validate_cache_path(candidate)
    _validate_cache_path(final_root)
    if candidate.resolve() == final_root.resolve():
        return
    quarantine: Path | None = None
    try:
        if final_root.exists():
            quarantine = final_root.with_name(
                f".{final_root.name}.invalid-{uuid.uuid4().hex}"
            )
            os.replace(final_root, quarantine)
        os.replace(candidate, final_root)
    except OSError as exc:
        if quarantine is not None and quarantine.exists() and not final_root.exists():
            try:
                os.replace(quarantine, final_root)
            except OSError:
                pass
        raise EngineError("model-files", "Could not promote the verified model cache") from exc
    finally:
        if quarantine is not None and quarantine.exists():
            shutil.rmtree(quarantine, ignore_errors=True)


def _download_models(state_root: Path) -> tuple[Path, Path, bool]:
    _progress(12, "Verifying exact pinned model bytes in the local resumable cache")
    models_root = state_root / "models"
    models_root.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(models_root):
        raise EngineError("model-path", "The Hunyuan model cache cannot be a link or junction")
    model_final = models_root / "Hunyuan3D-2.1"
    dino_final = models_root / "dinov2-giant"
    if _snapshot_ready(model_final, MODEL_REQUIRED_FILES) and _weights_ready(
        model_final, verify_hashes=True
    ):
        _install_trusted_paint_modules(model_final)
    model_ready = (
        _snapshot_ready(model_final, MODEL_REQUIRED_FILES)
        and _weights_ready(model_final, verify_hashes=True)
        and _trusted_modules_ready(model_final)
    )
    dino_ready = _dino_ready(dino_final, verify_hash=True)
    if not model_ready or not dino_ready:
        if shutil.disk_usage(state_root).free < MIN_FREE_DOWNLOAD_BYTES:
            raise EngineError(
                "disk-space",
                "Hunyuan3D 2.1 needs at least 32 GB of free disk space for its "
                "one-time pinned models",
            )

    downloaded = False
    if not model_ready:
        model_root = _download_candidate(model_final)
        if _snapshot_ready(model_root, MODEL_REQUIRED_FILES) and _weights_ready(
            model_root, verify_hashes=True
        ):
            _install_trusted_paint_modules(model_root)
        _progress(13, "Downloading official Hunyuan3D 2.1 models once (resumable)")
        model_root.mkdir(parents=True, exist_ok=True)
        candidate_ready = (
            _snapshot_ready(model_root, MODEL_REQUIRED_FILES)
            and _weights_ready(model_root, verify_hashes=True)
            and _trusted_modules_ready(model_root)
        )
        if not candidate_ready:
            for relative, (size, _digest) in PINNED_MODEL_WEIGHTS.items():
                _remove_invalid_weight(model_root / relative, size)
            _download_snapshot_resumable(
                repo_id=MODEL_REPOSITORY,
                revision=MODEL_REVISION,
                local_dir=model_root,
                progress_percent=13,
            )
            downloaded = True
        if _snapshot_ready(model_root, MODEL_REQUIRED_FILES):
            _install_trusted_paint_modules(model_root)
        if (
            not _snapshot_ready(model_root, MODEL_REQUIRED_FILES)
            or not _weights_ready(model_root, verify_hashes=True)
            or not _trusted_modules_ready(model_root)
        ):
            raise EngineError("model-download", "Hunyuan3D snapshot verification failed")
        _promote_verified(model_root, model_final)
    if not dino_ready:
        dino_root = _download_candidate(dino_final)
        _progress(18, "Downloading pinned DINOv2-giant once (resumable)")
        dino_root.mkdir(parents=True, exist_ok=True)
        if not _dino_ready(dino_root, verify_hash=True):
            _remove_invalid_weight(dino_root / "model.safetensors", DINO_WEIGHT[0])
            _download_snapshot_resumable(
                repo_id=DINO_REPOSITORY,
                revision=DINO_REVISION,
                local_dir=dino_root,
                progress_percent=18,
            )
            downloaded = True
        if not _dino_ready(dino_root, verify_hash=True):
            raise EngineError("model-download", "DINOv2 snapshot verification failed")
        _promote_verified(dino_root, dino_final)

    if _snapshot_ready(model_final, MODEL_REQUIRED_FILES):
        _install_trusted_paint_modules(model_final)
    if (
        not _snapshot_ready(model_final, MODEL_REQUIRED_FILES)
        or not _weights_ready(model_final, verify_hashes=True)
        or not _trusted_modules_ready(model_final)
        or not _dino_ready(dino_final, verify_hash=True)
    ):
        raise EngineError(
            "model-download",
            "The official model download did not produce a complete verified snapshot",
        )
    _atomic_json(
        state_root / "engine.ready.json",
        {
            "schema_version": ENGINE_SCHEMA_VERSION,
            "source_commit": SOURCE_COMMIT,
            "model_repository": MODEL_REPOSITORY,
            "model_revision": MODEL_REVISION,
            "dino_repository": DINO_REPOSITORY,
            "dino_revision": DINO_REVISION,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    return model_final, dino_final, downloaded


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,max_split_size_mb:256",
            "CUDA_MODULE_LOADING": "LAZY",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DIFFUSERS_OFFLINE": "1",
            "HF_ENDPOINT": "https://huggingface.co",
            "HF_HUB_ENABLE_HF_TRANSFER": "0",
            "HF_HUB_DISABLE_XET": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    environment.pop("HF_TOKEN", None)
    environment.pop("HUGGING_FACE_HUB_TOKEN", None)
    return environment


def _run_worker_child(arguments: list[str]) -> StageProcessResult:
    command = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(Path(__file__).resolve()),
        *arguments,
    ]
    process = subprocess.Popen(
        command,
        cwd=str(BUNDLE_ROOT),
        env=_child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None and process.stderr is not None
    last_child_error: tuple[str, str] | None = None

    def relay(stream: Any, *, error_stream: bool) -> None:
        nonlocal last_child_error
        for line in iter(stream.readline, ""):
            raw = line.rstrip("\r\n")
            if not error_stream and raw.startswith("MJERROR|"):
                fields = raw.split("|", 2)
                if len(fields) == 3:
                    child_code = _sanitize(fields[1], 80)
                    child_message = _sanitize(fields[2])
                    last_child_error = (child_code, child_message)
                    cleaned = f"MJERROR|{child_code}|{child_message}"
                else:
                    cleaned = _sanitize(raw, 2000)
            elif not error_stream and raw.startswith(
                ("MJPROGRESS|", "MJARTIFACT|", "MJSTAGEOOM|")
            ):
                cleaned = raw[:2000]
            else:
                cleaned = _sanitize(raw, 2000)
            print(cleaned, file=sys.stderr if error_stream else sys.stdout, flush=True)

    out_thread = threading.Thread(
        target=relay, args=(process.stdout,), kwargs={"error_stream": False}, daemon=True
    )
    err_thread = threading.Thread(
        target=relay, args=(process.stderr,), kwargs={"error_stream": True}, daemon=True
    )
    out_thread.start()
    err_thread.start()
    code = int(process.wait())
    out_thread.join()
    err_thread.join()
    return StageProcessResult(code, last_child_error)


def _run_stage(stage: str, state_path: Path) -> StageProcessResult:
    return _run_worker_child(
        ["--stage", stage, "--state", str(state_path)]
    )


def _run_rasterizer_preflight(texture_mode: str) -> StageProcessResult:
    return _run_worker_child(
        ["--rasterizer-self-test", "--texture-mode", texture_mode]
    )


def _orchestrate(request_path: Path) -> Path:
    payload = _validate_job(_load_json_file(request_path))
    # The worker gate is intentionally before engine/model setup and therefore
    # before the first possible network download.
    _validate_engine_pack()
    acceptance_path = _validate_license_acceptance()
    _progress(2, "Testing the CUDA runtime and Paint rasterizer before Shape")
    preflight_result = _run_rasterizer_preflight(payload["texture_mode"])
    if preflight_result != 0:
        if preflight_result.child_error is not None:
            raise EngineError(*preflight_result.child_error)
        raise EngineError(
            "paint-rasterizer-runtime",
            "Hunyuan Paint CUDA rasterizer self-check failed before Shape",
        )
    output_root = Path(payload["output_dir"])
    token = uuid.uuid4().hex
    staging = output_root / f".mujassam-{token}.partial"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = output_root / f"MujassamAI_{payload['target']}_{timestamp}_{token[:8]}"
    staging.mkdir(mode=0o700, exist_ok=False)
    published = False
    try:
        _progress(4, "Preparing Hunyuan3D 2.1 Ultimate PBR")
        image_path = Path(payload["image_path"])
        image_info = _prepare_image(image_path, staging)
        source_hash = _sha256(image_path)
        model_root, dino_root, downloaded = _download_models(_engine_state_root())

        paint_obj = staging / "pbr_mesh.obj"
        state: dict[str, Any] = {
            "schema_version": ENGINE_SCHEMA_VERSION,
            "job": payload,
            "staging": str(staging),
            "prepared_image": image_info["prepared_path"],
            "model_root": str(model_root),
            "dino_root": str(dino_root),
            "shape_mesh": str(staging / "shape.ply"),
            "paint_obj": str(paint_obj),
            "paint_base": str(paint_obj.with_suffix(".png")),
            "paint_metallic": str(paint_obj.with_name(paint_obj.stem + "_metallic.png")),
            "paint_roughness": str(paint_obj.with_name(paint_obj.stem + "_roughness.png")),
            "final_glb": str(staging / "model.glb"),
            "shape_inference_steps": SHAPE_INFERENCE_STEPS,
            "shape_guidance_scale": SHAPE_GUIDANCE_SCALE,
            "shape_octree_resolution": SHAPE_OCTREE_RESOLUTION,
            "shape_num_chunks": SHAPE_NUM_CHUNKS,
            "paint_max_views": PAINT_MAX_VIEWS,
            "paint_view_resolution": PAINT_VIEW_RESOLUTION,
            "paint_render_size": PAINT_RENDER_SIZE,
            "paint_texture_size": PBR_TEXTURE_SIZE,
        }
        state_path = staging / "engine-state.json"
        _atomic_json(state_path, state)
        resume_identity = _shape_resume_identity(
            Path(state["prepared_image"]), payload
        )
        shape_reused = _restore_shape_resume(
            resume_identity, Path(state["shape_mesh"])
        )
        if shape_reused:
            _progress(
                52,
                "Reusing verified completed geometry; skipping 50 Shape steps",
            )
        else:
            shape_result = _run_stage("shape", state_path)
            if shape_result != 0:
                if shape_result.child_error is not None:
                    raise EngineError(*shape_result.child_error)
                message = (
                    "Hunyuan3D 2.1 Ultimate Shape needs more available GPU/RAM at "
                    "the fixed 512/50-step quality. Close GPU programs and try again"
                    if shape_result == 42
                    else "Hunyuan3D 2.1 shape generation failed. Check the execution log"
                )
                raise EngineError("hunyuan21-shape", message)
            try:
                _save_shape_resume(resume_identity, Path(state["shape_mesh"]))
                _progress(52, "Saved verified geometry for automatic Paint retry")
            except (OSError, EngineError) as exc:
                _progress(
                    52,
                    "Geometry completed; retry checkpoint was unavailable: "
                    f"{_sanitize(exc, 240)}",
                )

        paint_result = _run_stage("paint", state_path)
        if paint_result != 0:
            if paint_result.child_error is not None:
                raise EngineError(*paint_result.child_error)
            message = (
                "Hunyuan3D Paint 2.1 needs more available GPU/RAM for the fixed "
                "12-view, 768, native-4K PBR quality. No quality downgrade was applied"
                if paint_result == 42
                else "Hunyuan3D Paint 2.1 failed. Check the execution log"
            )
            raise EngineError("hunyuan21-paint", message)

        finalize_result = _run_stage("finalize", state_path)
        if finalize_result != 0:
            if finalize_result.child_error is not None:
                raise EngineError(*finalize_result.child_error)
            message = (
                "Ultimate 8K AI restoration ran out of GPU memory; no lower-quality "
                "fallback was published"
                if finalize_result == 42 and payload["texture_mode"] == "export_8k"
                else "PBR maps were created, but lossless GLB export failed"
            )
            raise EngineError("pbr-finalize", message)
        final_glb = staging / "model.glb"
        if not final_glb.is_file() or final_glb.stat().st_size < 20:
            raise EngineError("glb-export", "Hunyuan3D did not create a valid GLB")

        stage_report = _load_json_file(staging / "stage-report.json")
        manifest = {
            "schema_version": 3,
            "application": "MujassamAI Portable",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "request": {
                "target": payload["target"],
                "source_schema_version": 3,
                "engine_mode": payload["engine_mode"],
                "texture_mode": payload["texture_mode"],
                "geometry_mode": payload["geometry_mode"],
                "hardware_preset": payload["hardware_preset"],
            },
            "source": {
                "original_name": image_path.name,
                "sha256": source_hash,
                **{key: value for key, value in image_info.items() if key != "prepared_path"},
            },
            "reconstruction": {
                "engine": "Hunyuan3D Shape 2.1 + Paint 2.1 PBR",
                "source_commit": SOURCE_COMMIT,
                "model_repository": MODEL_REPOSITORY,
                "model_revision": MODEL_REVISION,
                "dino_repository": DINO_REPOSITORY,
                "dino_revision": DINO_REVISION,
                "models_downloaded_this_run": downloaded,
                "separate_cuda_processes": True,
                "shape_resume_reused": shape_reused,
                "quality_downgraded": False,
                "shape_inference_steps": SHAPE_INFERENCE_STEPS,
                "shape_guidance_scale": SHAPE_GUIDANCE_SCALE,
                "shape_octree_resolution": SHAPE_OCTREE_RESOLUTION,
                "shape_num_chunks": SHAPE_NUM_CHUNKS,
                "paint_max_views": PAINT_MAX_VIEWS,
                "paint_view_resolution": PAINT_VIEW_RESOLUTION,
                "paint_native_texture_size": PBR_TEXTURE_SIZE,
                **stage_report,
            },
            "compliance": {
                "license_acceptance_file": acceptance_path.name,
                "license_sha256": LICENSE_SHA256,
                "territory_confirmation_required": True,
            },
            "output": {
                "glb": "model.glb",
                "glb_sha256": _sha256(final_glb),
                "prepared_image": "input_prepared.png",
            },
        }
        _atomic_json(staging / "manifest.json", manifest)
        for path in (
            state_path,
            staging / "shape.ply",
            Path(state["paint_obj"]),
            Path(state["paint_base"]),
            Path(state["paint_metallic"]),
            Path(state["paint_roughness"]),
            Path(state["paint_obj"]).with_suffix(".mtl"),
        ):
            path.unlink(missing_ok=True)

        _progress(98, "Publishing the completed 4K PBR asset")
        if final_dir.exists():
            raise EngineError("output-conflict", "The final output folder already exists")
        staging.rename(final_dir)
        published = True
        _delete_shape_resume(resume_identity)
        result = final_dir / "model.glb"
        _progress(100, "Hunyuan3D 2.1 Ultimate PBR model completed")
        _artifact(result)
        return result
    finally:
        if not published and staging.exists():
            try:
                if staging.parent == output_root and staging.name.startswith(".mujassam-"):
                    shutil.rmtree(staging, ignore_errors=True)
            except OSError:
                pass


def _force_weights_only_torch_load(
    torch: Any, allowed_files: dict[Path, tuple[int, str]]
) -> None:
    """Restrict pickle loads to already-hashed, allowlisted checkpoint paths."""

    if getattr(torch.load, "_mujassam_weights_only", False):
        return
    original = torch.load
    allowed = {
        path.resolve(strict=True): specification
        for path, specification in allowed_files.items()
    }

    def restricted_load(*args: Any, **kwargs: Any) -> Any:
        source = args[0] if args else kwargs.get("f")
        # pathlib.Path also has a ``name`` attribute, but it is only the final
        # component.  Preserve path-like inputs so relative resolution cannot
        # silently discard their verified parent directory.  Only unwrap the
        # ``name`` of an actual file-like object.
        if not isinstance(source, (str, os.PathLike)) and hasattr(source, "name"):
            source = source.name
        if not isinstance(source, (str, os.PathLike)):
            raise RuntimeError("torch.load source is not an allowlisted checkpoint path")
        try:
            resolved = Path(source).resolve(strict=True)
        except OSError as exc:
            raise RuntimeError("torch.load checkpoint path is missing") from exc
        specification = allowed.get(resolved)
        if specification is None:
            raise RuntimeError(f"torch.load refused non-allowlisted checkpoint: {resolved.name}")
        expected_size, expected_sha256 = specification
        if kwargs.get("weights_only") is False:
            raise RuntimeError("Unsafe torch.load(weights_only=False) was blocked")
        # Transformers 4.46 requests mmap=True for zip-format PyTorch
        # checkpoints.  mmap cannot preserve the same-open-handle guarantee
        # below, so neutralize the optimization and continue with the exact
        # verified handle instead of rejecting an otherwise valid load.
        kwargs.pop("mmap", None)
        kwargs["weights_only"] = True
        # Hash and deserialize from one already-open handle.  A path can be
        # renamed/replaced after verification; the handle pins the exact bytes
        # that were hashed and removes that final path-based TOCTOU window.
        with resolved.open("rb") as checkpoint:
            if os.fstat(checkpoint.fileno()).st_size != expected_size:
                raise RuntimeError(
                    f"torch.load refused changed allowlisted checkpoint: {resolved.name}"
                )
            digest = hashlib.sha256()
            for chunk in iter(lambda: checkpoint.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != expected_sha256:
                raise RuntimeError(
                    f"torch.load refused changed allowlisted checkpoint: {resolved.name}"
                )
            checkpoint.seek(0)
            if args:
                safe_args = (checkpoint, *args[1:])
                return original(*safe_args, **kwargs)
            safe_kwargs = dict(kwargs)
            safe_kwargs["f"] = checkpoint
            return original(**safe_kwargs)

    restricted_load._mujassam_weights_only = True  # type: ignore[attr-defined]
    torch.load = restricted_load


def _verify_weight_subset(model_root: Path, relatives: tuple[str, ...]) -> None:
    for relative in relatives:
        size, digest = PINNED_MODEL_WEIGHTS[relative]
        if not _verify_file(model_root / relative, size, digest):
            raise EngineError(
                "model-verification", f"Pinned model weight changed before load: {relative}"
            )


def _validated_stage_model_paths(
    state: dict[str, Any], *, stage: str
) -> tuple[Path, Path]:
    expected = _engine_state_root() / "models"
    model_root = Path(str(state.get("model_root", ""))).resolve(strict=True)
    dino_root = Path(str(state.get("dino_root", ""))).resolve(strict=True)
    if (
        model_root != (expected / "Hunyuan3D-2.1").resolve()
        or dino_root != (expected / "dinov2-giant").resolve()
        or not _snapshot_ready(model_root, MODEL_REQUIRED_FILES)
        or not _trusted_modules_ready(model_root)
        or not _dino_ready(dino_root, verify_hash=False)
    ):
        raise EngineError("stage-state", "Stage model paths are not the verified pinned cache")
    if stage == "shape":
        _verify_weight_subset(
            model_root,
            (
                f"{SHAPE_SUBFOLDER}/model.fp16.ckpt",
                f"{SHAPE_VAE_SUBFOLDER}/model.fp16.ckpt",
            ),
        )
    elif stage == "paint":
        _verify_weight_subset(
            model_root,
            (
                f"{PAINT_SUBFOLDER}/text_encoder/pytorch_model.bin",
                f"{PAINT_SUBFOLDER}/unet/diffusion_pytorch_model.bin",
                f"{PAINT_SUBFOLDER}/vae/diffusion_pytorch_model.bin",
                f"{PAINT_SUBFOLDER}/image_encoder/model.safetensors",
            ),
        )
        if not _dino_ready(dino_root, verify_hash=True):
            raise EngineError("model-verification", "Pinned DINOv2 weight changed before load")
    return model_root, dino_root


def _stage_shape(state: dict[str, Any]) -> int:
    _progress(23, "Re-verifying pinned Shape checkpoint bytes immediately before load")
    model_root, _dino_root = _validated_stage_model_paths(state, stage="shape")
    _activate_engine_paths()
    import torch
    from PIL import Image

    _force_weights_only_torch_load(
        torch,
        {
            model_root / relative: PINNED_MODEL_WEIGHTS[relative]
            for relative in (
                f"{SHAPE_SUBFOLDER}/model.fp16.ckpt",
                f"{SHAPE_VAE_SUBFOLDER}/model.fp16.ckpt",
            )
        },
    )
    from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline
    from hy3dshape.postprocessors import DegenerateFaceRemover, FaceReducer, FloaterRemover

    if not torch.cuda.is_available():
        raise EngineError("cuda-required", "Hunyuan3D 2.1 requires an NVIDIA CUDA GPU")
    _progress(27, "Loading pinned Hunyuan3D Shape 2.1 locally")
    pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        str(model_root),
        subfolder=SHAPE_SUBFOLDER,
        use_safetensors=False,
        variant="fp16",
        device="cuda",
        dtype=torch.float16,
        local_files_only=True,
    )
    try:
        with Image.open(state["prepared_image"]) as opened:
            image = opened.convert("RGBA")
        _progress(34, "Generating ultimate 512-octree geometry with 50 diffusion steps")
        try:
            mesh = pipeline(
                image=image,
                num_inference_steps=SHAPE_INFERENCE_STEPS,
                guidance_scale=SHAPE_GUIDANCE_SCALE,
                octree_resolution=SHAPE_OCTREE_RESOLUTION,
                num_chunks=SHAPE_NUM_CHUNKS,
                generator=_seed_torch(torch),
                output_type="trimesh",
                enable_pbar=False,
            )[0]
        except torch.cuda.OutOfMemoryError:
            print("MJSTAGEOOM|shape|CUDA memory exhausted at fixed ultimate quality", flush=True)
            return 42

        _progress(48, "Cleaning geometry without changing the selected quality profile")
        mesh = FloaterRemover()(mesh)
        mesh = DegenerateFaceRemover()(mesh)
        geometry_mode = state["job"]["geometry_mode"]
        if geometry_mode == "target_ready":
            mesh = FaceReducer()(mesh, max_facenum=ROBLOX_READY_FACES)
        elif geometry_mode == "max_detail":
            mesh = FaceReducer()(mesh, max_facenum=ROBLOX_MASTER_FACES)
        try:
            mesh.remove_unreferenced_vertices()
            mesh.fix_normals(multibody=True)
        except Exception:
            pass
        mesh.export(state["shape_mesh"])
        shape_path = Path(state["shape_mesh"])
        if not shape_path.is_file() or shape_path.stat().st_size < 100:
            raise EngineError("shape-export", "Hunyuan3D Shape did not export a mesh")
        _progress(52, "Ultimate geometry completed; releasing Shape CUDA memory")
        return 0
    finally:
        pipeline = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _validate_native_pbr_maps(state: dict[str, Any]) -> dict[str, list[int]]:
    from PIL import Image

    records: dict[str, list[int]] = {}
    for key in ("paint_base", "paint_metallic", "paint_roughness"):
        path = Path(state[key])
        try:
            if path.is_symlink() or not path.is_file():
                raise ValueError("file is missing")
            with Image.open(path) as image:
                image.load()
                size = [int(image.width), int(image.height)]
                if size != [PBR_TEXTURE_SIZE, PBR_TEXTURE_SIZE]:
                    raise ValueError(f"expected 4096x4096, got {size[0]}x{size[1]}")
                if (image.format or "").upper() != "PNG":
                    raise ValueError("map is not lossless PNG")
            records[key] = size
        except Exception as exc:
            raise EngineError(
                "paint-texture",
                f"Hunyuan Paint did not preserve its native 4K {key} map: {exc}",
            ) from exc
    return records


def _collect_cuda(torch_module: Any) -> None:
    """Release unreachable stage objects without changing the quality profile."""

    gc.collect()
    if torch_module.cuda.is_available():
        torch_module.cuda.empty_cache()


def _streaming_projection_is_redundant(painted_sum: Any, view_sum: Any) -> Any:
    """Keep Tencent's strict fast-bake redundancy test in one audited place."""

    return painted_sum / view_sum > 0.99


def _slice_cfg_batch(value: Any, branch: int, *, branches: int = 3) -> Any:
    """Slice tensor-like CFG inputs while preserving non-batch metadata.

    Tencent's Paint pipeline concatenates the unconditional, reference, and
    full-conditioning branches on dimension zero.  Dictionaries may contain
    more batched tensors, while lists such as the 12 camera azimuths are shared
    metadata and must not be sliced.
    """

    if isinstance(value, dict):
        return {
            key: _slice_cfg_batch(item, branch, branches=branches)
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(
            _slice_cfg_batch(item, branch, branches=branches) for item in value
        )
    if isinstance(value, list):
        return [
            _slice_cfg_batch(item, branch, branches=branches) for item in value
        ]
    shape = getattr(value, "shape", None)
    if shape is not None:
        try:
            if len(shape) > 0 and int(shape[0]) == branches:
                return value[branch : branch + 1]
        except (IndexError, TypeError, ValueError):
            pass
    return value


class _LowVramPaintDiffusionSchedule:
    """Stage DINO, VAE, and UNet to minimize overlapping peak allocations.

    The pinned custom pipeline creates its CUDA generator before DINO runs, so
    this scheduler deliberately starts inside the DINO call instead of using a
    generic Diffusers CPU-offload hook that could change the generator device.
    The three classifier-free-guidance branches retain the official order and
    math but execute sequentially to reduce activation memory.
    """

    _CFG_BRANCHES = 3
    _BRANCH_CACHE_KEY = "_mujassam_cfg_branch_caches"
    _MAIN_UNET_FF_CHUNK = 12

    def __init__(
        self,
        multiview_model: Any,
        torch_module: Any,
        *,
        emit_progress: bool = True,
    ) -> None:
        diffusion_pipeline = getattr(multiview_model, "pipeline", None)
        unet = getattr(diffusion_pipeline, "unet", None)
        vae = getattr(diffusion_pipeline, "vae", None)
        dino = getattr(multiview_model, "dino_v2", None)
        dual = getattr(unet, "unet_dual", None)
        if (
            diffusion_pipeline is None
            or unet is None
            or vae is None
            or dino is None
            or not callable(getattr(unet, "to", None))
            or not callable(getattr(vae, "to", None))
            or not callable(dino)
            or not callable(getattr(diffusion_pipeline, "denoise", None))
            or not callable(getattr(unet, "forward", None))
            or dual is None
            or not callable(getattr(dual, "to", None))
            or not callable(getattr(dual, "forward", None))
            or not callable(getattr(vae, "decode", None))
        ):
            raise EngineError(
                "paint-runtime", "The pinned Paint diffusion runtime has an unexpected layout"
            )
        if not all(
            bool(getattr(unet, flag, False))
            for flag in (
                "use_dino",
                "use_learned_text_clip",
                "use_dual_stream",
                "use_ra",
            )
        ):
            raise EngineError(
                "paint-runtime", "The pinned Paint DINO/learned-text configuration changed"
            )

        self._multiview_model = multiview_model
        self._pipeline = diffusion_pipeline
        self._unet = unet
        self._dual = dual
        self._vae = vae
        self._dino = dino
        self._torch = torch_module
        self._emit_progress = emit_progress
        self._original_denoise = diffusion_pipeline.denoise
        self._original_unet_forward = unet.forward
        self._original_dual_forward = dual.forward
        self._original_vae_decode = vae.decode
        self._dino_used = False
        self._denoise_used = False
        self._vae_offloaded_for_unet = False
        self._unet_offloaded_for_decode = False
        self._dual_offloaded = False
        self._dual_used = False
        self._condition_cache: dict[str, Any] | None = None

    def install(self) -> None:
        # Each sequential CFG branch contains 2 PBR channels x 12 views.  Split
        # only the main UNet feed-forward activation into two groups of 12;
        # the reference UNet is intentionally left untouched so its cache is
        # produced once with the exact official computation.
        main_unet = getattr(self._unet, "unet", None)
        modules = getattr(main_unet, "modules", None)
        if not callable(modules):
            raise EngineError(
                "paint-runtime", "The pinned main Paint UNet cannot enumerate its blocks"
            )
        chunked_blocks = 0
        for module in modules():
            if module.__class__.__name__ != "Basic2p5DTransformerBlock":
                continue
            set_chunk = getattr(module, "set_chunk_feed_forward", None)
            if not callable(set_chunk):
                raise EngineError(
                    "paint-runtime", "A pinned Paint transformer cannot enable FF chunking"
                )
            set_chunk(self._MAIN_UNET_FF_CHUNK, dim=0)
            chunked_blocks += 1
        if chunked_blocks <= 0:
            raise EngineError(
                "paint-runtime", "No pinned main Paint transformer blocks were found"
            )
        self._multiview_model.dino_v2 = self._run_dino
        self._pipeline.denoise = self._run_denoise
        # nn.Module.__call__ resolves ``forward`` from the instance, so these
        # bound methods preserve every upstream call site and its arguments.
        self._unet.forward = self._run_unet_forward
        self._dual.forward = self._run_dual_forward
        self._vae.decode = self._run_vae_decode

    @staticmethod
    def _move(module: Any, device: str, *, label: str) -> None:
        mover = getattr(module, "to", None)
        if not callable(mover):
            raise EngineError("paint-runtime", f"{label} cannot move to {device}")
        mover(device)

    def _run_dino(self, *args: Any, **kwargs: Any) -> Any:
        if self._dino_used or self._dino is None:
            raise EngineError("paint-runtime", "DINO feature extraction is one-shot")
        self._dino_used = True
        if self._emit_progress:
            _progress(66, "Extracting DINO features alone before loading VAE/UNet on CUDA")

        # The CUDA generator was already created by upstream at this point.
        # Offload every competing diffusion component, but keep the tiny DINO
        # output on CUDA for the later projector.
        for name in ("unet", "vae", "text_encoder", "image_encoder"):
            module = getattr(self._pipeline, name, None)
            if module is not None and callable(getattr(module, "to", None)):
                module.to("cpu")
        _collect_cuda(self._torch)

        dino = self._dino
        try:
            result = dino(*args, **kwargs)
        finally:
            # Break both owner links even if feature extraction raises.  This
            # prevents a failed attempt retaining the 2.3 GiB FP16 DINO model.
            self._dino = None
            self._multiview_model.dino_v2 = None
            del dino
            _collect_cuda(self._torch)

        # The pinned pipeline uses learned material tokens; its text/image
        # encoders are never consulted during inference and can remain absent.
        for name in ("text_encoder", "image_encoder"):
            if hasattr(self._pipeline, name):
                setattr(self._pipeline, name, None)
        self._move(self._vae, "cuda", label="Paint VAE")
        if self._emit_progress:
            _progress(67, "DINO released; encoding all 12 native 768-view conditions with VAE")
        return result

    def _run_denoise(self, *args: Any, **kwargs: Any) -> Any:
        if self._denoise_used:
            raise EngineError("paint-runtime", "Paint denoising is one-shot")
        if not self._dino_used:
            raise EngineError("paint-runtime", "DINO features must be staged before denoising")
        self._denoise_used = True

        # Keep VAE and UNet on CUDA only long enough for Diffusers to resolve
        # its execution device and create CUDA latents.  The first UNet forward
        # immediately sends VAE back to CPU.
        self._move(self._unet, "cuda", label="Paint UNet")
        self._move(self._vae, "cuda", label="Paint VAE")
        for key in ("prompt_embeds", "negative_prompt_embeds"):
            tensor = kwargs.get(key)
            mover = getattr(tensor, "to", None)
            if callable(mover):
                kwargs[key] = mover("cuda")
        if self._emit_progress:
            _progress(68, "VAE encodes complete; starting sequential 3-branch UNet diffusion")
        return self._original_denoise(*args, **kwargs)

    def _run_unet_forward(
        self,
        sample: Any,
        timestep: Any,
        encoder_hidden_states: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if not self._vae_offloaded_for_unet:
            self._move(self._vae, "cpu", label="Paint VAE")
            self._vae_offloaded_for_unet = True
            _collect_cuda(self._torch)

        shape = getattr(sample, "shape", None)
        try:
            cfg_batch = int(shape[0]) if shape is not None and len(shape) else 0
        except (IndexError, TypeError, ValueError):
            cfg_batch = 0
        if cfg_batch != self._CFG_BRANCHES:
            raise EngineError(
                "paint-runtime",
                "Pinned Paint UNet no longer supplied exactly three CFG branches",
            )

        outer_cache = kwargs.get("cache")
        if not isinstance(outer_cache, dict):
            raise EngineError("paint-runtime", "Paint UNet condition cache is unavailable")
        if self._condition_cache is None:
            self._condition_cache = outer_cache
        elif self._condition_cache is not outer_cache:
            raise EngineError("paint-runtime", "Paint UNet condition cache identity changed")
        branch_caches = outer_cache.get(self._BRANCH_CACHE_KEY)
        if branch_caches is None:
            branch_caches = [{} for _ in range(self._CFG_BRANCHES)]
            outer_cache[self._BRANCH_CACHE_KEY] = branch_caches
        if (
            not isinstance(branch_caches, list)
            or len(branch_caches) != self._CFG_BRANCHES
            or not all(isinstance(item, dict) for item in branch_caches)
        ):
            raise EngineError("paint-runtime", "Paint CFG branch cache is invalid")

        outputs = []
        for branch in range(self._CFG_BRANCHES):
            branch_kwargs = {
                key: _slice_cfg_batch(value, branch, branches=self._CFG_BRANCHES)
                for key, value in kwargs.items()
                if key != "cache"
            }
            branch_kwargs["cache"] = branch_caches[branch]
            output = self._original_unet_forward(
                _slice_cfg_batch(sample, branch, branches=self._CFG_BRANCHES),
                timestep,
                _slice_cfg_batch(
                    encoder_hidden_states, branch, branches=self._CFG_BRANCHES
                ),
                *(
                    _slice_cfg_batch(value, branch, branches=self._CFG_BRANCHES)
                    for value in args
                ),
                **branch_kwargs,
            )
            if not isinstance(output, (tuple, list)) or not output:
                raise EngineError("paint-runtime", "Paint UNet returned an invalid result")
            outputs.append(output)

            if branch == 0:
                if not self._dual_offloaded:
                    raise EngineError(
                        "paint-runtime",
                        "Reference Paint UNet was not released before the main UNet",
                    )
                # These two caches depend only on the identical repeated
                # reference/position inputs, so sharing their exact objects is
                # equivalent to the original B=3 calculation.  Unconditional
                # and reference branches also share zero DINO input; the full
                # branch must compute and retain its independent DINO cache.
                required_shared = (
                    "condition_embed_dict",
                    "position_voxel_indices",
                )
                if not all(key in branch_caches[0] for key in required_shared):
                    raise EngineError(
                        "paint-runtime", "Paint UNet did not create its reference caches"
                    )
                for target in branch_caches[1:]:
                    for key in required_shared:
                        target[key] = branch_caches[0][key]
                if "dino_hidden_states_proj" not in branch_caches[0]:
                    raise EngineError("paint-runtime", "Paint UNet did not create its DINO cache")
                branch_caches[1]["dino_hidden_states_proj"] = branch_caches[0][
                    "dino_hidden_states_proj"
                ]

        merged_first = self._torch.cat(
            [output[0] for output in outputs], dim=0
        )
        first = outputs[0]
        if isinstance(first, tuple):
            merged: Any = (merged_first, *first[1:])
        else:
            merged = [merged_first, *first[1:]]

        return merged

    def _run_dual_forward(self, *args: Any, **kwargs: Any) -> Any:
        """Offload the reference UNet before its parent starts the main UNet."""

        if self._dual_used:
            raise EngineError("paint-runtime", "Reference Paint UNet ran more than once")
        cross_attention = kwargs.get("cross_attention_kwargs")
        if (
            not isinstance(cross_attention, dict)
            or cross_attention.get("mode") != "w"
            or not isinstance(cross_attention.get("condition_embed_dict"), dict)
        ):
            raise EngineError(
                "paint-runtime", "Reference Paint UNet cache ABI changed"
            )
        condition_cache = cross_attention["condition_embed_dict"]
        self._dual_used = True
        result = self._original_dual_forward(*args, **kwargs)
        if (
            cross_attention.get("condition_embed_dict") is not condition_cache
            or not condition_cache
        ):
            raise EngineError(
                "paint-runtime", "Reference Paint UNet did not populate its cache"
            )
        # The upstream parent uses the reference call only for attention cache
        # side effects.  Its forward has succeeded here, so the weights can
        # leave CUDA before control resumes at the main UNet.
        self._move(self._dual, "cpu", label="Reference Paint UNet")
        self._dual_offloaded = True
        _collect_cuda(self._torch)
        if self._emit_progress:
            _progress(
                68,
                "Reference UNet cached and released before main diffusion",
            )
        return result

    def _run_vae_decode(self, *args: Any, **kwargs: Any) -> Any:
        if not self._unet_offloaded_for_decode:
            self._move(self._unet, "cpu", label="Paint UNet")
            self._unet_offloaded_for_decode = True
            if self._condition_cache is not None:
                self._condition_cache.clear()
                self._condition_cache = None
            _collect_cuda(self._torch)
            self._move(self._vae, "cuda", label="Paint VAE")
            if self._emit_progress:
                _progress(69, "UNet released; decoding the 12-view PBR result with VAE")
        return self._original_vae_decode(*args, **kwargs)


class _LowVramSuperResolutionOneShot:
    """Keep RealESRGAN off CUDA until diffusion has released its large models."""

    def __init__(
        self, super_model: Any, torch_module: Any, *, emit_progress: bool = True
    ) -> None:
        upsampler = getattr(super_model, "upsampler", None)
        model = getattr(upsampler, "model", None)
        if upsampler is None or model is None or not callable(getattr(model, "to", None)):
            raise EngineError(
                "paint-runtime", "The bundled RealESRGAN runtime has an unexpected layout"
            )
        self._super_model = super_model
        self._upsampler = upsampler
        self._model = model
        self._torch = torch_module
        self._emit_progress = emit_progress
        self._expected_calls = 0
        self._completed_calls = 0
        self._on_cuda = True
        self._released = False

    def offload_for_diffusion(self) -> None:
        if self._released:
            raise EngineError("paint-runtime", "RealESRGAN was already released")
        if self._emit_progress:
            _progress(65, "Moving RealESRGAN off CUDA before 12-view diffusion")
        self._model.to("cpu")
        self._upsampler.device = self._torch.device("cpu")
        self._on_cuda = False
        _collect_cuda(self._torch)

    def restore_for_enhancement(self, expected_calls: int) -> None:
        if self._released or expected_calls <= 0:
            raise EngineError("paint-runtime", "Paint returned no PBR views to enhance")
        self._expected_calls = int(expected_calls)
        self._completed_calls = 0
        self._model.to("cuda")
        self._upsampler.device = self._torch.device("cuda")
        self._on_cuda = True
        if self._emit_progress:
            _progress(70, "Diffusion models released; restoring RealESRGAN for PBR views")

    def _release_before_bake(self) -> None:
        if self._released:
            return
        # Break every owner link before collecting.  The upstream pipeline never
        # uses RealESRGAN after the last enhanced albedo/MR view.
        self._upsampler.model = None
        self._model = None
        self._super_model = None
        self._upsampler = None
        self._on_cuda = False
        self._released = True
        _collect_cuda(self._torch)
        if self._emit_progress:
            _progress(74, "PBR views enhanced; releasing RealESRGAN before the 4K bake")

    def __call__(self, image: Any) -> Any:
        if self._released or not self._on_cuda or self._super_model is None:
            raise EngineError("paint-runtime", "RealESRGAN is unavailable for enhancement")
        result = self._super_model(image)
        self._completed_calls += 1
        if self._completed_calls > self._expected_calls:
            raise EngineError("paint-runtime", "Paint requested unexpected extra enhancements")
        if self._emit_progress:
            percent = 70 + min(
                3,
                (3 * self._completed_calls) // max(1, self._expected_calls),
            )
            _progress(
                percent,
                f"Enhancing PBR view {self._completed_calls}/{self._expected_calls}",
            )
        if self._completed_calls == self._expected_calls:
            self._release_before_bake()
        return result


class _LowVramMultiviewOneShot:
    """Run diffusion once, then discard UNet/VAE/DINO before RealESRGAN."""

    def __init__(
        self,
        multiview_model: Any,
        super_model: _LowVramSuperResolutionOneShot,
        torch_module: Any,
        *,
        emit_progress: bool = True,
    ) -> None:
        self._multiview_model = multiview_model
        self._super_model = super_model
        self._torch = torch_module
        self._emit_progress = emit_progress
        self._used = False
        self._released = False

    def _release_diffusion_models(self) -> None:
        multiview_model = self._multiview_model
        diffusion_pipeline = getattr(multiview_model, "pipeline", None)
        if diffusion_pipeline is not None:
            # Explicitly sever the three large CUDA owners first.  Clearing the
            # remaining encoders and the pipeline then makes their allocations
            # collectible before RealESRGAN is returned to CUDA.
            for name in ("unet", "vae", "image_encoder", "text_encoder"):
                if hasattr(diffusion_pipeline, name):
                    setattr(diffusion_pipeline, name, None)
            multiview_model.pipeline = None
        if hasattr(multiview_model, "dino_v2"):
            multiview_model.dino_v2 = None
        self._multiview_model = None
        self._released = True
        _collect_cuda(self._torch)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self._used or self._multiview_model is None:
            raise EngineError("paint-runtime", "Multiview diffusion is one-shot per Paint stage")
        self._used = True
        self._super_model.offload_for_diffusion()
        result = self._multiview_model(*args, **kwargs)
        if not isinstance(result, dict):
            raise EngineError("paint-runtime", "Paint returned an invalid PBR view collection")
        albedo = result.get("albedo")
        metallic_roughness = result.get("mr")
        if (
            not isinstance(albedo, list)
            or not isinstance(metallic_roughness, list)
            or not albedo
            or len(albedo) != len(metallic_roughness)
        ):
            raise EngineError("paint-runtime", "Paint returned incomplete albedo/MR views")
        if self._emit_progress:
            _progress(69, "12-view diffusion completed; releasing UNet, VAE, and DINO")
        self._release_diffusion_models()
        self._super_model.restore_for_enhancement(len(albedo) + len(metallic_roughness))
        return result


def _install_streaming_pbr_bake(
    view_processor: Any, torch_module: Any, *, emit_progress: bool = True
) -> None:
    """Bake one projection at a time with Tencent's exact merge order/math."""

    bake_pass = 0

    def bake_from_multiview(
        views: Any,
        camera_elevs: Any,
        camera_azims: Any,
        view_weights: Any,
    ) -> tuple[Any, Any]:
        nonlocal bake_pass
        bake_pass += 1
        texture_merge = None
        trust_map_merge = None
        view_count = min(
            len(views), len(camera_elevs), len(camera_azims), len(view_weights)
        )
        if view_count <= 0:
            raise EngineError("paint-runtime", "Paint supplied no views for the 4K bake")

        for index, (view, camera_elev, camera_azim, weight) in enumerate(
            zip(views, camera_elevs, camera_azims, view_weights), start=1
        ):
            project_texture, project_cos_map, project_boundary_map = (
                view_processor.render.back_project(view, camera_elev, camera_azim)
            )
            # Preserve upstream exactly: weight first multiplies cosine raised
            # to bake_exp, then views are considered in their selected order.
            project_cos_map = weight * (project_cos_map**view_processor.config.bake_exp)
            if texture_merge is None:
                texture_merge = torch_module.zeros_like(project_texture)
                trust_map_merge = torch_module.zeros_like(project_cos_map)

            view_sum = (project_cos_map > 0).sum()
            painted_sum = ((project_cos_map > 0) * (trust_map_merge > 0)).sum()
            skipped = bool(
                _streaming_projection_is_redundant(painted_sum, view_sum)
            )
            if not skipped:
                texture_merge.add_(project_texture * project_cos_map)
                trust_map_merge.add_(project_cos_map)

            # Neither the unused boundary map nor an earlier 4K projection is
            # retained; the next iteration reuses the CUDA allocator's blocks.
            del project_texture, project_cos_map, project_boundary_map
            if emit_progress:
                base_percent = 74 if bake_pass == 1 else 77
                percent = base_percent + min(3, (3 * index) // view_count)
                suffix = " (redundant view skipped)" if skipped else ""
                _progress(
                    percent,
                    f"Streaming 4K PBR bake {bake_pass}/2: view {index}/{view_count}{suffix}",
                )

        if texture_merge is None or trust_map_merge is None:
            raise EngineError("paint-runtime", "The streaming 4K bake produced no texture")
        texture_merge = texture_merge / torch_module.clamp(trust_map_merge, min=1e-8)
        return texture_merge, trust_map_merge > 1e-8

    view_processor.bake_from_multiview = bake_from_multiview


def _install_low_vram_paint_runtime(
    pipeline: Any, torch_module: Any, *, emit_progress: bool = True
) -> None:
    """Install runtime-only 8 GiB scheduling without changing Paint quality."""

    models = getattr(pipeline, "models", None)
    if not isinstance(models, dict):
        raise EngineError("paint-runtime", "The Paint model registry is unavailable")
    multiview_model = models.get("multiview_model")
    super_model = models.get("super_model")
    diffusion_pipeline = getattr(multiview_model, "pipeline", None)
    if diffusion_pipeline is None:
        raise EngineError("paint-runtime", "The multiview diffusion pipeline is unavailable")

    enable_vae_slicing = getattr(diffusion_pipeline, "enable_vae_slicing", None)
    if callable(enable_vae_slicing):
        enable_vae_slicing()
    else:
        vae = getattr(diffusion_pipeline, "vae", None)
        enable_slicing = getattr(vae, "enable_slicing", None)
        if not callable(enable_slicing):
            raise EngineError("paint-runtime", "The pinned Paint VAE cannot enable slicing")
        enable_slicing()

    diffusion_schedule = _LowVramPaintDiffusionSchedule(
        multiview_model, torch_module, emit_progress=emit_progress
    )
    diffusion_schedule.install()

    super_wrapper = _LowVramSuperResolutionOneShot(
        super_model, torch_module, emit_progress=emit_progress
    )
    models["super_model"] = super_wrapper
    models["multiview_model"] = _LowVramMultiviewOneShot(
        multiview_model,
        super_wrapper,
        torch_module,
        emit_progress=emit_progress,
    )
    _install_streaming_pbr_bake(
        pipeline.view_processor, torch_module, emit_progress=emit_progress
    )
    if emit_progress:
        _progress(
            61,
            "Enabled staged DINO/VAE/UNet, sequential CFG, and native 4K streaming",
        )


def _stage_paint(state: dict[str, Any]) -> int:
    _progress(54, "Re-verifying pinned Paint and DINO bytes immediately before load")
    model_root, dino_root = _validated_stage_model_paths(state, stage="paint")
    _activate_engine_paths()
    import torch

    _force_weights_only_torch_load(
        torch,
        {
            model_root / relative: PINNED_MODEL_WEIGHTS[relative]
            for relative in (
                f"{PAINT_SUBFOLDER}/text_encoder/pytorch_model.bin",
                f"{PAINT_SUBFOLDER}/unet/diffusion_pytorch_model.bin",
                f"{PAINT_SUBFOLDER}/vae/diffusion_pytorch_model.bin",
            )
        }
        | {
            REALESRGAN_X4: (REALESRGAN_X4_BYTES, REALESRGAN_X4_SHA256),
        },
    )
    from utils.torchvision_fix import apply_fix

    if not apply_fix():
        raise EngineError(
            "paint-runtime",
            "The reviewed torchvision compatibility shim could not be applied",
        )
    from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline

    if not torch.cuda.is_available():
        raise EngineError("cuda-required", "Hunyuan Paint 2.1 requires NVIDIA CUDA")
    if not _verify_file(REALESRGAN_X4, REALESRGAN_X4_BYTES, REALESRGAN_X4_SHA256):
        raise EngineError(
            "model-verification", "RealESRGAN_x4plus changed before Paint could load it"
        )
    config = Hunyuan3DPaintConfig(PAINT_MAX_VIEWS, PAINT_VIEW_RESOLUTION)
    config.device = "cuda"
    config.render_size = PAINT_RENDER_SIZE
    config.texture_size = PBR_TEXTURE_SIZE
    config.multiview_cfg_path = str(PAINT_VENDOR_ROOT / "cfgs" / "hunyuan-paint-pbr.yaml")
    config.custom_pipeline = str(PAINT_VENDOR_ROOT / "hunyuanpaintpbr")
    config.multiview_pretrained_path = str(model_root)
    config.dino_ckpt_path = str(dino_root)
    config.realesrgan_ckpt_path = str(REALESRGAN_X4)
    _progress(57, "Loading pinned Hunyuan Paint 2.1 PBR entirely from local files")
    pipeline = Hunyuan3DPaintPipeline(config)
    _install_low_vram_paint_runtime(pipeline, torch)
    try:
        _progress(64, "Painting 12 selected views at 768 with staged CUDA memory")
        try:
            pipeline(
                mesh_path=state["shape_mesh"],
                image_path=state["prepared_image"],
                output_mesh_path=state["paint_obj"],
                use_remesh=False,
                save_glb=False,
            )
        except torch.cuda.OutOfMemoryError:
            print("MJSTAGEOOM|paint|CUDA memory exhausted at fixed ultimate quality", flush=True)
            return 42
        paint_obj = Path(state["paint_obj"])
        if not paint_obj.is_file() or paint_obj.stat().st_size < 100:
            raise EngineError("paint-export", "Hunyuan Paint did not export its PBR OBJ")
        _validate_native_pbr_maps(state)
        _progress(80, "Native 4K albedo, metallic, and roughness maps verified")
        return 0
    finally:
        pipeline = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _load_base_worker() -> Any:
    spec = importlib.util.spec_from_file_location("mujassam_base_worker_h21", BASE_WORKER_PATH)
    if spec is None or spec.loader is None:
        raise EngineError("pbr-runtime", "Could not load the Mujassam GLB quality module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _glb_document(path: Path) -> dict[str, Any]:
    try:
        file_size = path.stat().st_size
        with path.open("rb") as stream:
            magic, version, declared = struct.unpack("<4sII", stream.read(12))
            if magic != b"glTF" or version != 2 or declared != file_size:
                raise ValueError("invalid GLB header")
            json_length, json_type = struct.unpack("<II", stream.read(8))
            if json_type != 0x4E4F534A or json_length <= 0 or json_length > file_size - 20:
                raise ValueError("invalid GLB JSON chunk")
            document = json.loads(
                stream.read(json_length).decode("utf-8").rstrip(" \t\r\n\x00")
            )
    except (OSError, UnicodeError, json.JSONDecodeError, struct.error, ValueError) as exc:
        raise EngineError("glb-validation", f"Exported GLB is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise EngineError("glb-validation", "Exported GLB JSON is invalid")
    return document


def _validate_pbr_glb(path: Path, *, expected_color_edge: int, expected_data_edge: int) -> dict[str, Any]:
    import trimesh

    base = _load_base_worker()
    metadata = base._glb_metadata(path)
    document = _glb_document(path)
    images = document.get("images", [])
    if len(images) < 2 or any(
        not isinstance(image, dict) or image.get("mimeType") != "image/png" for image in images
    ):
        raise EngineError("glb-texture", "Final GLB must embed all PBR maps as lossless PNG")
    materials = document.get("materials", [])
    pbr_materials = [
        item.get("pbrMetallicRoughness", {})
        for item in materials
        if isinstance(item, dict) and isinstance(item.get("pbrMetallicRoughness"), dict)
    ]
    if not any(
        isinstance(pbr.get("baseColorTexture"), dict)
        and isinstance(pbr.get("metallicRoughnessTexture"), dict)
        for pbr in pbr_materials
    ):
        raise EngineError(
            "glb-pbr", "Final GLB does not reference both albedo and metallic-roughness textures"
        )
    reloaded = trimesh.load(path, force="mesh", process=False)
    _material, textures = base._capture_material_textures(reloaded)
    color = textures.get("baseColorTexture") or textures.get("image")
    data = textures.get("metallicRoughnessTexture")
    if color is None or data is None:
        raise EngineError("glb-pbr", "Final GLB PBR textures could not be decoded")
    if max(color.size) != expected_color_edge or max(data.size) != expected_data_edge:
        raise EngineError(
            "glb-texture",
            "Final GLB texture dimensions do not match the selected lossless profile",
        )
    metadata["base_color_size"] = [int(color.size[0]), int(color.size[1])]
    metadata["metallic_roughness_size"] = [int(data.size[0]), int(data.size[1])]
    metadata["pbr_material_verified"] = True
    return metadata


def _stage_finalize(state: dict[str, Any]) -> int:
    _activate_engine_paths()
    import numpy as np
    import torch
    import trimesh
    from PIL import Image, ImageFilter, ImageOps

    payload = state["job"]
    allowed_loads: dict[Path, tuple[int, str]] = {}
    if payload["texture_mode"] == "export_8k":
        if not _verify_file(REALESRGAN_X2, REALESRGAN_X2_BYTES, REALESRGAN_X2_SHA256):
            raise EngineError(
                "model-verification", "RealESRGAN_x2plus changed before 8K export"
            )
        allowed_loads[REALESRGAN_X2] = (REALESRGAN_X2_BYTES, REALESRGAN_X2_SHA256)
    _force_weights_only_torch_load(torch, allowed_loads)
    source_maps = _validate_native_pbr_maps(state)
    paint_obj = Path(state["paint_obj"])
    mesh = trimesh.load(paint_obj, force="mesh", process=False, maintain_order=True)
    uv = getattr(getattr(mesh, "visual", None), "uv", None)
    if uv is None or len(uv) != len(mesh.vertices):
        raise EngineError("pbr-uv", "Hunyuan Paint OBJ has no usable UV coordinates")

    with Image.open(state["paint_base"]) as opened:
        base_color = opened.convert("RGB").copy()
    with Image.open(state["paint_metallic"]) as opened:
        metallic = np.asarray(opened.convert("L"), dtype=np.uint8)
    with Image.open(state["paint_roughness"]) as opened:
        roughness = np.asarray(opened.convert("L"), dtype=np.uint8)
    if metallic.shape != roughness.shape or metallic.shape != (PBR_TEXTURE_SIZE, PBR_TEXTURE_SIZE):
        raise EngineError("pbr-texture", "Metallic and roughness maps have inconsistent sizes")
    packed_array = np.full((PBR_TEXTURE_SIZE, PBR_TEXTURE_SIZE, 3), 255, dtype=np.uint8)
    packed_array[:, :, 1] = roughness
    packed_array[:, :, 2] = metallic
    packed_mr = Image.fromarray(packed_array, mode="RGB")
    base_color.format = "PNG"
    packed_mr.format = "PNG"

    material = trimesh.visual.material.PBRMaterial(
        name="Hunyuan3D_2_1_PBR",
        baseColorTexture=base_color,
        metallicRoughnessTexture=packed_mr,
        metallicFactor=1.0,
        roughnessFactor=1.0,
    )
    mesh.visual = trimesh.visual.TextureVisuals(uv=uv, material=material)
    base = _load_base_worker()
    source_textures = {
        "baseColorTexture": base_color.copy(),
        "metallicRoughnessTexture": packed_mr.copy(),
    }
    runtime = {
        "torch": torch,
        "Image": Image,
        "ImageFilter": ImageFilter,
        "ImageOps": ImageOps,
    }
    texture_mode = payload["texture_mode"]
    _progress(84, "Applying the selected lossless PBR export profile")
    if texture_mode == "native_2k":
        texture_records = base._apply_texture_profile(
            material,
            source_textures,
            color_size=2048,
            data_size=2048,
            sharpen=None,
            runtime=runtime,
        )
        profile = {
            "mode": "native_2k",
            "method": "lossless-lanczos-from-native-4k",
            "base_color_edge": 2048,
            "data_edge": 2048,
            "ai_applied": False,
        }
        color_edge, data_edge = 2048, 2048
    elif texture_mode == "ai_4k":
        # Paint already produced genuine 4K maps.  A second AI pass would add
        # artifacts rather than information, so this is a lossless native copy.
        texture_records = base._apply_texture_profile(
            material,
            source_textures,
            color_size=4096,
            data_size=4096,
            sharpen=None,
            runtime=runtime,
        )
        profile = {
            "mode": "ai_4k",
            "method": "native-hunyuan-paint-4k-lossless",
            "base_color_edge": 4096,
            "data_edge": 4096,
            "ai_applied": False,
            "second_upscale_skipped": True,
        }
        color_edge, data_edge = 4096, 4096
    else:
        job = base.Job(
            image_path=Path(payload["image_path"]),
            output_dir=Path(payload["output_dir"]),
            engine_mode=ENGINE_MODE,
            target=payload["target"],
            texture_mode="export_8k",
            geometry_mode=payload["geometry_mode"],
            hardware_preset=payload["hardware_preset"],
            source_schema_version=3,
        )
        texture_records, profile = base._apply_selected_texture_profile(
            material,
            source_textures,
            job=job,
            hardware={
                "requested": payload["hardware_preset"],
                "resolved": "ultimate",
                "low_vram_mode": False,
                "ai_tile_size": 384,
            },
            runtime=runtime,
        )
        if profile.get("ai_used") is not True:
            ai_error = str(profile.get("ai_error") or "").lower()
            if any(
                marker in ai_error
                for marker in (
                    "outofmemory",
                    "out of memory",
                    "cuda error: memory allocation",
                    "cuda_error_out_of_memory",
                )
            ):
                print(
                    "MJSTAGEOOM|finalize|CUDA memory exhausted during fixed 8K AI restoration",
                    flush=True,
                )
                return 42
            raise EngineError(
                "texture-ai",
                "Ultimate 8K AI restoration failed; no lower-quality fallback was published",
            )
        color_edge, data_edge = 8192, 4096

    _progress(92, "Exporting albedo plus packed metallic-roughness as one GLB")
    mesh.export(state["final_glb"], include_normals=True)
    glb_path = Path(state["final_glb"])
    glb_info = _validate_pbr_glb(
        glb_path, expected_color_edge=color_edge, expected_data_edge=data_edge
    )
    report = {
        "device": "cuda",
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_memory_bytes": (
            int(torch.cuda.get_device_properties(0).total_memory)
            if torch.cuda.is_available()
            else 0
        ),
        "torch_version": str(torch.__version__),
        "geometry_faces": int(len(mesh.faces)),
        "geometry_faces_target": (
            ROBLOX_READY_FACES
            if payload["geometry_mode"] == "target_ready"
            else ROBLOX_MASTER_FACES
            if payload["geometry_mode"] == "max_detail"
            else None
        ),
        "native_pbr_maps": source_maps,
        "pbr_channel_layout": {
            "base_color": "separate lossless PNG",
            "metallic_roughness_green": "roughness",
            "metallic_roughness_blue": "metallic",
        },
        "texture_enhancement": profile,
        "texture_records": texture_records,
        "glb_stats": glb_info,
    }
    _atomic_json(Path(state["staging"]) / "stage-report.json", report)
    _progress(96, "Verified lossless PBR GLB and texture dimensions")
    return 0


def _weights_only_guard_self_test() -> bool:
    class Probe:
        def __init__(self) -> None:
            self.source: Any = None

        def load(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            self.source = args[0] if args else kwargs.get("f")
            return kwargs

    probe = Probe()
    own_path = Path(__file__).resolve(strict=True)
    try:
        _force_weights_only_torch_load(
            probe, {own_path: (own_path.stat().st_size, _sha256(own_path))}
        )
        # Keep this as a Path object: pathlib.Path.name must never be mistaken
        # for the name of an already-open file handle.
        result = probe.load(own_path)
        if (
            result.get("weights_only") is not True
            or not hasattr(probe.source, "read")
            or not getattr(probe.source, "closed", False)
        ):
            return False
        try:
            probe.load(str(own_path), weights_only=False)
            return False
        except RuntimeError:
            pass
        mmap_result = probe.load(str(own_path), mmap=True)
        if (
            mmap_result.get("weights_only") is not True
            or "mmap" in mmap_result
            or not hasattr(probe.source, "read")
            or not getattr(probe.source, "closed", False)
        ):
            return False
        try:
            probe.load(str(BASE_WORKER_PATH))
            return False
        except RuntimeError:
            pass
        return True
    except Exception:
        return False


def _secret_redaction_self_test() -> bool:
    probe = _sanitize(
        "https://alice:hunter2@example.invalid/path?token=verysecret "
        "Bearer abcdefghijklmnop hf_abcdefghijklmnop"
    ).lower()
    return all(
        secret not in probe
        for secret in (
            "alice",
            "hunter2",
            "verysecret",
            "abcdefghijklmnop",
            "hf_",
        )
    )


def _shape_resume_identity_self_test() -> bool:
    payload = {
        "target": "roblox",
        "geometry_mode": "max_detail",
    }
    try:
        first = _shape_resume_identity_from_hash("ab" * 32, payload)
        repeated = _shape_resume_identity_from_hash("ab" * 32, dict(payload))
        changed = _shape_resume_identity_from_hash(
            "ab" * 32, {**payload, "geometry_mode": "original"}
        )
        return (
            first == repeated
            and first["key"] != changed["key"]
            and first["identity"]["source_commit"] == SOURCE_COMMIT
            and first["identity"]["model_revision"] == MODEL_REVISION
            and first["identity"]["inference_steps"] == SHAPE_INFERENCE_STEPS
            and first["identity"]["octree_resolution"] == SHAPE_OCTREE_RESOLUTION
        )
    except Exception:
        return False


def _stage_process_result_self_test() -> bool:
    result = StageProcessResult(7, ("specific-code", "specific message"))
    return (
        isinstance(result, int)
        and int(result) == 7
        and result != 0
        and result.child_error == ("specific-code", "specific message")
    )


def _paint_low_vram_runtime_self_test() -> bool:
    """Exercise the CUDA ownership lifecycle with model-free stand-ins."""

    import weakref

    class FakeCuda:
        def __init__(self) -> None:
            self.collections = 0

        @staticmethod
        def is_available() -> bool:
            return True

        def empty_cache(self) -> None:
            self.collections += 1

    class FakeTorch:
        def __init__(self) -> None:
            self.cuda = FakeCuda()

        @staticmethod
        def device(value: str) -> str:
            return value

        @staticmethod
        def zeros_like(value: "FakeTensor") -> "FakeTensor":
            return FakeTensor([0.0 for _ in value.values])

        @staticmethod
        def clamp(value: "FakeTensor", *, min: float) -> "FakeTensor":
            return FakeTensor([max(item, min) for item in value.values])

        @staticmethod
        def cat(values: list["FakeBatch"], dim: int = 0) -> "FakeBatch":
            if dim != 0:
                raise ValueError("fake batch supports only dimension zero")
            labels: list[str] = []
            for value in values:
                labels.extend(value.labels)
            return FakeBatch(labels)

    class FakeScalar:
        """Mimic tensor scalar division, including 0/0 -> NaN."""

        def __init__(self, value: float) -> None:
            self.value = float(value)

        def __truediv__(self, other: Any) -> float:
            denominator = other.value if isinstance(other, FakeScalar) else float(other)
            if denominator == 0:
                return float("nan")
            return self.value / denominator

    class FakeTensor:
        """Tiny pure-stdlib tensor facade for the streaming arithmetic."""

        def __init__(self, values: Any) -> None:
            self.values = [float(value) for value in values]

        def __pow__(self, exponent: float) -> "FakeTensor":
            return FakeTensor(value**exponent for value in self.values)

        def __mul__(self, other: Any) -> "FakeTensor":
            values = other.values if isinstance(other, FakeTensor) else other
            if isinstance(values, list):
                return FakeTensor(
                    left * right for left, right in zip(self.values, values)
                )
            return FakeTensor(value * values for value in self.values)

        def __rmul__(self, other: Any) -> "FakeTensor":
            return self * other

        def __truediv__(self, other: Any) -> "FakeTensor":
            values = other.values if isinstance(other, FakeTensor) else other
            if isinstance(values, list):
                return FakeTensor(
                    left / right for left, right in zip(self.values, values)
                )
            return FakeTensor(value / values for value in self.values)

        def __gt__(self, other: Any) -> "FakeTensor":
            values = other.values if isinstance(other, FakeTensor) else other
            if isinstance(values, list):
                return FakeTensor(
                    1.0 if left > right else 0.0
                    for left, right in zip(self.values, values)
                )
            return FakeTensor(1.0 if value > values else 0.0 for value in self.values)

        def sum(self) -> Any:
            return FakeScalar(sum(self.values))

        def add_(self, other: "FakeTensor") -> "FakeTensor":
            self.values = [
                left + right for left, right in zip(self.values, other.values)
            ]
            return self

    class FakeBatch:
        """Tensor-like batch used to audit CFG slicing and concatenation."""

        def __init__(self, labels: Any) -> None:
            self.labels = [str(label) for label in labels]
            self.shape = (len(self.labels), 1)
            self.moves: list[str] = []

        def __getitem__(self, key: Any) -> "FakeBatch":
            selected = self.labels[key]
            if isinstance(selected, list):
                return FakeBatch(selected)
            return FakeBatch([selected])

        def to(self, device: str) -> "FakeBatch":
            self.moves.append(str(device))
            return self

    class FakeModule:
        def __init__(self) -> None:
            self.moves: list[str] = []

        def to(self, device: str) -> "FakeModule":
            self.moves.append(str(device))
            return self

    class FakeDino(FakeModule):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def __call__(self, _image: Any) -> FakeBatch:
            self.calls += 1
            return FakeBatch(["dino"])

    class Basic2p5DTransformerBlock:
        def __init__(self) -> None:
            self.chunk_calls: list[tuple[int, int]] = []

        def set_chunk_feed_forward(self, chunk_size: int, dim: int = 0) -> None:
            self.chunk_calls.append((int(chunk_size), int(dim)))

    class FakeMainUnet(FakeModule):
        def __init__(self) -> None:
            super().__init__()
            self.blocks = [Basic2p5DTransformerBlock() for _ in range(2)]

        def modules(self) -> list[Any]:
            return [self, *self.blocks]

    class FakeReferenceUnet(FakeModule):
        def __init__(self) -> None:
            super().__init__()
            self.block = Basic2p5DTransformerBlock()
            self.calls = 0

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self.forward(*args, **kwargs)

        def forward(self, *_args: Any, **kwargs: Any) -> tuple[FakeBatch]:
            self.calls += 1
            cross_attention = kwargs.get("cross_attention_kwargs")
            if not isinstance(cross_attention, dict):
                raise RuntimeError("missing fake reference cross-attention ABI")
            condition_embed_dict = cross_attention.get("condition_embed_dict")
            if (
                cross_attention.get("mode") != "w"
                or not isinstance(condition_embed_dict, dict)
            ):
                raise RuntimeError("missing fake reference cache output")
            condition_embed_dict["reference"] = "cached"
            return (FakeBatch(["dual-output"]),)

    class FakeUpsampler:
        def __init__(self) -> None:
            self.model = FakeModule()
            self.device = "cuda"

    class FakeSuperModel:
        def __init__(self) -> None:
            self.upsampler = FakeUpsampler()

        @staticmethod
        def __call__(image: Any) -> Any:
            return image

    class FakeVae(FakeModule):
        def __init__(self) -> None:
            super().__init__()
            self.slicing_enabled = False
            self.decode_calls = 0

        def enable_slicing(self) -> None:
            self.slicing_enabled = True

        def decode(self, latent: Any, **_kwargs: Any) -> tuple[Any]:
            self.decode_calls += 1
            return (latent,)

    class FakeUnet(FakeModule):
        use_dino = True
        use_learned_text_clip = True
        use_dual_stream = True
        use_ra = True

        def __init__(self) -> None:
            super().__init__()
            self.unet = FakeMainUnet()
            self.unet_dual = FakeReferenceUnet()
            self.calls: list[tuple[str, str, str, bool, bool]] = []

        def __call__(self, *args: Any, **kwargs: Any) -> Any:
            return self.forward(*args, **kwargs)

        def forward(
            self,
            sample: FakeBatch,
            _timestep: Any,
            encoder_hidden_states: FakeBatch,
            *_args: Any,
            **kwargs: Any,
        ) -> tuple[FakeBatch]:
            cache = kwargs.get("cache")
            if not isinstance(cache, dict):
                raise RuntimeError("missing fake branch cache")
            ref_latents = kwargs.get("ref_latents")
            dino_states = kwargs.get("dino_hidden_states")
            if not isinstance(ref_latents, FakeBatch) or not isinstance(
                dino_states, FakeBatch
            ):
                raise RuntimeError("missing fake batched conditions")
            cached = "condition_embed_dict" in cache
            if not cached:
                condition_embed_dict: dict[str, Any] = {}
                self.unet_dual(
                    FakeBatch(["reference"]),
                    cross_attention_kwargs={
                        "mode": "w",
                        "num_in_batch": 1,
                        "condition_embed_dict": condition_embed_dict,
                    },
                )
                cache["condition_embed_dict"] = condition_embed_dict
            cache.setdefault("position_voxel_indices", object())
            cache.setdefault(
                "dino_hidden_states_proj", {"source": dino_states.labels[0]}
            )
            self.calls.append(
                (
                    sample.labels[0],
                    encoder_hidden_states.labels[0],
                    dino_states.labels[0],
                    cached,
                    bool(self.unet_dual.moves and self.unet_dual.moves[-1] == "cpu"),
                )
            )
            return (FakeBatch([f"out-{sample.labels[0]}"]),)

    class FakeDiffusionPipeline:
        def __init__(self, *, pipeline_slicing: bool = True) -> None:
            self.unet = FakeUnet()
            self.vae = FakeVae()
            self.image_encoder = FakeModule()
            self.text_encoder = FakeModule()
            self.slicing_enabled = False
            self.noise_results: list[list[str]] = []
            self.prompt_moves: list[str] = []
            self.cfg_cache: dict[str, Any] = {}
            self.cfg_branch_snapshot: list[dict[str, Any]] | None = None
            if not pipeline_slicing:
                self.enable_vae_slicing = None

        def enable_vae_slicing(self) -> None:
            self.slicing_enabled = True

        def denoise(self, *_args: Any, **kwargs: Any) -> dict[str, list[str]]:
            prompt = kwargs.get("prompt_embeds")
            if isinstance(prompt, FakeBatch):
                self.prompt_moves = list(prompt.moves)
            cache = self.cfg_cache
            for _step in range(2):
                result = self.unet(
                    FakeBatch(["uncond", "reference", "full"]),
                    1,
                    FakeBatch(["text-u", "text-r", "text-f"]),
                    ref_latents=FakeBatch(["ref-u", "ref-r", "ref-f"]),
                    dino_hidden_states=FakeBatch(["dino-u", "dino-r", "dino-f"]),
                    embeds_normal=FakeBatch(["normal-u", "normal-r", "normal-f"]),
                    embeds_position=FakeBatch(["position-u", "position-r", "position-f"]),
                    position_maps=FakeBatch(["map-u", "map-r", "map-f"]),
                    ref_scale=FakeBatch(["scale-u", "scale-r", "scale-f"]),
                    cache=cache,
                    return_dict=False,
                )
                self.noise_results.append(result[0].labels)
            branches = self.cfg_cache.get(
                _LowVramPaintDiffusionSchedule._BRANCH_CACHE_KEY
            )
            if isinstance(branches, list):
                self.cfg_branch_snapshot = branches
            self.vae.decode(FakeBatch(["latent"]))
            return {"albedo": ["a0", "a1"], "mr": ["m0", "m1"]}

    class FakeMultiviewModel:
        def __init__(
            self, super_model: FakeSuperModel, diffusion: FakeDiffusionPipeline
        ) -> None:
            self.pipeline = diffusion
            self.dino_v2 = FakeDino()
            self.super_model = super_model
            self.saw_cpu_super_model = False

        def __call__(self, *_args: Any, **_kwargs: Any) -> dict[str, list[str]]:
            self.saw_cpu_super_model = self.super_model.upsampler.device == "cpu"
            dino_states = self.dino_v2("image")
            if not isinstance(dino_states, FakeBatch):
                raise RuntimeError("fake DINO result changed")
            return self.pipeline.denoise(
                prompt_embeds=FakeBatch(["prompt"]),
                negative_prompt_embeds=FakeBatch(["negative"]),
            )

    class FakeViewProcessor:
        pass

    class FakePaintPipeline:
        def __init__(self, *, pipeline_slicing: bool = True) -> None:
            super_model = FakeSuperModel()
            diffusion = FakeDiffusionPipeline(pipeline_slicing=pipeline_slicing)
            self.multiview = FakeMultiviewModel(super_model, diffusion)
            self.diffusion = self.multiview.pipeline
            self.super_model = super_model
            self.models = {
                "super_model": super_model,
                "multiview_model": self.multiview,
            }
            self.view_processor = FakeViewProcessor()

    try:
        if (
            SHAPE_INFERENCE_STEPS,
            PAINT_MAX_VIEWS,
            PAINT_VIEW_RESOLUTION,
            PBR_TEXTURE_SIZE,
            _LowVramPaintDiffusionSchedule._MAIN_UNET_FF_CHUNK,
        ) != (50, 12, 768, 4096, 12):
            return False
        torch_module = FakeTorch()
        pipeline = FakePaintPipeline()
        diffusion = pipeline.diffusion
        original_multiview = pipeline.multiview
        original_super = pipeline.super_model
        original_unet = diffusion.unet
        original_vae = diffusion.vae
        original_dino = original_multiview.dino_v2
        original_text_encoder = diffusion.text_encoder
        original_image_encoder = diffusion.image_encoder
        _install_low_vram_paint_runtime(
            pipeline, torch_module, emit_progress=False
        )
        multiview_wrapper = pipeline.models["multiview_model"]
        super_wrapper = pipeline.models["super_model"]
        result = multiview_wrapper("images", "conditions")
        if (
            result != {"albedo": ["a0", "a1"], "mr": ["m0", "m1"]}
            or not diffusion.slicing_enabled
            or not original_multiview.saw_cpu_super_model
            or original_multiview.pipeline is not None
            or original_multiview.dino_v2 is not None
            or any(
                getattr(diffusion, name) is not None
                for name in ("unet", "vae", "image_encoder", "text_encoder")
            )
            or original_super.upsampler.model.moves != ["cpu", "cuda"]
            or original_dino.calls != 1
            or original_text_encoder.moves != ["cpu"]
            or original_image_encoder.moves != ["cpu"]
            or original_vae.moves != ["cpu", "cuda", "cuda", "cpu", "cuda"]
            or original_unet.moves != ["cpu", "cuda", "cpu"]
            or original_unet.unet_dual.moves != ["cpu"]
            or original_unet.unet_dual.calls != 1
            or [block.chunk_calls for block in original_unet.unet.blocks]
            != [[(12, 0)], [(12, 0)]]
            or original_unet.unet_dual.block.chunk_calls != []
            or original_vae.decode_calls != 1
            or diffusion.prompt_moves != ["cuda"]
            or diffusion.noise_results
            != [
                ["out-uncond", "out-reference", "out-full"],
                ["out-uncond", "out-reference", "out-full"],
            ]
            or original_unet.calls
            != [
                ("uncond", "text-u", "dino-u", False, True),
                ("reference", "text-r", "dino-r", True, True),
                ("full", "text-f", "dino-f", True, True),
                ("uncond", "text-u", "dino-u", True, True),
                ("reference", "text-r", "dino-r", True, True),
                ("full", "text-f", "dino-f", True, True),
            ]
        ):
            return False
        branch_caches = diffusion.cfg_branch_snapshot
        if not isinstance(branch_caches, list) or len(branch_caches) != 3:
            return False
        if not (
            branch_caches[0]["condition_embed_dict"]
            is branch_caches[1]["condition_embed_dict"]
            is branch_caches[2]["condition_embed_dict"]
            and branch_caches[0]["position_voxel_indices"]
            is branch_caches[1]["position_voxel_indices"]
            is branch_caches[2]["position_voxel_indices"]
            and branch_caches[0]["dino_hidden_states_proj"]
            is branch_caches[1]["dino_hidden_states_proj"]
            and branch_caches[2]["dino_hidden_states_proj"]
            is not branch_caches[0]["dino_hidden_states_proj"]
            and branch_caches[2]["dino_hidden_states_proj"]["source"] == "dino-f"
            and diffusion.cfg_cache == {}
        ):
            return False
        try:
            original_unet.unet_dual(
                FakeBatch(["second-reference"]),
                cross_attention_kwargs={
                    "mode": "w",
                    "num_in_batch": 1,
                    "condition_embed_dict": {},
                },
            )
            return False
        except EngineError:
            pass
        try:
            multiview_wrapper("images", "conditions")
            return False
        except EngineError:
            pass
        if [super_wrapper(value) for value in ("a0", "m0", "a1", "m1")] != [
            "a0",
            "m0",
            "a1",
            "m1",
        ]:
            return False
        lifecycle_valid = (
            super_wrapper._released
            and original_super.upsampler.model is None
            and torch_module.cuda.collections >= 3
            and not bool(_streaming_projection_is_redundant(99, 100))
            and bool(_streaming_projection_is_redundant(100, 101))
            and callable(pipeline.view_processor.bake_from_multiview)
        )
        try:
            super_wrapper("extra")
            return False
        except EngineError:
            pass
        if not lifecycle_valid:
            return False

        fallback_pipeline = FakePaintPipeline(pipeline_slicing=False)
        fallback_vae = fallback_pipeline.diffusion.vae
        _install_low_vram_paint_runtime(
            fallback_pipeline, FakeTorch(), emit_progress=False
        )
        if not fallback_vae.slicing_enabled:
            return False

        class FakeRender:
            def __init__(self) -> None:
                self.calls = 0
                self.overlapped_projection = False
                self._previous: list[Any] = []
                self._projections = (
                    ([1, 2, 0, 0], [1, 1, 0, 0]),
                    ([0, 20, 30, 0], [0, 1, 1, 0]),
                    ([100, 100, 100, 100], [1, 1, 0, 0]),
                    ([9, 9, 9, 9], [0, 0, 0, 0]),
                )

            def back_project(
                self, _view: Any, _elevation: Any, _azimuth: Any
            ) -> tuple[FakeTensor, FakeTensor, FakeTensor]:
                if any(reference() is not None for reference in self._previous):
                    self.overlapped_projection = True
                texture_values, cosine_values = self._projections[self.calls]
                self.calls += 1
                texture = FakeTensor(texture_values)
                cosine = FakeTensor(cosine_values)
                boundary = FakeTensor([0, 0, 0, 0])
                self._previous = [
                    weakref.ref(texture),
                    weakref.ref(cosine),
                    weakref.ref(boundary),
                ]
                return texture, cosine, boundary

        class FakeBakeConfig:
            bake_exp = 2

        class FakeStreamingViewProcessor:
            def __init__(self) -> None:
                self.render = FakeRender()
                self.config = FakeBakeConfig()

        streaming = FakeStreamingViewProcessor()
        streaming_torch = FakeTorch()
        _install_streaming_pbr_bake(
            streaming, streaming_torch, emit_progress=False
        )
        weights = [1.0, 0.5, 1.0, 7.0]
        texture_values = (
            [1, 2, 0, 0],
            [0, 20, 30, 0],
            [100, 100, 100, 100],
            [9, 9, 9, 9],
        )
        cosine_values = (
            [1, 1, 0, 0],
            [0, 1, 1, 0],
            [1, 1, 0, 0],
            [0, 0, 0, 0],
        )
        reference_texture = [0.0, 0.0, 0.0, 0.0]
        reference_trust = [0.0, 0.0, 0.0, 0.0]
        for projected, cosine, weight in zip(
            texture_values, cosine_values, weights
        ):
            weighted = [
                weight * (value**FakeBakeConfig.bake_exp) for value in cosine
            ]
            view_sum = sum(value > 0 for value in weighted)
            painted_sum = sum(
                value > 0 and trust > 0
                for value, trust in zip(weighted, reference_trust)
            )
            ratio = painted_sum / view_sum if view_sum else float("nan")
            if ratio > 0.99:
                continue
            reference_texture = [
                total + color * contribution
                for total, color, contribution in zip(
                    reference_texture, projected, weighted
                )
            ]
            reference_trust = [
                total + contribution
                for total, contribution in zip(reference_trust, weighted)
            ]
        reference_texture = [
            value / max(trust, 1e-8)
            for value, trust in zip(reference_texture, reference_trust)
        ]
        streamed_texture, streamed_mask = streaming.bake_from_multiview(
            ["v0", "v1", "v2", "v3"],
            [0, 1, 2, 3],
            [10, 11, 12, 13],
            weights,
        )
        return (
            streaming.render.calls == 4
            and not streaming.render.overlapped_projection
            and all(
                abs(actual - expected) < 1e-12
                for actual, expected in zip(
                    streamed_texture.values, reference_texture
                )
            )
            and [bool(value) for value in streamed_mask.values]
            == [trust > 1e-8 for trust in reference_trust]
            and all(
                abs(actual - expected) < 1e-12
                for actual, expected in zip(
                    streamed_texture.values, [1.0, 8.0, 30.0, 0.0]
                )
            )
        )
    except Exception:
        return False


def _self_test() -> int:
    x4_valid = _verify_file(REALESRGAN_X4, REALESRGAN_X4_BYTES, REALESRGAN_X4_SHA256)
    try:
        _validate_engine_pack()
        engine_pack_valid = True
    except Exception:
        engine_pack_valid = False
    checks = {
        "source_commit": len(SOURCE_COMMIT) == 40,
        "model_revision": len(MODEL_REVISION) == 40,
        "dino_revision": len(DINO_REVISION) == 40,
        "runtime_abi_contract": (
            REQUIRED_PYTHON == (3, 11, 9)
            and REQUIRED_TORCH == "2.5.1+cu124"
            and REQUIRED_TORCHVISION == "0.20.1+cu124"
            and REQUIRED_CUDA_RUNTIME == "12.4"
        ),
        "ultimate_shape": (
            SHAPE_INFERENCE_STEPS == 50
            and SHAPE_GUIDANCE_SCALE == 5.0
            and SHAPE_OCTREE_RESOLUTION == 512
            and SHAPE_NUM_CHUNKS == 8000
        ),
        "ultimate_paint": (
            PAINT_MAX_VIEWS == 12
            and PAINT_VIEW_RESOLUTION == 768
            and PBR_TEXTURE_SIZE == 4096
        ),
        "pinned_weights": len(PINNED_MODEL_WEIGHTS) == 6,
        "remote_python_excluded": not any(path.endswith(".py") for path in MODEL_REQUIRED_FILES),
        "weights_only_allowlist_guard": _weights_only_guard_self_test(),
        "secret_redaction": _secret_redaction_self_test(),
        "shape_resume_identity": _shape_resume_identity_self_test(),
        "stage_error_propagation": _stage_process_result_self_test(),
        "paint_low_vram_runtime": _paint_low_vram_runtime_self_test(),
        "download_retries": MAX_DOWNLOAD_ATTEMPTS >= 3,
        "download_stall_watchdog": DOWNLOAD_STALL_SECONDS >= 60,
        "base_worker": BASE_WORKER_PATH.is_file(),
        "notice": (ENGINE_ROOT / "NOTICE.txt").is_file(),
        "realesrgan_x4_verified": x4_valid,
        "engine_pack_verified": engine_pack_valid,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        _error("hunyuan21-self-test", "Failed: " + ", ".join(failed))
        return 1
    print("MJHUNYUAN21SELFTEST|OK|1", flush=True)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mujassam Hunyuan3D 2.1 Ultimate PBR worker")
    parser.add_argument("--job", help="Mujassam schema-v3 job JSON")
    parser.add_argument("--stage", choices=("shape", "paint", "finalize"))
    parser.add_argument("--state", help="Trusted stage state JSON")
    parser.add_argument(
        "--download-repository", choices=(MODEL_REPOSITORY, DINO_REPOSITORY)
    )
    parser.add_argument("--download-revision")
    parser.add_argument("--download-dir")
    parser.add_argument("--rasterizer-self-test", action="store_true")
    parser.add_argument(
        "--texture-mode", choices=("native_2k", "ai_4k", "export_8k")
    )
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.self_test:
        return _self_test()
    try:
        if args.rasterizer_self_test:
            if not args.texture_mode:
                raise EngineError(
                    "usage", "--texture-mode is required with --rasterizer-self-test"
                )
            _validate_engine_pack()
            _validate_license_acceptance()
            _validate_base_runtime(args.texture_mode)
            _rasterizer_cuda_preflight()
            print("MJHUNYUAN21RASTERIZER|OK|1", flush=True)
            return 0
        if args.download_repository:
            if not args.download_revision or not args.download_dir:
                raise EngineError("usage", "Download revision and directory are required")
            _download_snapshot_once(
                args.download_repository,
                args.download_revision,
                Path(args.download_dir),
            )
            return 0
        if args.stage:
            if not args.state:
                raise EngineError("usage", "--state is required with --stage")
            # Stage entry points are public CLI modes.  Repeat both gates here
            # so a direct invocation cannot bypass the orchestrator's legal or
            # reviewed-engine checks before loading any checkpoint.
            _validate_engine_pack()
            _validate_license_acceptance()
            state = _load_state(Path(args.state))
            _validate_base_runtime(state["job"]["texture_mode"])
            if args.stage == "shape":
                return _stage_shape(state)
            if args.stage == "paint":
                return _stage_paint(state)
            return _stage_finalize(state)
        if not args.job:
            raise EngineError("usage", "--job is required")
        _orchestrate(Path(args.job))
        return 0
    except EngineError as exc:
        _error(exc.code, exc)
        return 1
    except KeyboardInterrupt:
        _error("cancelled", "The operation was cancelled")
        return 130
    except Exception as exc:
        if args.stage:
            detail = str(exc).lower()
            name = type(exc).__name__.lower()
            if (
                "outofmemory" in name
                or "out of memory" in detail
                or "cuda error: memory allocation" in detail
                or "cuda_error_out_of_memory" in detail
            ):
                print(f"MJSTAGEOOM|{args.stage}|CUDA memory exhausted", flush=True)
                return 42
        if os.environ.get("MUJASSAM_DEBUG") == "1":
            traceback.print_exc(file=sys.stderr)
        _error("hunyuan21-internal", f"Unexpected Hunyuan3D 2.1 failure: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
