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
import re
import shutil
import struct
import sys
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
FOREGROUND_RATIO = 1.3
MAX_JOB_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 200 * 1024 * 1024
MAX_IMAGE_PIXELS = 100_000_000
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
TEXTURE_RESOLUTIONS = {1024, 2048}
TARGETS = {"roblox", "unreal"}
ROBLOX_VERTEX_TARGETS = {"balanced": 10_000, "high": 25_000}

# SPAR3D's pinned exporter marks its baked textures as JPEG. Trimesh then
# recompresses them while writing the GLB, which visibly damages fine detail.
# Keep SPAR3D's GPU bake capped at 2K for 8 GB cards, then create lossless
# display textures on CPU. Ultra+ stays at Roblox's 4K limit with stronger
# detail enhancement, while Unreal gets an optional 8K color map and 4K normal.
ULTRA_SOURCE_RESOLUTION = 2048
ULTRA_TEXTURE_SIZE = 4096
ULTRA_PLUS_TEXTURE_SIZE = 8192
ULTRA_SHARPEN = {"radius": 1.15, "percent": 110, "threshold": 2}
ULTRA_PLUS_SHARPEN = {"radius": 1.35, "percent": 145, "threshold": 2}

APP_ROOT = Path(__file__).resolve().parent
BUNDLE_ROOT = APP_ROOT.parent.resolve()
VENDOR_ROOT = APP_ROOT / "vendor" / "stable-point-aware-3d"
MODEL_ROOT = BUNDLE_ROOT / "models" / "spar3d"
BACKGROUND_CHECKPOINT = (
    BUNDLE_ROOT / "models" / "transparent-background" / "ckpt_base.pth"
)

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
    target: str
    texture_resolution: int
    roblox_quality: str


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
    required = {
        "schema_version",
        "image_path",
        "output_dir",
        "target",
        "texture_resolution",
        "roblox_quality",
    }
    missing = sorted(required - payload.keys())
    unknown = sorted(payload.keys() - required)
    if missing:
        raise WorkerError("request-schema", f"Missing properties: {', '.join(missing)}")
    if unknown:
        raise WorkerError("request-schema", f"Unknown properties: {', '.join(unknown)}")

    schema_version = payload["schema_version"]
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise WorkerError(
            "schema-version", f"schema_version must be {SCHEMA_VERSION}"
        )

    target = _require_string(payload, "target")
    if target not in TARGETS:
        raise WorkerError("request-schema", "target must be 'roblox' or 'unreal'")

    texture_resolution = payload["texture_resolution"]
    if type(texture_resolution) is not int or texture_resolution not in TEXTURE_RESOLUTIONS:
        raise WorkerError("request-schema", "texture_resolution must be 1024 or 2048")

    roblox_quality = _require_string(payload, "roblox_quality")
    if roblox_quality not in ROBLOX_VERTEX_TARGETS:
        raise WorkerError(
            "request-schema", "roblox_quality must be 'balanced' or 'high'"
        )

    return Job(
        image_path=_validate_image_path(_require_string(payload, "image_path")),
        output_dir=_validate_output_root(_require_string(payload, "output_dir")),
        target=target,
        texture_resolution=int(texture_resolution),
        roblox_quality=roblox_quality,
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
    sharpen: dict[str, float | int],
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
            "luminance_sharpened": bool(is_color),
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


def _run_job(job: Job) -> Path:
    _emit_progress(3, "Validating the portable runtime")
    bundle = _validate_bundle()
    hub_cache = _configure_runtime_environment(bundle)
    staging, final_dir = _create_output_paths(job)
    runtime: dict[str, Any] | None = None
    model: Any = None
    published = False
    try:
        _emit_progress(8, "Loading pinned local dependencies")
        runtime = _import_runtime(bundle)
        torch = runtime["torch"]
        if not torch.cuda.is_available():
            raise WorkerError(
                "cuda-required", "A CUDA-capable NVIDIA GPU is required for SPAR3D"
            )
        if job.target == "roblox" and not runtime["triangle_remesh_available"]:
            raise WorkerError(
                "remesh-unavailable",
                "Triangle remeshing support is missing from the bundled SPAR3D runtime",
            )

        source_hash = _sha256(job.image_path)
        image, image_info = _prepare_image(job, bundle, runtime, staging)
        torch.cuda.empty_cache()

        seed = 42
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
        _emit_progress(38, "Loading SPAR3D from the bundled model in low-VRAM mode")
        try:
            model = runtime["SPAR3D"].from_pretrained(
                str(bundle.model_root),
                config_name=MODEL_CONFIG_NAME,
                weight_name=MODEL_WEIGHT_NAME,
                low_vram_mode=True,
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

        if job.target == "roblox":
            remesh_option = "triangle"
            target_vertices: int | None = ROBLOX_VERTEX_TARGETS[job.roblox_quality]
            vertex_count = target_vertices
        else:
            remesh_option = "none"
            target_vertices = None
            vertex_count = -1

        _emit_progress(55, "Reconstructing the 3D surface and textures")
        try:
            with torch.no_grad(), torch.autocast(
                device_type="cuda", dtype=torch.bfloat16
            ):
                mesh, generation = model.run_image(
                    [image],
                    bake_resolution=job.texture_resolution,
                    remesh=remesh_option,
                    vertex_count=vertex_count,
                    return_points=True,
                )
        except torch.cuda.OutOfMemoryError as exc:
            raise WorkerError(
                "cuda-out-of-memory",
                "GPU memory was exhausted. Close GPU applications and try texture_resolution 1024.",
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

        # The neural model is no longer needed. Release it before CPU texture
        # enhancement so Ultra+ has as much RAM/VRAM headroom as possible.
        generation = None
        image = None
        model = None
        gc.collect()
        torch.cuda.empty_cache()

        material, source_textures = _capture_material_textures(mesh)
        if job.texture_resolution == ULTRA_SOURCE_RESOLUTION:
            _emit_progress(82, "Creating lossless Ultra 4K textures")
            texture_profile = "ultra-4k-lossless"
            texture_info = _apply_texture_profile(
                material,
                source_textures,
                color_size=ULTRA_TEXTURE_SIZE,
                data_size=ULTRA_TEXTURE_SIZE,
                sharpen=ULTRA_SHARPEN,
                runtime=runtime,
            )
        else:
            _emit_progress(82, "Preserving baked textures as lossless PNG")
            texture_profile = "lossless-native"
            texture_info = _apply_texture_profile(
                material,
                source_textures,
                color_size=job.texture_resolution,
                data_size=job.texture_resolution,
                sharpen=ULTRA_SHARPEN,
                runtime=runtime,
            )

        _emit_progress(87, "Exporting lossless GLB")
        glb_path = staging / "model.glb"
        try:
            mesh.export(str(glb_path), include_normals=True)
        except Exception as exc:
            raise WorkerError("glb-export", f"GLB export failed: {exc}") from exc

        _emit_progress(90, "Validating lossless GLB")
        glb_info = _glb_metadata(glb_path)
        if glb_info["images"] and glb_info["png_images"] != glb_info["images"]:
            raise WorkerError(
                "glb-texture-encoding",
                "The exported GLB contains a texture that is not lossless PNG",
            )
        glb_hash = _sha256(glb_path)

        ultra_plus_info: dict[str, Any] | None = None
        ultra_plus_hash: str | None = None
        ultra_plus_error: str | None = None
        ultra_plus_texture_info: dict[str, Any] | None = None
        create_ultra_plus = (
            job.texture_resolution == ULTRA_SOURCE_RESOLUTION
            and (job.target == "unreal" or job.roblox_quality == "high")
        )
        ultra_plus_color_size = (
            ULTRA_PLUS_TEXTURE_SIZE if job.target == "unreal" else ULTRA_TEXTURE_SIZE
        )
        ultra_plus_label = "Ultra+ 8K" if job.target == "unreal" else "Ultra+ 4K"
        ultra_plus_name = (
            "model_UltraPlus_8K.glb"
            if job.target == "unreal"
            else "model_UltraPlus_4K.glb"
        )
        ultra_plus_path = staging / ultra_plus_name
        if create_ultra_plus:
            _emit_progress(92, f"Creating optional {ultra_plus_label} texture")
            try:
                ultra_plus_texture_info = _apply_texture_profile(
                    material,
                    source_textures,
                    color_size=ultra_plus_color_size,
                    data_size=ULTRA_TEXTURE_SIZE,
                    sharpen=ULTRA_PLUS_SHARPEN,
                    runtime=runtime,
                )
                mesh.export(str(ultra_plus_path), include_normals=True)
                ultra_plus_info = _glb_metadata(ultra_plus_path)
                if (
                    ultra_plus_info["images"]
                    and ultra_plus_info["png_images"] != ultra_plus_info["images"]
                ):
                    raise WorkerError(
                        "glb-texture-encoding",
                        "The Ultra+ GLB contains a texture that is not lossless PNG",
                    )
                ultra_plus_hash = _sha256(ultra_plus_path)
            except Exception as exc:
                ultra_plus_error = _sanitize(exc, 300)
                ultra_plus_path.unlink(missing_ok=True)
                print(
                    f"{ultra_plus_label} was skipped; the verified Ultra 4K model is ready: "
                    + ultra_plus_error,
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                gc.collect()

        _emit_progress(95, "Finalizing the verified asset")
        cuda_properties = torch.cuda.get_device_properties(torch.cuda.current_device())

        manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "application": "MujassamAI Portable",
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "request": {
                "target": job.target,
                "texture_resolution": job.texture_resolution,
                "roblox_quality": job.roblox_quality,
            },
            "source": {
                "original_name": job.image_path.name,
                "sha256": source_hash,
                **image_info,
            },
            "reconstruction": {
                "engine": "SPAR3D",
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
                "gpu_memory_bytes": int(cuda_properties.total_memory),
                "torch_version": str(torch.__version__),
                "low_vram_mode": True,
                "foreground_ratio": FOREGROUND_RATIO,
                "seed": seed,
                "texture_enhancement": {
                    "profile": texture_profile,
                    "gpu_bake_resolution": job.texture_resolution,
                    "lossless_png": True,
                    "textures": texture_info,
                    "ultra_plus_requested": create_ultra_plus,
                    "ultra_plus_created": ultra_plus_info is not None,
                    "ultra_plus_profile": ultra_plus_label,
                    "ultra_plus_error": ultra_plus_error,
                    "ultra_plus_textures": ultra_plus_texture_info,
                },
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
                "ultra_plus_glb": (
                    ultra_plus_name if ultra_plus_info is not None else None
                ),
                "ultra_plus_glb_sha256": ultra_plus_hash,
                "ultra_plus_glb_stats": ultra_plus_info,
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
        final_glb = final_dir / (
            ultra_plus_name if ultra_plus_info is not None else "model.glb"
        )
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


def _self_test() -> int:
    checks: list[tuple[bool, str]] = [
        (SCHEMA_VERSION == 1, "schema version"),
        (FOREGROUND_RATIO == 1.3, "foreground ratio"),
        (TARGETS == {"roblox", "unreal"}, "targets"),
        (TEXTURE_RESOLUTIONS == {1024, 2048}, "texture resolutions"),
        (ULTRA_SOURCE_RESOLUTION == 2048, "Ultra source resolution"),
        (ULTRA_TEXTURE_SIZE == 4096, "Ultra texture size"),
        (ULTRA_PLUS_TEXTURE_SIZE == 8192, "Ultra+ texture size"),
        (ROBLOX_VERTEX_TARGETS == {"balanced": 10_000, "high": 25_000}, "Roblox targets"),
        (VENDOR_ROOT == APP_ROOT / "vendor" / "stable-point-aware-3d", "vendor path"),
        (MODEL_ROOT == BUNDLE_ROOT / "models" / "spar3d", "model path"),
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
    print("MJSELFTEST|OK|1", flush=True)
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.self_test:
        return _self_test()
    request_value = args.request_option or args.request
    if not request_value:
        _emit_error("usage", "A JSON request path is required")
        return 2
    _emit_progress(1, "Reading request")
    try:
        payload = _load_request(Path(request_value))
        job = _parse_job(payload)
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
