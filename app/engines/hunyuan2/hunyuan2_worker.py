#!/usr/bin/env python3
"""Hunyuan3D 2mini + Paint worker for Mujassam AI.

This adapter intentionally runs shape generation, texture generation, and
texture restoration in separate child processes.  Process boundaries are the
most reliable way to return every CUDA allocation before the next stage on an
8 GB GPU.  Official Tencent model snapshots are downloaded once into the
current user's LocalAppData and are pinned to immutable revisions.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENGINE_SCHEMA_VERSION = 1
SOURCE_COMMIT = "f8db63096c8282cb27354314d896feba5ba6ff8a"
SHAPE_REPOSITORY = "tencent/Hunyuan3D-2mini"
SHAPE_REVISION = "f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6"
SHAPE_SUBFOLDER = "hunyuan3d-dit-v2-mini"
PAINT_REPOSITORY = "tencent/Hunyuan3D-2"
PAINT_REVISION = "9cd649ba6913f7a852e3286bad86bfa9a2d83dcf"
PAINT_SUBFOLDER = "hunyuan3d-paint-v2-0-turbo"
DELIGHT_SUBFOLDER = "hunyuan3d-delight-v2-0"
MIN_FREE_DOWNLOAD_BYTES = 24 * 1024**3
MAX_DOWNLOAD_ATTEMPTS = 6
DOWNLOAD_STALL_SECONDS = 180
DOWNLOAD_POLL_SECONDS = 5
DOWNLOAD_STATUS_SECONDS = 30
ROBLOX_READY_FACES = 20_000
ROBLOX_MASTER_FACES = 100_000
MAX_IMAGE_PIXELS = 100_000_000
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
SHAPE_LOW_VRAM_PROFILES = ((384, 8000), (256, 8000), (192, 4000))
PAINT_LOW_VRAM_RESOLUTIONS = (2048, 1536, 1024, 768)

# The two legacy components below have no safetensors alternative in the
# pinned Turbo snapshot.  The official UNet safetensors file is an FP16
# conversion of the same state dict; the bundled loader is patched at build
# time to stream it into an empty model instead of constructing FP32 copies.
# Pin and hash every large Paint weight before any model loader sees it.
PINNED_PAINT_WEIGHTS: dict[str, tuple[int, str]] = {
    f"{PAINT_SUBFOLDER}/text_encoder/pytorch_model.bin": (
        1_361_671_895,
        "c3e254d7b61353497ea0be2c4013df4ea8f739ee88cffa0ba58cd085459ed565",
    ),
    f"{PAINT_SUBFOLDER}/vae/diffusion_pytorch_model.bin": (
        334_707_217,
        "1b4889b6b1d4ce7ae320a02dedaeff1780ad77d415ea0d744b476155c6377ddc",
    ),
    f"{PAINT_SUBFOLDER}/unet/diffusion_pytorch_model.safetensors": (
        3_722_161_032,
        "d6acffa4a22f4da61d87f446bfa83e7ac245481c1535fbf25b200fe4462d0b22",
    ),
}
SHAPE_REQUIRED_FILES = (
    f"{SHAPE_SUBFOLDER}/config.yaml",
    f"{SHAPE_SUBFOLDER}/model.fp16.safetensors",
)
PAINT_REQUIRED_FILES = (
    f"{DELIGHT_SUBFOLDER}/model_index.json",
    f"{DELIGHT_SUBFOLDER}/feature_extractor/preprocessor_config.json",
    f"{DELIGHT_SUBFOLDER}/scheduler/scheduler_config.json",
    f"{DELIGHT_SUBFOLDER}/text_encoder/config.json",
    f"{DELIGHT_SUBFOLDER}/text_encoder/model.safetensors",
    f"{DELIGHT_SUBFOLDER}/tokenizer/merges.txt",
    f"{DELIGHT_SUBFOLDER}/tokenizer/special_tokens_map.json",
    f"{DELIGHT_SUBFOLDER}/tokenizer/tokenizer_config.json",
    f"{DELIGHT_SUBFOLDER}/tokenizer/vocab.json",
    f"{DELIGHT_SUBFOLDER}/unet/config.json",
    f"{DELIGHT_SUBFOLDER}/unet/diffusion_pytorch_model.safetensors",
    f"{DELIGHT_SUBFOLDER}/vae/config.json",
    f"{DELIGHT_SUBFOLDER}/vae/diffusion_pytorch_model.safetensors",
    f"{PAINT_SUBFOLDER}/model_index.json",
    f"{PAINT_SUBFOLDER}/feature_extractor/preprocessor_config.json",
    f"{PAINT_SUBFOLDER}/image_encoder/config.json",
    f"{PAINT_SUBFOLDER}/image_encoder/model.safetensors",
    f"{PAINT_SUBFOLDER}/image_encoder/preprocessor_config.json",
    f"{PAINT_SUBFOLDER}/scheduler/scheduler_config.json",
    f"{PAINT_SUBFOLDER}/text_encoder/config.json",
    f"{PAINT_SUBFOLDER}/text_encoder/pytorch_model.bin",
    f"{PAINT_SUBFOLDER}/tokenizer/merges.txt",
    f"{PAINT_SUBFOLDER}/tokenizer/special_tokens_map.json",
    f"{PAINT_SUBFOLDER}/tokenizer/tokenizer_config.json",
    f"{PAINT_SUBFOLDER}/tokenizer/vocab.json",
    f"{PAINT_SUBFOLDER}/unet/config.json",
    f"{PAINT_SUBFOLDER}/unet/diffusion_pytorch_model.safetensors",
    f"{PAINT_SUBFOLDER}/unet/modules.py",
    f"{PAINT_SUBFOLDER}/vae/config.json",
    f"{PAINT_SUBFOLDER}/vae/diffusion_pytorch_model.bin",
)

ENGINE_ROOT = Path(__file__).resolve().parent
APP_ROOT = ENGINE_ROOT.parent.parent
BUNDLE_ROOT = APP_ROOT.parent
VENDOR_ROOT = ENGINE_ROOT / "vendor" / "Hunyuan3D-2"
TRUSTED_PAINT_UNET_MODULE = (
    VENDOR_ROOT / "hy3dgen" / "texgen" / "hunyuanpaint" / "unet" / "modules.py"
)
PYTHON_PACKAGES = ENGINE_ROOT / "python_packages"
BASE_WORKER_PATH = APP_ROOT / "worker.py"
BACKGROUND_CHECKPOINT = (
    BUNDLE_ROOT / "models" / "transparent-background" / "ckpt_base.pth"
)
SPAR_VENDOR_ROOT = APP_ROOT / "vendor" / "stable-point-aware-3d"


class EngineError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _sanitize(value: object, limit: int = 900) -> str:
    text = str(value).replace("\x00", "").replace("\r", " ").replace("\n", " ")
    text = text.replace("|", "/")
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


def _engine_state_root() -> Path:
    base = (
        os.environ.get("LOCALAPPDATA")
        or os.environ.get("TEMP")
        or os.environ.get("TMP")
    )
    if not base:
        raise EngineError("engine-state", "Windows LocalAppData could not be located")
    root = Path(base).resolve() / "MujassamAI" / "Engines" / "Hunyuan3D-2"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _activate_engine_paths() -> None:
    for path in (str(PYTHON_PACKAGES), str(VENDOR_ROOT)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)


def _validate_engine_pack() -> None:
    required = (
        ENGINE_ROOT / "ENGINE-MANIFEST.json",
        VENDOR_ROOT / "hy3dgen" / "shapegen" / "pipelines.py",
        VENDOR_ROOT / "hy3dgen" / "texgen" / "pipelines.py",
        TRUSTED_PAINT_UNET_MODULE,
        ENGINE_ROOT / "LICENSE-HUNYUAN3D-2.txt",
        ENGINE_ROOT / "NOTICE-HUNYUAN3D-2.txt",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise EngineError(
            "engine-component-missing",
            "The Hunyuan3D engine update is incomplete. Missing: " + ", ".join(missing),
        )
    if not PYTHON_PACKAGES.is_dir():
        raise EngineError(
            "engine-component-missing",
            "The Hunyuan3D Python component is not installed",
        )


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


def _validate_job(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("schema_version") != 3:
        raise EngineError("schema-version", "Hunyuan3D requires job schema version 3")
    if payload.get("engine_mode") != "hunyuan3d_2mini_low_vram":
        raise EngineError(
            "engine-mode",
            "This engine component supports Hunyuan3D 2mini Low-VRAM only",
        )
    target = payload.get("target")
    if target not in {"roblox", "unreal"}:
        raise EngineError("request-schema", "target must be roblox or unreal")
    texture_mode = payload.get("texture_mode")
    if texture_mode not in {"native_2k", "ai_4k", "export_8k"}:
        raise EngineError("request-schema", "Unsupported texture mode")
    geometry_mode = payload.get("geometry_mode")
    if geometry_mode not in {"target_ready", "max_detail", "original"}:
        raise EngineError("request-schema", "Unsupported geometry mode")

    image_value = payload.get("image_path")
    output_value = payload.get("output_dir")
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
        useful_alpha = "A" in source.getbands() and source.convert("RGBA").getchannel("A").getextrema()[0] < 255
        image = source.convert("RGBA")

    removed = False
    if useful_alpha:
        _progress(10, "Using the image's transparent background")
    else:
        _progress(8, "Removing the background locally")
        if not BACKGROUND_CHECKPOINT.is_file() or not SPAR_VENDOR_ROOT.is_dir():
            raise EngineError(
                "background-component",
                "The bundled background-removal component is missing",
            )
        spar_path = str(SPAR_VENDOR_ROOT)
        if spar_path not in sys.path:
            sys.path.insert(0, spar_path)
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
        "background_removed": bool(removed),
        "prepared_width": int(prepared.width),
        "prepared_height": int(prepared.height),
        "prepared_path": str(prepared_path),
    }


def _snapshot_ready(root: Path, required: tuple[str, ...]) -> bool:
    return all((root / relative).is_file() and (root / relative).stat().st_size > 0 for relative in required)


def _pinned_paint_weights_ready(
    root: Path, *, verify_hashes: bool = False
) -> bool:
    """Validate every large Paint weight in the immutable snapshot."""

    for relative, (expected_size, expected_sha256) in PINNED_PAINT_WEIGHTS.items():
        path = root / relative
        try:
            if not path.is_file() or path.stat().st_size != expected_size:
                return False
            if verify_hashes and _sha256(path) != expected_sha256:
                path.unlink(missing_ok=True)
                return False
        except OSError:
            return False
    return True


def _install_trusted_paint_unet_module(root: Path) -> None:
    """Replace Hub executable code with the reviewed, build-pinned loader."""

    try:
        trusted = TRUSTED_PAINT_UNET_MODULE.read_bytes()
        text = trusted.decode("utf-8-sig")
    except OSError as exc:
        raise EngineError(
            "engine-component-missing",
            "The reviewed Hunyuan Paint low-memory loader is missing",
        ) from exc
    required_markers = (
        "from accelerate import init_empty_weights",
        "from accelerate.utils import set_module_tensor_to_device",
        "from safetensors import safe_open",
        "diffusion_pytorch_model.safetensors",
        "weight_keys != model_keys",
    )
    if any(marker not in text for marker in required_markers):
        raise EngineError(
            "engine-component-invalid",
            "The bundled Hunyuan Paint low-memory loader did not pass validation",
        )
    destination = root / PAINT_SUBFOLDER / "unet" / "modules.py"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        trusted_sha256 = hashlib.sha256(trusted).hexdigest()
        if destination.is_file() and _sha256(destination) == trusted_sha256:
            return
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_bytes(trusted)
        os.replace(temporary, destination)
    except OSError as exc:
        raise EngineError(
            "model-files",
            "Could not install the reviewed Hunyuan Paint model loader",
        ) from exc


def _download_patterns(repo_id: str) -> tuple[list[str], list[str]]:
    common_ignore = ["*.ckpt", "*.pt", "*.pth", "*.pkl"]
    if repo_id == SHAPE_REPOSITORY:
        return (
            [
                f"{SHAPE_SUBFOLDER}/config.yaml",
                f"{SHAPE_SUBFOLDER}/model.fp16.safetensors",
                "LICENSE",
                "NOTICE",
                "README.md",
            ],
            common_ignore,
        )
    if repo_id == PAINT_REPOSITORY:
        return (
            [
                f"{DELIGHT_SUBFOLDER}/**",
                f"{PAINT_SUBFOLDER}/**",
                "LICENSE",
                "NOTICE",
                "README.md",
            ],
            common_ignore
            + [f"{PAINT_SUBFOLDER}/unet/diffusion_pytorch_model.bin"],
        )
    raise EngineError("model-download", "Refusing an unpinned model repository")


def _download_snapshot_once(repo_id: str, revision: str, local_dir: Path) -> None:
    expected = {
        SHAPE_REPOSITORY: (
            SHAPE_REVISION,
            _engine_state_root() / "models" / "Hunyuan3D-2mini",
        ),
        PAINT_REPOSITORY: (
            PAINT_REVISION,
            _engine_state_root() / "models" / "Hunyuan3D-2",
        ),
    }.get(repo_id)
    if expected is None or revision != expected[0]:
        raise EngineError("model-download", "Refusing an unpinned model revision")
    expected_root = expected[1].resolve()
    if local_dir.resolve() != expected_root:
        raise EngineError("model-download", "Refusing an unexpected model destination")

    # huggingface_hub reads these settings at import time. A finite socket
    # timeout plus a single worker avoids indefinite concurrent-download hangs.
    os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "60"
    os.environ["HF_HUB_ETAG_TIMEOUT"] = "30"
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ.pop("TRANSFORMERS_OFFLINE", None)
    os.environ.pop("HF_TOKEN", None)
    os.environ.pop("HUGGING_FACE_HUB_TOKEN", None)
    _activate_engine_paths()
    from huggingface_hub import snapshot_download

    allow_patterns, ignore_patterns = _download_patterns(repo_id)
    local_dir.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id=repo_id,
        revision=revision,
        local_dir=str(local_dir),
        allow_patterns=allow_patterns,
        ignore_patterns=ignore_patterns,
        max_workers=1,
    )


def _download_observed_roots(local_dir: Path) -> list[Path]:
    roots = [local_dir]
    for name in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        value = os.environ.get(name)
        if value:
            roots.append(Path(value).expanduser())
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        roots.append(Path(hf_home).expanduser() / "hub")
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(str(root)))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _download_signature(roots: list[Path]) -> tuple[int, int, int]:
    files = 0
    total_bytes = 0
    newest_ns = 0
    for root in roots:
        try:
            candidates = root.rglob("*") if root.exists() else ()
            for candidate in candidates:
                try:
                    if not candidate.is_file():
                        continue
                    stat = candidate.stat()
                    files += 1
                    total_bytes += stat.st_size
                    newest_ns = max(newest_ns, stat.st_mtime_ns)
                except OSError:
                    continue
        except OSError:
            continue
    return files, total_bytes, newest_ns


def _stop_download_process(process: subprocess.Popen[str]) -> None:
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
    *,
    repo_id: str,
    revision: str,
    local_dir: Path,
    progress_percent: int,
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
            "DO_NOT_TRACK": "1",
        }
    )
    for name in (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
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
    )
    roots = _download_observed_roots(local_dir)
    signature = _download_signature(roots)
    last_change = time.monotonic()
    last_status = last_change
    while process.poll() is None:
        time.sleep(DOWNLOAD_POLL_SECONDS)
        now = time.monotonic()
        current = _download_signature(roots)
        if current != signature:
            signature = current
            last_change = now
        if now - last_status >= DOWNLOAD_STATUS_SECONDS:
            cached_gib = current[1] / float(1024**3)
            _progress(
                progress_percent,
                f"Model download active; {cached_gib:.2f} GiB cached locally",
            )
            last_status = now
        if now - last_change >= DOWNLOAD_STALL_SECONDS:
            _stop_download_process(process)
            raise TimeoutError(
                f"No downloaded byte changed for {DOWNLOAD_STALL_SECONDS} seconds"
            )

    stdout, stderr = process.communicate()
    if process.returncode != 0:
        detail = _sanitize(stderr or stdout or f"exit code {process.returncode}")
        raise RuntimeError(detail)


def _download_snapshot_resumable(
    *,
    repo_id: str,
    revision: str,
    local_dir: Path,
    progress_percent: int,
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
        f"The official model download was interrupted after automatic retries: {last_error}",
    )


def _download_models(state_root: Path) -> tuple[Path, Path, bool]:
    models_root = state_root / "models"
    shape_root = models_root / "Hunyuan3D-2mini"
    paint_root = models_root / "Hunyuan3D-2"
    shape_required = SHAPE_REQUIRED_FILES
    paint_required = PAINT_REQUIRED_FILES
    downloaded = False
    paint_ready = _snapshot_ready(
        paint_root, paint_required
    ) and _pinned_paint_weights_ready(paint_root, verify_hashes=True)
    if not _snapshot_ready(shape_root, shape_required) or not paint_ready:
        free_bytes = shutil.disk_usage(state_root).free
        if free_bytes < MIN_FREE_DOWNLOAD_BYTES:
            raise EngineError(
                "disk-space",
                "Hunyuan3D needs at least 24 GB of free disk space for its one-time models",
            )

    if not _snapshot_ready(shape_root, shape_required):
        _progress(13, "Downloading the official Hunyuan3D 2mini model once (resumable)")
        shape_root.mkdir(parents=True, exist_ok=True)
        _download_snapshot_resumable(
            repo_id=SHAPE_REPOSITORY,
            revision=SHAPE_REVISION,
            local_dir=shape_root,
            progress_percent=13,
        )
        downloaded = True
    if not paint_ready:
        _progress(18, "Downloading official Hunyuan3D Paint models once (resumable)")
        paint_root.mkdir(parents=True, exist_ok=True)
        # Remove known partial files so huggingface_hub cannot mistake them for
        # completed local-dir entries when resuming an interrupted download.
        for relative, (expected_size, _) in PINNED_PAINT_WEIGHTS.items():
            candidate = paint_root / relative
            try:
                if candidate.is_file() and candidate.stat().st_size != expected_size:
                    candidate.unlink()
            except OSError:
                pass
        _download_snapshot_resumable(
            repo_id=PAINT_REPOSITORY,
            revision=PAINT_REVISION,
            local_dir=paint_root,
            progress_percent=18,
        )
        downloaded = True

    if _snapshot_ready(paint_root, paint_required):
        _install_trusted_paint_unet_module(paint_root)

    if (
        not _snapshot_ready(shape_root, shape_required)
        or not _snapshot_ready(paint_root, paint_required)
        or not _pinned_paint_weights_ready(paint_root, verify_hashes=True)
    ):
        raise EngineError(
            "model-download",
            "The official Hunyuan3D download did not produce a complete local snapshot",
        )
    ready = {
        "schema_version": ENGINE_SCHEMA_VERSION,
        "source_commit": SOURCE_COMMIT,
        "shape_repository": SHAPE_REPOSITORY,
        "shape_revision": SHAPE_REVISION,
        "paint_repository": PAINT_REPOSITORY,
        "paint_revision": PAINT_REVISION,
        "updated_utc": datetime.now(timezone.utc).isoformat(),
    }
    temporary = state_root / f".engine-ready-{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps(ready, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, state_root / "engine.ready.json")
    return shape_root, paint_root, downloaded


def _child_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,max_split_size_mb:128",
            "CUDA_MODULE_LOADING": "LAZY",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "DIFFUSERS_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    environment.pop("HF_TOKEN", None)
    environment.pop("HUGGING_FACE_HUB_TOKEN", None)
    return environment


def _run_stage(stage: str, state_path: Path) -> int:
    command = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        str(Path(__file__).resolve()),
        "--stage",
        stage,
        "--state",
        str(state_path),
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

    def relay(stream: Any, *, error_stream: bool) -> None:
        for line in iter(stream.readline, ""):
            raw = line.rstrip("\r\n")
            if not error_stream and raw.startswith(
                ("MJPROGRESS|", "MJARTIFACT|", "MJERROR|", "MJSTAGEOOM|")
            ):
                cleaned = raw[:2000]
            else:
                cleaned = _sanitize(raw, 2000)
            print(
                cleaned,
                file=sys.stderr if error_stream else sys.stdout,
                flush=True,
            )

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


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _orchestrate(request_path: Path) -> Path:
    _validate_engine_pack()
    payload = _validate_job(_load_json_file(request_path))
    output_root = Path(payload["output_dir"])
    token = uuid.uuid4().hex
    staging = output_root / f".mujassam-{token}.partial"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_dir = output_root / f"MujassamAI_{payload['target']}_{timestamp}_{token[:8]}"
    staging.mkdir(mode=0o700, exist_ok=False)
    published = False
    try:
        _progress(4, "Preparing Hunyuan3D Low-VRAM mode for the RTX GPU")
        image_path = Path(payload["image_path"])
        image_info = _prepare_image(image_path, staging)
        source_hash = _sha256(image_path)
        state_root = _engine_state_root()
        shape_root, paint_root, downloaded = _download_models(state_root)

        state: dict[str, Any] = {
            "schema_version": ENGINE_SCHEMA_VERSION,
            "job": payload,
            "staging": str(staging),
            "prepared_image": image_info["prepared_path"],
            "shape_model_root": str(shape_root),
            "paint_model_root": str(paint_root),
            "shape_octree_resolution": 384,
            "shape_num_chunks": 8000,
            "paint_resolution": 2048,
            "shape_mesh": str(staging / "shape.ply"),
            "paint_glb": str(staging / "paint.glb"),
            "final_glb": str(staging / "model.glb"),
        }
        state_path = staging / "engine-state.json"
        _atomic_json(state_path, state)

        shape_code = 1
        for attempt, (octree, chunks) in enumerate(SHAPE_LOW_VRAM_PROFILES):
            state["shape_octree_resolution"] = octree
            state["shape_num_chunks"] = chunks
            _atomic_json(state_path, state)
            shape_code = _run_stage("shape", state_path)
            if shape_code == 0:
                break
            if shape_code != 42:
                break
            if attempt + 1 < len(SHAPE_LOW_VRAM_PROFILES):
                next_octree = SHAPE_LOW_VRAM_PROFILES[attempt + 1][0]
                _progress(43, f"Retrying shape at {next_octree} for the available 8GB VRAM")
        if shape_code != 0:
            message = (
                "Hunyuan3D shape ran out of available GPU/RAM after automatic "
                "low-memory retries. Close GPU programs and try again"
                if shape_code == 42
                else "Hunyuan3D shape generation failed. Check the execution log for the exact error"
            )
            raise EngineError(
                "hunyuan-shape",
                message,
            )

        paint_code = 1
        for attempt, resolution in enumerate(PAINT_LOW_VRAM_RESOLUTIONS):
            state["paint_resolution"] = resolution
            _atomic_json(state_path, state)
            paint_code = _run_stage("paint", state_path)
            if paint_code == 0:
                break
            if paint_code != 42:
                break
            if attempt + 1 < len(PAINT_LOW_VRAM_RESOLUTIONS):
                next_resolution = PAINT_LOW_VRAM_RESOLUTIONS[attempt + 1]
                _progress(72, f"Retrying Hunyuan Paint at {next_resolution}px for 8GB VRAM")
        if paint_code != 0:
            message = (
                "Hunyuan3D Paint ran out of available GPU/RAM after automatic "
                "low-memory retries. Close other programs and try again"
                if paint_code == 42
                else "Hunyuan3D Paint failed. Check the execution log for the exact error"
            )
            raise EngineError(
                "hunyuan-paint",
                message,
            )

        if _run_stage("upscale", state_path) != 0:
            raise EngineError(
                "texture-finalize",
                "The Hunyuan texture was created, but final lossless export failed",
            )

        final_glb_staged = staging / "model.glb"
        if not final_glb_staged.is_file() or final_glb_staged.stat().st_size < 20:
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
                "engine": "Hunyuan3D-2mini + Hunyuan3D-Paint-2.0",
                "engine_mode": payload["engine_mode"],
                "source_commit": SOURCE_COMMIT,
                "shape_repository": SHAPE_REPOSITORY,
                "shape_revision": SHAPE_REVISION,
                "paint_repository": PAINT_REPOSITORY,
                "paint_revision": PAINT_REVISION,
                "low_vram_mode": True,
                "separate_cuda_processes": True,
                "models_downloaded_this_run": downloaded,
                "shape_octree_resolution": state["shape_octree_resolution"],
                "paint_native_resolution": state["paint_resolution"],
                **stage_report,
            },
            "output": {
                "glb": "model.glb",
                "glb_sha256": _sha256(final_glb_staged),
                "prepared_image": "input_prepared.png",
            },
        }
        _atomic_json(staging / "manifest.json", manifest)
        state_path.unlink(missing_ok=True)
        (staging / "shape.ply").unlink(missing_ok=True)
        (staging / "paint.glb").unlink(missing_ok=True)

        _progress(98, "Publishing the completed Hunyuan3D asset")
        if final_dir.exists():
            raise EngineError("output-conflict", "The final output folder already exists")
        staging.rename(final_dir)
        published = True
        result = final_dir / "model.glb"
        _progress(100, "Hunyuan3D model completed")
        _artifact(result)
        return result
    finally:
        if not published and staging.exists():
            try:
                if staging.parent == output_root and staging.name.startswith(".mujassam-"):
                    shutil.rmtree(staging, ignore_errors=True)
            except OSError:
                pass


def _load_state(path: Path) -> dict[str, Any]:
    value = _load_json_file(path)
    staging_value = value.get("staging")
    if not isinstance(staging_value, str):
        raise EngineError("stage-state", "Stage state has no staging path")
    staging = Path(staging_value).resolve(strict=True)
    if not staging.is_dir() or not staging.name.startswith(".mujassam-") or not staging.name.endswith(".partial"):
        raise EngineError("stage-state", "Stage state is outside a Mujassam staging folder")
    if path.resolve(strict=True).parent != staging:
        raise EngineError("stage-state", "Stage state path is inconsistent")
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


def _stage_shape(state: dict[str, Any]) -> int:
    _activate_engine_paths()
    import torch
    from PIL import Image
    from hy3dgen.shapegen import (
        DegenerateFaceRemover,
        FaceReducer,
        FloaterRemover,
        Hunyuan3DDiTFlowMatchingPipeline,
    )

    if not torch.cuda.is_available():
        raise EngineError("cuda-required", "Hunyuan3D requires an NVIDIA CUDA GPU")
    _progress(30, "Loading Hunyuan3D 2mini on the RTX GPU")
    pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
        state["shape_model_root"],
        subfolder=SHAPE_SUBFOLDER,
        use_safetensors=True,
        device="cuda",
        dtype=torch.float16,
    )
    try:
        with Image.open(state["prepared_image"]) as source:
            image = source.convert("RGBA")
        _progress(37, "Generating high-detail Hunyuan geometry")
        try:
            mesh = pipeline(
                image=image,
                num_inference_steps=30,
                guidance_scale=5.0,
                octree_resolution=int(state["shape_octree_resolution"]),
                num_chunks=int(state["shape_num_chunks"]),
                generator=_seed_torch(torch),
                output_type="trimesh",
                enable_pbar=False,
            )[0]
        except torch.cuda.OutOfMemoryError:
            print("MJSTAGEOOM|shape|CUDA memory exhausted", flush=True)
            return 42

        _progress(48, "Cleaning the mesh before texture generation")
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
        if not Path(state["shape_mesh"]).is_file():
            raise EngineError("shape-export", "The intermediate Hunyuan mesh was not exported")
        _progress(52, "Hunyuan geometry completed; freeing GPU memory")
        return 0
    finally:
        pipeline = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _stage_paint(state: dict[str, Any]) -> int:
    _activate_engine_paths()
    import torch
    import trimesh
    from PIL import Image
    from hy3dgen.texgen.pipelines import Hunyuan3DPaintPipeline, Hunyuan3DTexGenConfig

    if not torch.cuda.is_available():
        raise EngineError("cuda-required", "Hunyuan Paint requires an NVIDIA CUDA GPU")
    paint_root = Path(state["paint_model_root"])
    config = Hunyuan3DTexGenConfig(
        str(paint_root / DELIGHT_SUBFOLDER),
        str(paint_root / PAINT_SUBFOLDER),
        PAINT_SUBFOLDER,
    )
    # Load both diffusion pipelines on CPU first. Official model CPU offload
    # then moves only the active modules to the 8 GB GPU.
    config.device = "cpu"
    config.render_size = int(state["paint_resolution"])
    config.texture_size = int(state["paint_resolution"])
    _progress(57, "Loading Hunyuan Paint with official CPU offload")
    pipeline = Hunyuan3DPaintPipeline(config)
    try:
        pipeline.enable_model_cpu_offload(device="cuda")
        config.device = "cuda"
        for wrapper in pipeline.models.values():
            if hasattr(wrapper, "device"):
                wrapper.device = "cuda"
            inner = getattr(wrapper, "pipeline", None)
            if inner is not None and hasattr(inner, "enable_attention_slicing"):
                inner.enable_attention_slicing("max")

        mesh = trimesh.load(state["shape_mesh"], force="mesh", process=False)
        with Image.open(state["prepared_image"]) as source:
            image = source.convert("RGBA")
        _progress(65, "Painting all sides with Hunyuan multiview AI")
        try:
            textured = pipeline(mesh, image=image)
        except torch.cuda.OutOfMemoryError:
            print("MJSTAGEOOM|paint|CUDA memory exhausted", flush=True)
            return 42
        textured.export(state["paint_glb"])
        if not Path(state["paint_glb"]).is_file():
            raise EngineError("paint-export", "Hunyuan Paint did not export a GLB")
        _progress(79, "Hunyuan multiview texture completed; freeing GPU memory")
        return 0
    finally:
        pipeline = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()


def _load_base_worker() -> Any:
    spec = importlib.util.spec_from_file_location("mujassam_base_worker", BASE_WORKER_PATH)
    if spec is None or spec.loader is None:
        raise EngineError("texture-runtime", "Could not load the Mujassam texture module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _stage_upscale(state: dict[str, Any]) -> int:
    _activate_engine_paths()
    import torch
    import trimesh
    from PIL import Image, ImageFilter, ImageOps

    base = _load_base_worker()
    mesh = trimesh.load(state["paint_glb"], force="mesh", process=False)
    material, textures = base._capture_material_textures(mesh)
    payload = state["job"]
    job = base.Job(
        image_path=Path(payload["image_path"]),
        output_dir=Path(payload["output_dir"]),
        engine_mode="hunyuan3d_2mini_low_vram",
        target=payload["target"],
        texture_mode=payload["texture_mode"],
        geometry_mode=payload["geometry_mode"],
        hardware_preset=payload["hardware_preset"],
        source_schema_version=3,
    )
    hardware = {
        "requested": payload["hardware_preset"],
        "resolved": "8gb-low-vram",
        "low_vram_mode": True,
        "ai_tile_size": 256,
    }
    runtime = {
        "torch": torch,
        "Image": Image,
        "ImageFilter": ImageFilter,
        "ImageOps": ImageOps,
    }
    _progress(83, "Restoring the Hunyuan color texture at the selected resolution")
    texture_info, texture_profile = base._apply_selected_texture_profile(
        material,
        textures,
        job=job,
        hardware=hardware,
        runtime=runtime,
    )
    _progress(93, "Exporting and validating one lossless GLB")
    mesh.export(state["final_glb"], include_normals=True)
    glb_info = base._glb_metadata(Path(state["final_glb"]))
    if glb_info["images"] and glb_info["png_images"] != glb_info["images"]:
        raise EngineError("glb-texture", "The final GLB texture is not lossless PNG")
    report = {
        "device": "cuda",
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "gpu_memory_bytes": (
            int(torch.cuda.get_device_properties(0).total_memory)
            if torch.cuda.is_available()
            else 0
        ),
        "torch_version": str(torch.__version__),
        "geometry_faces_target": (
            ROBLOX_READY_FACES
            if payload["geometry_mode"] == "target_ready"
            else ROBLOX_MASTER_FACES
            if payload["geometry_mode"] == "max_detail"
            else None
        ),
        "texture_enhancement": texture_profile,
        "glb_stats": glb_info,
        "texture_records": texture_info,
    }
    _atomic_json(Path(state["staging"]) / "stage-report.json", report)
    _progress(96, "Verified Hunyuan3D GLB is ready")
    return 0


def _self_test() -> int:
    checks = {
        "source_commit": len(SOURCE_COMMIT) == 40,
        "shape_revision": len(SHAPE_REVISION) == 40,
        "paint_revision": len(PAINT_REVISION) == 40,
        "safe_shape": SHAPE_SUBFOLDER == "hunyuan3d-dit-v2-mini",
        "low_vram_default": ROBLOX_READY_FACES == 20_000,
        "quality_first_shape": SHAPE_LOW_VRAM_PROFILES[0][0] == 384,
        "quality_first_paint": PAINT_LOW_VRAM_RESOLUTIONS[0] == 2048,
        "download_retries": MAX_DOWNLOAD_ATTEMPTS >= 3,
        "download_stall_watchdog": DOWNLOAD_STALL_SECONDS >= 60,
        "required_paint_weights": set(PINNED_PAINT_WEIGHTS).issubset(
            PAINT_REQUIRED_FILES
        ),
        "base_worker": BASE_WORKER_PATH.is_file(),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        _error("hunyuan-self-test", "Failed: " + ", ".join(failed))
        return 1
    print("MJHUNYUANSELFTEST|OK|1", flush=True)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mujassam Hunyuan3D low-VRAM worker")
    parser.add_argument("--job", help="Mujassam schema-v3 job JSON")
    parser.add_argument("--stage", choices=("shape", "paint", "upscale"))
    parser.add_argument("--state", help="Trusted stage state JSON")
    parser.add_argument(
        "--download-repository", choices=(SHAPE_REPOSITORY, PAINT_REPOSITORY)
    )
    parser.add_argument("--download-revision")
    parser.add_argument("--download-dir")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.self_test:
        return _self_test()
    try:
        if args.download_repository:
            if not args.download_revision or not args.download_dir:
                raise EngineError(
                    "usage",
                    "--download-revision and --download-dir are required",
                )
            _download_snapshot_once(
                args.download_repository,
                args.download_revision,
                Path(args.download_dir),
            )
            return 0
        if args.stage:
            if not args.state:
                raise EngineError("usage", "--state is required with --stage")
            state_path = Path(args.state)
            state = _load_state(state_path)
            if args.stage == "shape":
                return _stage_shape(state)
            if args.stage == "paint":
                return _stage_paint(state)
            return _stage_upscale(state)
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
            class_name = type(exc).__name__.lower()
            if (
                "outofmemory" in class_name
                or "out of memory" in detail
                or "cuda error: memory allocation" in detail
                or "cuda_error_out_of_memory" in detail
            ):
                print(f"MJSTAGEOOM|{args.stage}|CUDA memory exhausted", flush=True)
                return 42
        if os.environ.get("MUJASSAM_DEBUG") == "1":
            traceback.print_exc(file=sys.stderr)
        _error("hunyuan-internal", f"Unexpected Hunyuan3D failure: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
