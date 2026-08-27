#!/usr/bin/env python3
"""Portable SPAR3D worker used by MujassamAI.

The worker accepts one JSON request file and communicates with the launcher only
through the line-oriented MJ* protocol on stdout.  The gated SPAR3D model and
vendor source are always loaded from the application bundle.  DINOv2 is
initialized locally from the copy embedded in the SPAR3D checkpoint, with a
pinned public download retained only as a compatibility fallback.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 3
SUPPORTED_SCHEMA_VERSIONS = {1, 2, 3}
FOREGROUND_RATIO = 1.3
MAX_JOB_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 200 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TARGETS = {"roblox", "unreal"}
ENGINE_MODES = {
    "hunyuan3d_2mini_low_vram",
    "hunyuan3d_2_1_pbr",
    "spar3d_legacy",
}
ENGINE_DISPLAY_NAMES = {
    "hunyuan3d_2mini_low_vram": "Hunyuan3D 2mini Low-VRAM",
    "hunyuan3d_2_1_pbr": "Hunyuan3D 2.1 PBR",
    "spar3d_legacy": "SPAR3D",
}
TEXTURE_MODES = {
    "native_1k": 1024,  # legacy launcher compatibility only
    "native_2k": 2048,
    "ai_4k": 4096,
    "export_8k": 8192,
}
HARDWARE_PRESETS = {"auto", "vram_8gb", "vram_16gb_plus"}
GEOMETRY_MODES = {"target_ready", "max_detail", "original"}
ROBLOX_READY_VERTEX_TARGET = 9_500
SPAR_BAKE_RESOLUTION = 2048
HIGH_VRAM_THRESHOLD_BYTES = 12 * 1024**3

APP_ROOT = Path(__file__).resolve().parent
BUNDLE_ROOT = APP_ROOT.parent.resolve()
VENDOR_ROOT = APP_ROOT / "vendor" / "stable-point-aware-3d"
MODEL_ROOT = BUNDLE_ROOT / "models" / "spar3d"
AI_TEXTURE_MODEL = BUNDLE_ROOT / "models" / "realesrgan" / "RealESRGAN_x2plus.pth"
BACKGROUND_CHECKPOINT = (
    BUNDLE_ROOT / "models" / "transparent-background" / "ckpt_base.pth"
)
HUNYUAN2_WORKER = APP_ROOT / "engines" / "hunyuan2" / "hunyuan2_worker.py"

MODEL_CONFIG_NAME = "config.yaml"
MODEL_WEIGHT_NAME = "model.safetensors"
DINO_REPOSITORY = "facebook/dinov2-large"
DINO_REVISION = "0ff9d1340c9524c60f3f03e8573c57a1f8197f24"
DINO_WEIGHT_SHA256 = "399fba97a95f22c36834418bc69373364a99af3a1153da1c0fb31db567c92e23"
HUB_DOWNLOAD_TIMEOUT_SECONDS = 300
HUB_ETAG_TIMEOUT_SECONDS = 60
MAX_SAFETENSORS_HEADER_BYTES = 64 * 1024 * 1024
DINO_CONFIG_KWARGS = {
    "attention_probs_dropout_prob": 0.0,
    "drop_path_rate": 0.0,
    "hidden_act": "gelu",
    "hidden_dropout_prob": 0.0,
    "hidden_size": 1024,
    "image_size": 518,
    "initializer_range": 0.02,
    "layer_norm_eps": 1e-6,
    "layerscale_value": 1.0,
    "mlp_ratio": 4,
    "num_attention_heads": 16,
    "num_channels": 3,
    "num_hidden_layers": 24,
    "patch_size": 14,
    "qkv_bias": True,
    "use_swiglu_ffn": False,
}

_SENSITIVE_PATTERN = re.compile(
    r"(?i)(?:hf_[a-z0-9]{12,}|bearer\s+[a-z0-9._~+/=-]{12,}|"
    r"(?:token|secret|password|authorization)\s*[:=]\s*[^\s|]+)"
)


class WorkerError(RuntimeError):
    """An expected, user-presentable worker failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Job:
    image_path: Path
    output_dir: Path
    engine_mode: str
    target: str
    texture_mode: str
    geometry_mode: str
    hardware_preset: str
    source_schema_version: int


@dataclass(frozen=True)
class BundlePaths:
    vendor_root: Path
    model_root: Path
    model_config: Path
    model_weights: Path
    background_checkpoint: Path


def _sanitize(value: object, limit: int = 900) -> str:
    text = str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
    text = text.replace("|", "/")
    text = _SENSITIVE_PATTERN.sub("[redacted]", text)
    text = " ".join(text.split())
    return text[:limit] or "unknown error"


def _emit_progress(percent: int, message: str) -> None:
    percent = max(0, min(100, int(percent)))
    print(f"MJPROGRESS|{percent}|{_sanitize(message, 300)}", flush=True)


def _emit_artifact(path: Path) -> None:
    print(f"MJARTIFACT|{_sanitize(path, 1800)}", flush=True)


def _emit_error(code: str, message: object) -> None:
    print(f"MJERROR|{_sanitize(code, 80)}|{_sanitize(message)}", flush=True)


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _require_bundled_path(path: Path, *, kind: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkerError("bundle-missing", f"Missing bundled {kind}: {path}") from exc
    if not _is_within(resolved, BUNDLE_ROOT):
        raise WorkerError(
            "bundle-path-invalid", f"Bundled {kind} resolves outside the application"
        )
    return resolved


def _validate_bundle() -> BundlePaths:
    vendor_root = _require_bundled_path(VENDOR_ROOT, kind="SPAR3D vendor directory")
    model_root = _require_bundled_path(MODEL_ROOT, kind="SPAR3D model directory")
    model_config = _require_bundled_path(
        MODEL_ROOT / MODEL_CONFIG_NAME, kind="SPAR3D config"
    )
    model_weights = _require_bundled_path(
        MODEL_ROOT / MODEL_WEIGHT_NAME, kind="SPAR3D weights"
    )
    background_checkpoint = _require_bundled_path(
        BACKGROUND_CHECKPOINT, kind="background-removal checkpoint"
    )

    required_vendor_entries = (vendor_root / "spar3d", vendor_root / "run.py")
    if not required_vendor_entries[0].is_dir() or not required_vendor_entries[1].is_file():
        raise WorkerError(
            "vendor-invalid", "The bundled stable-point-aware-3d source is incomplete"
        )
    for file_path, label in (
        (model_config, "SPAR3D config"),
        (model_weights, "SPAR3D weights"),
        (background_checkpoint, "background-removal checkpoint"),
    ):
        if not file_path.is_file() or file_path.stat().st_size <= 0:
            raise WorkerError("bundle-invalid", f"Bundled {label} is empty or invalid")

    return BundlePaths(
        vendor_root=vendor_root,
        model_root=model_root,
        model_config=model_config,
        model_weights=model_weights,
        background_checkpoint=background_checkpoint,
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON property: {key}")
        result[key] = value
    return result


def _load_request(path: Path) -> dict[str, Any]:
    try:
        request_path = path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkerError("request-not-found", "The JSON request file was not found") from exc
    if not request_path.is_file():
        raise WorkerError("request-invalid", "The JSON request path is not a regular file")
    size = request_path.stat().st_size
    if size <= 1 or size > MAX_JOB_BYTES:
        raise WorkerError("request-size", "The JSON request has an invalid size")
    try:
        raw = request_path.read_text(encoding="utf-8-sig")
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorkerError("request-json", f"Invalid JSON request: {exc}") from exc
    if not isinstance(value, dict):
        raise WorkerError("request-schema", "The JSON request must be an object")
    return value


def _require_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise WorkerError("request-schema", f"'{key}' must be a non-empty string")
    return value.strip()


def _validate_image_path(value: str) -> Path:
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkerError("image-not-found", "The source image was not found") from exc
    if not resolved.is_file():
        raise WorkerError("image-invalid", "The source image is not a regular file")
    if resolved.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise WorkerError("image-format", "Supported image formats are PNG, JPEG, and WebP")
    size = resolved.stat().st_size
    if size <= 0 or size > MAX_IMAGE_BYTES:
        raise WorkerError("image-size", "The source image has an invalid file size")
    return resolved


def _validate_output_root(value: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise WorkerError("output-path", "output_dir must be an absolute path")
    try:
        preflight = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise WorkerError("output-path", f"Cannot resolve output_dir: {exc}") from exc
    if preflight == Path(preflight.anchor) or _is_within(preflight, BUNDLE_ROOT):
        raise WorkerError(
            "output-path", "output_dir must be outside the application bundle and filesystem root"
        )
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise WorkerError("output-path", f"Cannot create output_dir: {exc}") from exc
    if not resolved.is_dir():
        raise WorkerError("output-path", "output_dir is not a directory")
    if resolved == Path(resolved.anchor) or _is_within(resolved, BUNDLE_ROOT):
        raise WorkerError(
            "output-path", "output_dir must be outside the application bundle and filesystem root"
        )
    test_path = resolved / f".mujassam-write-test-{uuid.uuid4().hex}"
    try:
        test_path.touch(mode=0o600, exist_ok=False)
        test_path.unlink()
    except OSError as exc:
        raise WorkerError("output-permission", f"output_dir is not writable: {exc}") from exc
    return resolved


def _parse_job(payload: dict[str, Any]) -> Job:
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise WorkerError(
            "schema-version",
            f"schema_version must be one of {sorted(SUPPORTED_SCHEMA_VERSIONS)}",
        )

    common = {"schema_version", "image_path", "output_dir", "target"}
    if schema_version == 1:
        required = common | {"texture_resolution", "roblox_quality"}
    elif schema_version == 2:
        required = common | {"texture_mode", "geometry_mode", "hardware_preset"}
    else:
        required = common | {
            "engine_mode",
            "texture_mode",
            "geometry_mode",
            "hardware_preset",
        }
    missing = sorted(required - payload.keys())
    unknown = sorted(payload.keys() - required)
    if missing:
        raise WorkerError("request-schema", f"Missing properties: {', '.join(missing)}")
    if unknown:
        raise WorkerError("request-schema", f"Unknown properties: {', '.join(unknown)}")

    target = _require_string(payload, "target")
    if target not in TARGETS:
        raise WorkerError("request-schema", "target must be 'roblox' or 'unreal'")

    if schema_version == 1:
        texture_resolution = payload["texture_resolution"]
        if type(texture_resolution) is not int or texture_resolution not in {1024, 2048}:
            raise WorkerError(
                "request-schema", "texture_resolution must be 1024 or 2048"
            )
        roblox_quality = _require_string(payload, "roblox_quality")
        if roblox_quality not in {"balanced", "high"}:
            raise WorkerError(
                "request-schema", "roblox_quality must be 'balanced' or 'high'"
            )
        texture_mode = "native_1k" if texture_resolution == 1024 else "ai_4k"
        geometry_mode = "target_ready" if roblox_quality == "balanced" else "max_detail"
        if target == "unreal":
            geometry_mode = "original"
        hardware_preset = "auto"
        engine_mode = "spar3d_legacy"
    else:
        texture_mode = _require_string(payload, "texture_mode")
        geometry_mode = _require_string(payload, "geometry_mode")
        hardware_preset = _require_string(payload, "hardware_preset")
        engine_mode = (
            "spar3d_legacy"
            if schema_version == 2
            else _require_string(payload, "engine_mode")
        )
        if texture_mode not in {"native_2k", "ai_4k", "export_8k"}:
            raise WorkerError(
                "request-schema",
                "texture_mode must be native_2k, ai_4k, or export_8k",
            )
        if geometry_mode not in GEOMETRY_MODES:
            raise WorkerError(
                "request-schema",
                "geometry_mode must be target_ready, max_detail, or original",
            )
        if hardware_preset not in HARDWARE_PRESETS:
            raise WorkerError(
                "request-schema",
                "hardware_preset must be auto, vram_8gb, or vram_16gb_plus",
            )
        if engine_mode not in ENGINE_MODES:
            raise WorkerError(
                "request-schema",
                "engine_mode must be hunyuan3d_2mini_low_vram, "
                "hunyuan3d_2_1_pbr, or spar3d_legacy",
            )

    if texture_mode == "export_8k" and target != "unreal":
        raise WorkerError("request-schema", "8K export is available only for Unreal")
    if target == "unreal" and geometry_mode != "original":
        raise WorkerError("request-schema", "Unreal geometry_mode must be original")
    if target == "roblox" and geometry_mode == "original":
        raise WorkerError(
            "request-schema", "Roblox geometry_mode must be target_ready or max_detail"
        )

    return Job(
        image_path=_validate_image_path(_require_string(payload, "image_path")),
        output_dir=_validate_output_root(_require_string(payload, "output_dir")),
        engine_mode=engine_mode,
        target=target,
        texture_mode=texture_mode,
        geometry_mode=geometry_mode,
        hardware_preset=hardware_preset,
        source_schema_version=int(schema_version),
    )


def _configure_runtime_environment(bundle: BundlePaths) -> Path:
    # transparent-background writes a tiny generated config next to its state
    # directory even when an explicit checkpoint is supplied.  Keep that state
    # in LocalAppData instead of modifying the portable application folder.
    state_base = Path(
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("TEMP")
        or os.environ.get("TMP")
        or str(Path.cwd())
    )
    application_state = state_base / "MujassamAI"
    hub_cache = application_state / "HuggingFace" / "hub"
    try:
        hub_cache.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkerError(
            "cache-path", f"Could not create the local model cache: {exc}"
        ) from exc

    forced = {
        "HF_HOME": str(application_state / "HuggingFace"),
        "HF_HUB_CACHE": str(hub_cache),
        "TRANSFORMERS_CACHE": str(application_state / "HuggingFace" / "transformers"),
        "HF_HUB_DOWNLOAD_TIMEOUT": str(HUB_DOWNLOAD_TIMEOUT_SECONDS),
        "HF_HUB_ETAG_TIMEOUT": str(HUB_ETAG_TIMEOUT_SECONDS),
        "HF_DATASETS_OFFLINE": "1",
        "DIFFUSERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "DO_NOT_TRACK": "1",
        "WANDB_MODE": "offline",
        "TOKENIZERS_PARALLELISM": "false",
        "SPAR3D_LOW_VRAM": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
        "TRANSPARENT_BACKGROUND_FILE_PATH": str(application_state),
    }
    for key, value in forced.items():
        os.environ[key] = value
    # huggingface_hub reads HF_HUB_OFFLINE while it is imported.  Explicitly
    # remove inherited offline flags so the missing public DINOv2 files can be
    # fetched on the first run.  All gated SPAR3D assets remain bundle-local.
    for offline_name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        os.environ.pop(offline_name, None)
    for secret_name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
        os.environ.pop(secret_name, None)
    return hub_cache


def _safetensors_keys(path: Path) -> set[str]:
    try:
        with path.open("rb") as stream:
            raw_length = stream.read(8)
            if len(raw_length) != 8:
                raise ValueError("missing header length")
            header_length = int.from_bytes(raw_length, "little", signed=False)
            if not 2 <= header_length <= MAX_SAFETENSORS_HEADER_BYTES:
                raise ValueError("invalid header length")
            raw_header = stream.read(header_length)
            if len(raw_header) != header_length:
                raise ValueError("incomplete header")
        header = json.loads(raw_header.decode("utf-8"))
        if not isinstance(header, dict):
            raise ValueError("header is not an object")
        return {str(key) for key in header if key != "__metadata__"}
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise WorkerError(
            "model-header", f"Could not inspect the bundled SPAR3D checkpoint: {exc}"
        ) from exc


def _checkpoint_embeds_dinov2(path: Path) -> bool:
    keys = _safetensors_keys(path)
    required_tails = (
        "model.embeddings.cls_token",
        "model.embeddings.position_embeddings",
        "model.embeddings.patch_embeddings.projection.weight",
        "model.encoder.layer.23.attention.attention.query.weight",
        "model.layernorm.weight",
    )
    for root in ("image_tokenizer", "pdiff_image_tokenizer"):
        if not all(
            any(key.endswith(f"{root}.{tail}") for key in keys)
            for tail in required_tails
        ):
            return False
    return True


def _install_embedded_dinov2_loader(runtime: dict[str, Any]) -> None:
    Dinov2Model = runtime["Dinov2Model"]
    Dinov2Config = runtime["Dinov2Config"]
    original_from_pretrained = Dinov2Model.from_pretrained

    def local_from_pretrained(
        cls: Any, pretrained_model_name_or_path: object, *args: Any, **kwargs: Any
    ) -> Any:
        if str(pretrained_model_name_or_path) == DINO_REPOSITORY:
            return cls(Dinov2Config(**DINO_CONFIG_KWARGS))
        return original_from_pretrained(pretrained_model_name_or_path, *args, **kwargs)

    Dinov2Model.from_pretrained = classmethod(local_from_pretrained)


def _prepare_dinov2_dependency(
    model: Any,
    hub_cache: Path,
    bundle: BundlePaths,
    runtime: dict[str, Any],
) -> str:
    """Use checkpoint-embedded DINOv2, or resolve the pinned public snapshot.

    Returns the source used for the DINOv2 initialization weights.
    """

    if _checkpoint_embeds_dinov2(bundle.model_weights):
        _emit_progress(44, "Using DINOv2 weights already included with SPAR3D")
        _install_embedded_dinov2_loader(runtime)
        _emit_progress(51, "Bundled DINOv2 helper model is ready")
        return "embedded-spar3d-checkpoint"

    try:
        from huggingface_hub import hf_hub_download
    except Exception as exc:
        raise WorkerError(
            "dinov2-runtime", f"The bundled DINOv2 downloader could not load: {exc}"
        ) from exc

    paths: dict[str, Path] = {}
    downloaded = False
    for filename in ("config.json", "model.safetensors"):
        try:
            cached = hf_hub_download(
                repo_id=DINO_REPOSITORY,
                filename=filename,
                revision=DINO_REVISION,
                cache_dir=str(hub_cache),
                local_files_only=True,
            )
            paths[filename] = Path(cached).resolve(strict=True)
            continue
        except Exception:
            pass

        downloaded = True
        if filename == "model.safetensors":
            _emit_progress(
                46,
                "Downloading the one-time DINOv2 helper model (1.22 GB); interrupted transfers resume",
            )
        else:
            _emit_progress(44, "Preparing the one-time DINOv2 helper model")
        try:
            fetched = hf_hub_download(
                repo_id=DINO_REPOSITORY,
                filename=filename,
                revision=DINO_REVISION,
                cache_dir=str(hub_cache),
                local_files_only=False,
            )
            paths[filename] = Path(fetched).resolve(strict=True)
        except Exception as exc:
            raise WorkerError(
                "dinov2-download",
                "The one-time DINOv2 download failed. Check the internet connection "
                f"and try again: {exc}",
            ) from exc

    config_path = paths["config.json"]
    weight_path = paths["model.safetensors"]
    if config_path.parent != weight_path.parent:
        raise WorkerError("dinov2-cache", "The local DINOv2 cache is inconsistent")
    if weight_path.stat().st_size < 1_000_000_000:
        raise WorkerError("dinov2-cache", "The cached DINOv2 weights are incomplete")
    _emit_progress(49, "Verifying the local DINOv2 helper model")
    if _sha256(weight_path) != DINO_WEIGHT_SHA256:
        raise WorkerError("dinov2-cache", "The cached DINOv2 weights failed verification")

    local_snapshot = str(config_path.parent)
    try:
        model.cfg.image_tokenizer.pretrained_model_name_or_path = local_snapshot
        model.cfg.pdiff_image_tokenizer.pretrained_model_name_or_path = local_snapshot
    except Exception as exc:
        raise WorkerError(
            "dinov2-config", f"Could not configure the local DINOv2 model: {exc}"
        ) from exc
    _emit_progress(51, "DINOv2 helper model is ready")
    return "downloaded" if downloaded else "local-cache"


def _sha256(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_useful_alpha(image: Any) -> bool:
    if "A" not in image.getbands() and not (
        image.mode == "P" and "transparency" in image.info
    ):
        return False
    alpha = image.convert("RGBA").getchannel("A")
    minimum, _maximum = alpha.getextrema()
    return int(minimum) < 255


def _import_runtime(bundle: BundlePaths) -> dict[str, Any]:
    vendor_string = str(bundle.vendor_root)
    sys.path[:] = [entry for entry in sys.path if entry != vendor_string]
    sys.path.insert(0, vendor_string)
    try:
        import torch
        from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError
        from spar3d.models.mesh import TRIANGLE_REMESH_AVAILABLE
        from spar3d.system import SPAR3D
        from spar3d.models.tokenizers.dinov2 import Dinov2Model
        from spar3d.utils import foreground_crop, remove_background
        from transparent_background import Remover
        from transformers.models.dinov2.configuration_dinov2 import Dinov2Config
        import spar3d
    except Exception as exc:
        raise WorkerError("runtime-import", f"Bundled 3D runtime could not load: {exc}") from exc

    # Upstream SPAR3D intentionally has no spar3d/__init__.py, so Python loads
    # it as a namespace package and spar3d.__file__ is None.  Validate its
    # namespace search location instead of treating it like a regular module.
    expected_package_root = (bundle.vendor_root / "spar3d").resolve(strict=True)
    try:
        package_locations = {
            Path(entry).resolve(strict=True)
            for entry in getattr(spar3d, "__path__", ())
        }
    except (OSError, RuntimeError, TypeError) as exc:
        raise WorkerError(
            "vendor-invalid", "SPAR3D namespace location could not be validated"
        ) from exc
    if package_locations != {expected_package_root}:
        raise WorkerError(
            "vendor-shadowed", "SPAR3D was imported from outside the pinned vendor directory"
        )
    return {
        "torch": torch,
        "Image": Image,
        "ImageFilter": ImageFilter,
        "ImageOps": ImageOps,
        "UnidentifiedImageError": UnidentifiedImageError,
        "SPAR3D": SPAR3D,
        "Dinov2Model": Dinov2Model,
        "Dinov2Config": Dinov2Config,
        "foreground_crop": foreground_crop,
        "remove_background": remove_background,
        "Remover": Remover,
        "triangle_remesh_available": bool(TRIANGLE_REMESH_AVAILABLE),
    }


def _prepare_image(
    job: Job,
    bundle: BundlePaths,
    runtime: dict[str, Any],
    staging_dir: Path,
) -> tuple[Any, dict[str, Any]]:
    Image = runtime["Image"]
    Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
    try:
        with Image.open(job.image_path) as opened:
            opened.load()
            normalized = runtime["ImageOps"].exif_transpose(opened)
            width, height = normalized.size
            if width < 64 or height < 64:
                raise WorkerError("image-dimensions", "The source image must be at least 64x64")
            if width * height > MAX_IMAGE_PIXELS:
                raise WorkerError("image-dimensions", "The source image has too many pixels")
            useful_alpha = _has_useful_alpha(normalized)
            image = normalized.convert("RGBA")
            if useful_alpha and int(image.getchannel("A").getextrema()[1]) == 0:
                raise WorkerError("image-alpha", "The source image is fully transparent")
    except WorkerError:
        raise
    except Exception as exc:
        raise WorkerError("image-decode", f"Could not decode the source image: {exc}") from exc

    background_removed = False
    if useful_alpha:
        _emit_progress(20, "Using the image's existing alpha mask")
    else:
        _emit_progress(18, "Removing background on CPU")
        remover = None
        try:
            remover = runtime["Remover"](
                mode="base",
                device="cpu",
                ckpt=str(bundle.background_checkpoint),
            )
            image = runtime["remove_background"](image, remover).convert("RGBA")
            background_removed = True
        except Exception as exc:
            raise WorkerError(
                "background-removal", f"CPU background removal failed: {exc}"
            ) from exc
        finally:
            if remover is not None:
                del remover
            gc.collect()

    _emit_progress(28, f"Cropping foreground at ratio {FOREGROUND_RATIO}")
    try:
        image = runtime["foreground_crop"](image, FOREGROUND_RATIO).convert("RGBA")
        prepared_path = staging_dir / "input_prepared.png"
        image.save(prepared_path, format="PNG", optimize=True)
    except Exception as exc:
        raise WorkerError("foreground-crop", f"Foreground preparation failed: {exc}") from exc

    return image, {
        "width": int(width),
        "height": int(height),
        "had_useful_alpha": useful_alpha,
        "background_removed": background_removed,
        "prepared_width": int(image.width),
        "prepared_height": int(image.height),
    }


def _scaled_texture_size(size: tuple[int, int], longest_edge: int) -> tuple[int, int]:
    width, height = (int(size[0]), int(size[1]))
    if width < 1 or height < 1 or longest_edge < 1:
        raise WorkerError("texture-enhance", "The baked texture has invalid dimensions")
    scale = float(longest_edge) / float(max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _enhance_texture_image(
    source: Any,
    *,
    longest_edge: int,
    runtime: dict[str, Any],
    sharpen: dict[str, float | int] | None,
) -> Any:
    """Resize one PIL texture losslessly and sharpen luminance when requested."""

    Image = runtime["Image"]
    ImageFilter = runtime["ImageFilter"]
    if source is None or not hasattr(source, "convert") or not hasattr(source, "size"):
        raise WorkerError("texture-enhance", "The baked material texture is invalid")

    try:
        has_alpha = "A" in source.getbands()
        converted = source.convert("RGBA" if has_alpha else "RGB")
        output_size = _scaled_texture_size(converted.size, longest_edge)
        if converted.size != output_size:
            converted = converted.resize(output_size, Image.Resampling.LANCZOS)

        if sharpen is not None:
            # Sharpen only luminance. Sharpening RGB independently can create
            # colored halos around UV seams; normal/data maps never use this.
            alpha = converted.getchannel("A") if has_alpha else None
            rgb = converted.convert("RGB")
            y, cb, cr = rgb.convert("YCbCr").split()
            y = y.filter(
                ImageFilter.UnsharpMask(
                    radius=float(sharpen["radius"]),
                    percent=int(sharpen["percent"]),
                    threshold=int(sharpen["threshold"]),
                )
            )
            converted = Image.merge("YCbCr", (y, cb, cr)).convert("RGB")
            if alpha is not None:
                converted.putalpha(alpha)

        # Trimesh 4.4.x preserves JPEG only when PIL's format equals JPEG.
        # Setting PNG makes the GLB embed a lossless image.
        converted.format = "PNG"
        return converted
    except WorkerError:
        raise
    except Exception as exc:
        raise WorkerError(
            "texture-enhance", f"Could not enhance a baked texture: {exc}"
        ) from exc


def _capture_material_textures(mesh: Any) -> tuple[Any, dict[str, Any]]:
    material = getattr(getattr(mesh, "visual", None), "material", None)
    if material is None:
        raise WorkerError("texture-enhance", "The generated mesh has no material")

    textures: dict[str, Any] = {}
    for attribute in (
        "baseColorTexture",
        "normalTexture",
        "metallicRoughnessTexture",
        "emissiveTexture",
        "occlusionTexture",
    ):
        value = getattr(material, attribute, None)
        if (
            value is not None
            and hasattr(value, "copy")
            and hasattr(value, "convert")
            and hasattr(value, "size")
        ):
            textures[attribute] = value.copy()

    # Some Trimesh material variants expose the color texture as image.
    if "baseColorTexture" not in textures:
        fallback = getattr(material, "image", None)
        if (
            fallback is not None
            and hasattr(fallback, "copy")
            and hasattr(fallback, "convert")
            and hasattr(fallback, "size")
        ):
            textures["image"] = fallback.copy()
    if not any(key in textures for key in ("baseColorTexture", "image")):
        raise WorkerError("texture-enhance", "The generated material has no color texture")
    return material, textures


def _apply_texture_profile(
    material: Any,
    source_textures: dict[str, Any],
    *,
    color_size: int,
    data_size: int,
    sharpen: dict[str, float | int] | None,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    enhanced: dict[str, dict[str, Any]] = {}
    for attribute, source in source_textures.items():
        is_color = attribute in {"baseColorTexture", "image", "emissiveTexture"}
        target_size = color_size if is_color else data_size
        result = _enhance_texture_image(
            source,
            longest_edge=target_size,
            runtime=runtime,
            sharpen=sharpen if is_color else None,
        )
        setattr(material, attribute, result)
        enhanced[attribute] = {
            "source_size": [int(source.size[0]), int(source.size[1])],
            "output_size": [int(result.size[0]), int(result.size[1])],
            "encoding": "PNG",
            "luminance_sharpened": bool(is_color and sharpen is not None),
        }
    return enhanced


def _glb_metadata(path: Path) -> dict[str, Any]:
    try:
        file_size = path.stat().st_size
        if file_size < 20:
            raise ValueError("file is too small")
        with path.open("rb") as stream:
            header = stream.read(12)
            magic, version, declared_size = struct.unpack("<4sII", header)
            if magic != b"glTF" or version != 2 or declared_size != file_size:
                raise ValueError("invalid GLB header")
            json_length, json_type = struct.unpack("<II", stream.read(8))
            if json_type != 0x4E4F534A or json_length <= 0 or json_length > file_size - 20:
                raise ValueError("invalid GLB JSON chunk")
            document = json.loads(stream.read(json_length).decode("utf-8").rstrip(" \t\r\n\x00"))
        if str(document.get("asset", {}).get("version")) != "2.0":
            raise ValueError("GLB asset version is not 2.0")
        mesh_count = len(document.get("meshes", []))
        if mesh_count < 1:
            raise ValueError("GLB contains no meshes")
        image_mime_types = [
            str(image.get("mimeType", ""))
            for image in document.get("images", [])
            if isinstance(image, dict)
        ]
        return {
            "bytes": file_size,
            "meshes": mesh_count,
            "materials": len(document.get("materials", [])),
            "images": len(document.get("images", [])),
            "png_images": sum(value == "image/png" for value in image_mime_types),
            "jpeg_images": sum(value == "image/jpeg" for value in image_mime_types),
            "nodes": len(document.get("nodes", [])),
        }
    except (OSError, UnicodeError, json.JSONDecodeError, struct.error, ValueError) as exc:
        raise WorkerError("glb-validation", f"Exported GLB is invalid: {exc}") from exc


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _create_output_paths(job: Job) -> tuple[Path, Path]:
    token = uuid.uuid4().hex
    staging = job.output_dir / f".mujassam-{token}.partial"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = job.output_dir / f"MujassamAI_{job.target}_{timestamp}_{token[:8]}"
    try:
        staging.mkdir(mode=0o700, parents=False, exist_ok=False)
    except OSError as exc:
        raise WorkerError("output-create", f"Could not create the staging directory: {exc}") from exc
    return staging, final


def _safe_remove_staging(staging: Path, output_root: Path) -> None:
    try:
        resolved_root = output_root.resolve(strict=True)
        resolved_staging = staging.resolve(strict=False)
        if (
            _is_within(resolved_staging, resolved_root)
            and resolved_staging.parent == resolved_root
            and resolved_staging.name.startswith(".mujassam-")
            and resolved_staging.name.endswith(".partial")
        ):
            shutil.rmtree(resolved_staging, ignore_errors=True)
    except OSError:
        pass


def _resolve_hardware_profile(job: Job, gpu_memory_bytes: int) -> dict[str, Any]:
    """Resolve one UI preset into safe SPAR3D and texture-restoration settings."""

    if job.hardware_preset == "vram_16gb_plus" and gpu_memory_bytes < HIGH_VRAM_THRESHOLD_BYTES:
        raise WorkerError(
            "hardware-profile",
            "The 16GB+ preset needs at least 12 GB of detected VRAM. "
            "Choose Auto or 8GB on this computer.",
        )
    if job.texture_mode == "export_8k" and gpu_memory_bytes < HIGH_VRAM_THRESHOLD_BYTES:
        raise WorkerError(
            "hardware-profile",
            "AI 8K export needs the stronger computer (at least 12 GB detected VRAM). "
            "Choose AI 4K on this computer.",
        )

    low_vram = (
        job.hardware_preset == "vram_8gb"
        or (
            job.hardware_preset == "auto"
            and gpu_memory_bytes < HIGH_VRAM_THRESHOLD_BYTES
        )
    )
    return {
        "requested": job.hardware_preset,
        "resolved": "8gb-safe" if low_vram else "16gb-plus",
        "low_vram_mode": low_vram,
        "ai_tile_size": 256 if low_vram else 384,
    }


def _import_bundled_quality() -> Any:
    """Import only the application-owned texture-quality package under -I."""

    _require_bundled_path(
        APP_ROOT / "quality" / "__init__.py",
        kind="texture-quality package",
    )
    app_string = str(APP_ROOT)
    sys.path[:] = [entry for entry in sys.path if entry != app_string]
    sys.path.insert(0, app_string)
    import quality as bundled_quality

    expected_quality_root = (APP_ROOT / "quality").resolve(strict=True)
    actual_quality_root = Path(bundled_quality.__file__).resolve(strict=True).parent
    if actual_quality_root != expected_quality_root:
        raise WorkerError(
            "texture-ai-runtime",
            "The texture-quality module was loaded from outside the application",
        )
    return bundled_quality


def _apply_selected_texture_profile(
    material: Any,
    source_textures: dict[str, Any],
    *,
    job: Job,
    hardware: dict[str, Any],
    runtime: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply native lossless or verified AI restoration to the baked textures."""

    target_edge = TEXTURE_MODES[job.texture_mode]
    ai_requested = job.texture_mode in {"ai_4k", "export_8k"}
    ai_passes = 2 if job.texture_mode == "export_8k" else 1
    ai_strength = 0.68 if job.texture_mode == "export_8k" else 0.82
    upscaler: Any = None
    ai_error: str | None = None
    quality_api: dict[str, Any] | None = None

    if ai_requested:
        try:
            bundled_quality = _import_bundled_quality()

            quality_api = {
                "spec": bundled_quality.REALESRGAN_X2PLUS,
                "resize_normal_map": bundled_quality.resize_normal_map,
                "restore_color_texture": bundled_quality.restore_color_texture,
            }
            upscaler = bundled_quality.load_realesrgan_x2plus(
                AI_TEXTURE_MODEL,
                device="cuda",
                half=True,
            )
        except Exception as exc:
            ai_error = _sanitize(exc, 500)
            print(
                "AI texture restoration could not start; exporting a lossless "
                f"Lanczos fallback instead: {ai_error}",
                file=sys.stderr,
                flush=True,
            )

    textures: dict[str, dict[str, Any]] = {}
    try:
        for attribute, source in source_textures.items():
            is_base_color = attribute in {"baseColorTexture", "image"}
            is_normal = attribute == "normalTexture"
            # Unreal's 8K option spends memory where it can add visible color
            # detail. Normal/packed data maps stay at 4K; larger copies add no
            # learned information and make the GLB unnecessarily huge.
            attribute_edge = (
                target_edge
                if is_base_color
                else min(target_edge, 4096)
            )
            output_size = _scaled_texture_size(source.size, attribute_edge)
            method = "lossless-native"

            if is_base_color and ai_requested:
                if quality_api is not None:
                    result = quality_api["restore_color_texture"](
                        source,
                        target_size=output_size,
                        upscaler=upscaler,
                        ai_strength=ai_strength,
                        tile_size=int(hardware["ai_tile_size"]),
                        tile_pad=16,
                        max_ai_passes=ai_passes,
                    )
                    method = "RealESRGAN-x2plus" if upscaler is not None else "Lanczos-fallback"
                else:
                    result = _enhance_texture_image(
                        source,
                        longest_edge=attribute_edge,
                        runtime=runtime,
                        sharpen=None,
                    )
                    method = "Lanczos-fallback"
            elif is_normal and ai_requested and quality_api is not None:
                result = quality_api["resize_normal_map"](source, output_size)
                method = "vector-renormalized"
            else:
                result = _enhance_texture_image(
                    source,
                    longest_edge=attribute_edge,
                    runtime=runtime,
                    sharpen=None,
                )
                method = "Lanczos" if result.size != source.size else "native"

            result.format = "PNG"
            setattr(material, attribute, result)
            textures[attribute] = {
                "source_size": [int(source.size[0]), int(source.size[1])],
                "output_size": [int(result.size[0]), int(result.size[1])],
                "encoding": "PNG",
                "method": method,
            }
    except Exception as exc:
        if ai_requested and upscaler is not None:
            # Never lose an otherwise valid 3D reconstruction to the optional
            # restoration stage. Rebuild every texture from the untouched bake.
            ai_error = _sanitize(exc, 500)
            print(
                f"AI texture restoration failed; using lossless fallback: {ai_error}",
                file=sys.stderr,
                flush=True,
            )
            textures = _apply_texture_profile(
                material,
                source_textures,
                color_size=target_edge,
                data_size=min(target_edge, 4096),
                sharpen=None,
                runtime=runtime,
            )
            for value in textures.values():
                value["method"] = "Lanczos-fallback"
        else:
            raise
    finally:
        if upscaler is not None:
            try:
                upscaler.close()
            except Exception:
                pass
        upscaler = None
        gc.collect()
        torch = runtime.get("torch")
        if torch is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()

    ai_used = any(
        value.get("method") == "RealESRGAN-x2plus" for value in textures.values()
    )
    profile = {
        "mode": job.texture_mode,
        "gpu_bake_resolution": (
            1024 if job.texture_mode == "native_1k" else SPAR_BAKE_RESOLUTION
        ),
        "output_longest_edge": target_edge,
        "lossless_png": True,
        "ai_requested": ai_requested,
        "ai_used": ai_used,
        "ai_model": "RealESRGAN_x2plus" if ai_requested else None,
        "ai_model_sha256": (
            quality_api["spec"].sha256 if quality_api is not None else None
        ),
        "ai_strength": ai_strength if ai_requested else 0.0,
        "ai_passes": ai_passes if ai_used else 0,
        "ai_error": ai_error,
        "textures": textures,
    }
    return textures, profile


def _run_job(job: Job) -> Path:
    _emit_progress(3, "Validating the portable runtime")
    if job.engine_mode != "spar3d_legacy":
        raise WorkerError(
            "engine-component-missing",
            f"{ENGINE_DISPLAY_NAMES[job.engine_mode]} was selected, but its local "
            "runtime component is not installed in this build",
        )
    bundle = _validate_bundle()
    hub_cache = _configure_runtime_environment(bundle)
    staging, final_dir = _create_output_paths(job)
    runtime: dict[str, Any] | None = None
    model: Any = None
    mesh: Any = None
    published = False
    try:
        _emit_progress(8, "Loading pinned local dependencies")
        runtime = _import_runtime(bundle)
        torch = runtime["torch"]
        if not torch.cuda.is_available():
            raise WorkerError(
                "cuda-required", "A CUDA-capable NVIDIA GPU is required for SPAR3D"
            )
        cuda_properties = torch.cuda.get_device_properties(torch.cuda.current_device())
        gpu_memory_bytes = int(cuda_properties.total_memory)
        hardware = _resolve_hardware_profile(job, gpu_memory_bytes)
        if (
            job.target == "roblox"
            and job.geometry_mode == "target_ready"
            and not runtime["triangle_remesh_available"]
        ):
            raise WorkerError(
                "remesh-unavailable",
                "Triangle remeshing support is missing from the bundled SPAR3D runtime",
            )

        source_hash = _sha256(job.image_path)
        image, image_info = _prepare_image(job, bundle, runtime, staging)
        torch.cuda.empty_cache()

        seed = 42
        random.seed(seed)
        try:
            import numpy as np

            np.random.seed(seed)
        except Exception:
            pass
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        os.environ["SPAR3D_LOW_VRAM"] = "1" if hardware["low_vram_mode"] else "0"
        mode_label = "8GB-safe" if hardware["low_vram_mode"] else "16GB+ full"
        _emit_progress(38, f"Loading SPAR3D in {mode_label} mode")
        try:
            model = runtime["SPAR3D"].from_pretrained(
                str(bundle.model_root),
                config_name=MODEL_CONFIG_NAME,
                weight_name=MODEL_WEIGHT_NAME,
                low_vram_mode=bool(hardware["low_vram_mode"]),
            )
            dinov2_source = _prepare_dinov2_dependency(
                model, hub_cache, bundle, runtime
            )
            model.to("cuda")
            model.eval()
        except WorkerError:
            raise
        except Exception as exc:
            raise WorkerError("model-load", f"Could not load the bundled SPAR3D model: {exc}") from exc

        if job.target == "roblox" and job.geometry_mode == "target_ready":
            remesh_option = "triangle"
            target_vertices: int | None = ROBLOX_READY_VERTEX_TARGET
            vertex_count = target_vertices
        else:
            remesh_option = "none"
            target_vertices = None
            vertex_count = -1

        bake_resolution = (
            1024 if job.texture_mode == "native_1k" else SPAR_BAKE_RESOLUTION
        )
        autocast_dtype = (
            torch.bfloat16
            if torch.cuda.is_bf16_supported()
            else torch.float16
        )

        _emit_progress(55, "Reconstructing the 3D surface and textures")
        try:
            with torch.no_grad(), torch.autocast(
                device_type="cuda", dtype=autocast_dtype
            ):
                mesh, generation = model.run_image(
                    [image],
                    bake_resolution=bake_resolution,
                    remesh=remesh_option,
                    vertex_count=vertex_count,
                    return_points=True,
                )
        except torch.cuda.OutOfMemoryError as exc:
            raise WorkerError(
                "cuda-out-of-memory",
                "GPU memory was exhausted. Close GPU applications and choose the 8GB preset.",
            ) from exc
        except Exception as exc:
            raise WorkerError("reconstruction", f"SPAR3D reconstruction failed: {exc}") from exc

        points_path = staging / "points.ply"
        points_exported = False
        try:
            point_clouds = generation.get("point_clouds", [])
            if point_clouds:
                point_clouds[0].export(str(points_path))
                points_exported = points_path.is_file() and points_path.stat().st_size > 0
        except Exception:
            points_path.unlink(missing_ok=True)

        # SPAR3D and Real-ESRGAN never share VRAM. This is what lets the same
        # executable offer AI 4K safely on 8 GB cards and larger tiles on a
        # stronger computer.
        generation = None
        image = None
        model = None
        gc.collect()
        torch.cuda.empty_cache()

        material, source_textures = _capture_material_textures(mesh)
        if job.texture_mode == "native_1k" or job.texture_mode == "native_2k":
            _emit_progress(80, "Preserving the original bake as lossless PNG")
        elif job.texture_mode == "ai_4k":
            _emit_progress(80, "Restoring the color texture with AI at 4K")
        else:
            _emit_progress(80, "Restoring the color texture with AI for 8K Unreal export")
        texture_info, texture_profile = _apply_selected_texture_profile(
            material,
            source_textures,
            job=job,
            hardware=hardware,
            runtime=runtime,
        )

        _emit_progress(91, "Exporting one lossless GLB matching your selection")
        glb_path = staging / "model.glb"
        try:
            mesh.export(str(glb_path), include_normals=True)
        except Exception as exc:
            raise WorkerError("glb-export", f"GLB export failed: {exc}") from exc

        _emit_progress(94, "Validating lossless GLB")
        glb_info = _glb_metadata(glb_path)
        if glb_info["images"] and glb_info["png_images"] != glb_info["images"]:
            raise WorkerError(
                "glb-texture-encoding",
                "The exported GLB contains a texture that is not lossless PNG",
            )
        glb_hash = _sha256(glb_path)

        _emit_progress(96, "Finalizing the verified asset")

        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "application": "MujassamAI Portable",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "request": {
                "target": job.target,
                "source_schema_version": job.source_schema_version,
                "engine_mode": job.engine_mode,
                "texture_mode": job.texture_mode,
                "geometry_mode": job.geometry_mode,
                "hardware_preset": job.hardware_preset,
            },
            "source": {
                "original_name": job.image_path.name,
                "sha256": source_hash,
                **image_info,
            },
            "reconstruction": {
                "engine": "SPAR3D",
                "engine_mode": job.engine_mode,
                "requested_engine": ENGINE_DISPLAY_NAMES[job.engine_mode],
                "offline": dinov2_source != "downloaded",
                "dinov2_downloaded_this_run": dinov2_source == "downloaded",
                "dinov2_source": dinov2_source,
                "dinov2_repository": DINO_REPOSITORY,
                "dinov2_revision": DINO_REVISION,
                "vendor_path": "app/vendor/stable-point-aware-3d",
                "model_path": "models/spar3d",
                "model_config": MODEL_CONFIG_NAME,
                "model_weights": MODEL_WEIGHT_NAME,
                "model_weights_bytes": bundle.model_weights.stat().st_size,
                "background_checkpoint": "models/transparent-background/ckpt_base.pth",
                "device": "cuda",
                "gpu": str(cuda_properties.name),
                "gpu_memory_bytes": gpu_memory_bytes,
                "torch_version": str(torch.__version__),
                "autocast_dtype": str(autocast_dtype),
                "hardware_profile": hardware,
                "low_vram_mode": bool(hardware["low_vram_mode"]),
                "foreground_ratio": FOREGROUND_RATIO,
                "seed": seed,
                "texture_enhancement": texture_profile,
                "remesh": {
                    "option": remesh_option,
                    "count_type": "vertex" if target_vertices is not None else "keep",
                    "target_vertices": target_vertices,
                },
            },
            "output": {
                "glb": "model.glb",
                "glb_sha256": glb_hash,
                "glb_stats": glb_info,
                "prepared_image": "input_prepared.png",
                "points": "points.ply" if points_exported else None,
            },
        }
        _atomic_json(staging / "manifest.json", manifest)

        _emit_progress(98, "Publishing the completed model")
        if final_dir.exists():
            raise WorkerError("output-conflict", "The final output directory already exists")
        staging.rename(final_dir)
        published = True
        final_glb = final_dir / "model.glb"
        _emit_progress(100, "3D model completed")
        _emit_artifact(final_glb)
        return final_glb
    finally:
        model = None
        gc.collect()
        if runtime is not None:
            torch = runtime.get("torch")
            try:
                if torch is not None and torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
        if not published:
            _safe_remove_staging(staging, job.output_dir)


def _run_hunyuan2_component(request_path: Path) -> int:
    """Run Hunyuan in its own process and relay the existing MJ protocol."""

    try:
        component = HUNYUAN2_WORKER.resolve(strict=True)
    except OSError as exc:
        raise WorkerError(
            "engine-component-missing",
            "Hunyuan3D is selected, but its engine update is not installed",
        ) from exc
    if not component.is_file() or not _is_within(component, APP_ROOT):
        raise WorkerError(
            "engine-component-invalid",
            "The Hunyuan3D engine component path is invalid",
        )

    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,max_split_size_mb:128",
            "CUDA_MODULE_LOADING": "LAZY",
        }
    )
    command = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(component),
        "--job",
        str(request_path.resolve(strict=True)),
    ]
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

    def relay(stream: Any, *, error_stream: bool) -> None:
        for line in iter(stream.readline, ""):
            raw = line.rstrip("\r\n")
            if not error_stream and raw.startswith(
                ("MJPROGRESS|", "MJARTIFACT|", "MJERROR|", "MJSTAGEOOM|")
            ):
                cleaned = raw[:2000]
            else:
                cleaned = _sanitize(raw, 2000)
            print(cleaned, file=sys.stderr if error_stream else sys.stdout, flush=True)

    stdout_thread = threading.Thread(
        target=relay, args=(process.stdout,), kwargs={"error_stream": False}, daemon=True
    )
    stderr_thread = threading.Thread(
        target=relay, args=(process.stderr,), kwargs={"error_stream": True}, daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    exit_code = int(process.wait())
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)
    return exit_code


def _self_test() -> int:
    sample_job = Job(
        image_path=Path("input.png"),
        output_dir=Path("output"),
        engine_mode="hunyuan3d_2mini_low_vram",
        target="roblox",
        texture_mode="ai_4k",
        geometry_mode="target_ready",
        hardware_preset="auto",
        source_schema_version=3,
    )
    low_profile = _resolve_hardware_profile(sample_job, 8 * 1024**3)
    high_profile = _resolve_hardware_profile(sample_job, 16 * 1024**3)
    checks: list[tuple[bool, str]] = [
        (SCHEMA_VERSION == 3, "schema version"),
        (SUPPORTED_SCHEMA_VERSIONS == {1, 2, 3}, "supported schema versions"),
        (FOREGROUND_RATIO == 1.3, "foreground ratio"),
        (TARGETS == {"roblox", "unreal"}, "targets"),
        (
            ENGINE_MODES
            == {
                "hunyuan3d_2mini_low_vram",
                "hunyuan3d_2_1_pbr",
                "spar3d_legacy",
            },
            "engine modes",
        ),
        (
            TEXTURE_MODES
            == {
                "native_1k": 1024,
                "native_2k": 2048,
                "ai_4k": 4096,
                "export_8k": 8192,
            },
            "texture modes",
        ),
        (
            HARDWARE_PRESETS == {"auto", "vram_8gb", "vram_16gb_plus"},
            "hardware presets",
        ),
        (
            GEOMETRY_MODES == {"target_ready", "max_detail", "original"},
            "geometry modes",
        ),
        (ROBLOX_READY_VERTEX_TARGET == 9_500, "Roblox ready target"),
        (SPAR_BAKE_RESOLUTION == 2048, "SPAR bake resolution"),
        (low_profile["low_vram_mode"] is True, "automatic 8GB profile"),
        (high_profile["low_vram_mode"] is False, "automatic 16GB profile"),
        (VENDOR_ROOT == APP_ROOT / "vendor" / "stable-point-aware-3d", "vendor path"),
        (MODEL_ROOT == BUNDLE_ROOT / "models" / "spar3d", "model path"),
        (
            AI_TEXTURE_MODEL
            == BUNDLE_ROOT / "models" / "realesrgan" / "RealESRGAN_x2plus.pth",
            "AI texture model path",
        ),
        (DINO_REPOSITORY == "facebook/dinov2-large", "DINOv2 repository"),
        (len(DINO_REVISION) == 40, "DINOv2 revision"),
        (len(DINO_WEIGHT_SHA256) == 64, "DINOv2 weight digest"),
        (HUB_DOWNLOAD_TIMEOUT_SECONDS == 300, "download timeout"),
        (HUB_ETAG_TIMEOUT_SECONDS == 60, "metadata timeout"),
        (MAX_SAFETENSORS_HEADER_BYTES == 64 * 1024 * 1024, "model header limit"),
        (DINO_CONFIG_KWARGS.get("hidden_size") == 1024, "DINOv2 hidden size"),
        (DINO_CONFIG_KWARGS.get("num_hidden_layers") == 24, "DINOv2 layers"),
        (
            BACKGROUND_CHECKPOINT
            == BUNDLE_ROOT / "models" / "transparent-background" / "ckpt_base.pth",
            "background checkpoint path",
        ),
    ]
    failed = [label for passed, label in checks if not passed]
    if failed:
        _emit_error("self-test", f"Failed checks: {', '.join(failed)}")
        return 1
    print("MJSELFTEST|OK|3", flush=True)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MujassamAI portable SPAR3D worker")
    parser.add_argument("request", nargs="?", help="Path to the JSON request")
    parser.add_argument(
        "--job",
        "--request",
        dest="request_option",
        help="Path to the JSON request (launcher-friendly form)",
    )
    parser.add_argument("--self-test", action="store_true", help="Run lightweight checks")
    parser.add_argument(
        "--quality-self-test",
        action="store_true",
        help="Import and exercise the bundled AI texture module under isolated mode",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.self_test:
        return _self_test()
    if args.quality_self_test:
        try:
            result = _import_bundled_quality().self_test()
            print(
                "MJQUALITYSELFTEST|OK|" + json.dumps(result, sort_keys=True),
                flush=True,
            )
            return 0
        except Exception as exc:
            _emit_error("quality-self-test", exc)
            return 1
    request_value = args.request_option or args.request
    if not request_value:
        _emit_error("usage", "A JSON request path is required")
        return 2
    _emit_progress(1, "Reading request")
    try:
        payload = _load_request(Path(request_value))
        job = _parse_job(payload)
        if job.engine_mode == "hunyuan3d_2mini_low_vram":
            return _run_hunyuan2_component(Path(request_value))
        _run_job(job)
        return 0
    except WorkerError as exc:
        _emit_error(exc.code, exc)
        return 1
    except KeyboardInterrupt:
        _emit_error("cancelled", "The operation was cancelled")
        return 130
    except Exception as exc:
        if os.environ.get("MUJASSAM_DEBUG") == "1":
            traceback.print_exc(file=sys.stderr)
        _emit_error("internal", f"Unexpected worker failure: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
