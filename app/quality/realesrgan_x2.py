"""Memory-bounded Real-ESRGAN x2 texture restoration.

The network layout matches the official ``RealESRGAN_x2plus`` checkpoint:
RRDBNet(3, 3, 64, 23, 32, scale=2).  Only the official ``params_ema`` tensor
mapping is accepted.  The model file is verified before deserialization and
``torch.load(weights_only=True)`` is mandatory; the code never falls back to
unsafe pickle loading.

The module is intentionally self-contained.  It depends only on PyTorch,
NumPy and Pillow, all of which already exist in the portable SPAR3D runtime.
It does not download a model and it does not import BasicSR/Real-ESRGAN.
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch
from PIL import Image
from torch import Tensor, nn
from torch.nn import functional as F


class QualityError(RuntimeError):
    """Base class for expected texture-quality failures."""


class WeightVerificationError(QualityError):
    """Raised when the AI model is missing, truncated or has the wrong hash."""


@dataclass(frozen=True)
class ModelSpec:
    file_name: str
    download_url: str
    byte_count: int
    sha256: str
    scale: int = 2


REALESRGAN_X2PLUS = ModelSpec(
    file_name="RealESRGAN_x2plus.pth",
    download_url=(
        "https://github.com/xinntao/Real-ESRGAN/releases/download/"
        "v0.2.1/RealESRGAN_x2plus.pth"
    ),
    byte_count=67_061_725,
    sha256="49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb",
    scale=2,
)


def verify_weight_file(
    path: str | os.PathLike[str],
    spec: ModelSpec = REALESRGAN_X2PLUS,
) -> Path:
    """Verify the exact official model size and SHA-256, returning its path."""

    model_path = Path(path)
    try:
        stat = model_path.stat()
    except OSError as exc:
        raise WeightVerificationError(f"AI texture model is missing: {model_path}") from exc
    if not model_path.is_file():
        raise WeightVerificationError(f"AI texture model is not a file: {model_path}")
    if stat.st_size != spec.byte_count:
        raise WeightVerificationError(
            f"AI texture model has the wrong size ({stat.st_size:,} bytes; "
            f"expected {spec.byte_count:,})."
        )

    digest = hashlib.sha256()
    try:
        with model_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise WeightVerificationError(f"Could not read AI texture model: {exc}") from exc

    actual = digest.hexdigest()
    if actual != spec.sha256:
        raise WeightVerificationError(
            "AI texture model failed its SHA-256 safety check "
            f"({actual}; expected {spec.sha256})."
        )
    return model_path


def _pixel_unshuffle(value: Tensor, scale: int) -> Tensor:
    batch, channels, height, width = value.shape
    if height % scale or width % scale:
        raise ValueError(f"Input dimensions must be divisible by {scale}")
    value = value.view(
        batch,
        channels,
        height // scale,
        scale,
        width // scale,
        scale,
    )
    value = value.permute(0, 1, 3, 5, 2, 4).contiguous()
    return value.view(batch, channels * scale * scale, height // scale, width // scale)


class ResidualDenseBlock(nn.Module):
    """Five-convolution residual dense block used by official RRDBNet."""

    def __init__(self, num_feat: int = 64, num_grow_ch: int = 32) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, value: Tensor) -> Tensor:
        x1 = self.lrelu(self.conv1(value))
        x2 = self.lrelu(self.conv2(torch.cat((value, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((value, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((value, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((value, x1, x2, x3, x4), 1))
        return x5 * 0.2 + value


class RRDB(nn.Module):
    """Residual-in-residual dense block."""

    def __init__(self, num_feat: int, num_grow_ch: int = 32) -> None:
        super().__init__()
        self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

    def forward(self, value: Tensor) -> Tensor:
        output = self.rdb1(value)
        output = self.rdb2(output)
        output = self.rdb3(output)
        return output * 0.2 + value


class RRDBNet(nn.Module):
    """BasicSR-compatible RRDBNet used by the official x2plus weights."""

    def __init__(
        self,
        num_in_ch: int = 3,
        num_out_ch: int = 3,
        num_feat: int = 64,
        num_block: int = 23,
        num_grow_ch: int = 32,
        scale: int = 2,
    ) -> None:
        super().__init__()
        if scale not in (1, 2, 4):
            raise ValueError("RRDBNet scale must be 1, 2 or 4")
        self.scale = scale
        first_channels = num_in_ch * (4 if scale == 2 else 16 if scale == 1 else 1)
        self.conv_first = nn.Conv2d(first_channels, num_feat, 3, 1, 1)
        self.body = nn.Sequential(
            *(RRDB(num_feat=num_feat, num_grow_ch=num_grow_ch) for _ in range(num_block))
        )
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, value: Tensor) -> Tensor:
        if self.scale == 2:
            feature_input = _pixel_unshuffle(value, 2)
        elif self.scale == 1:
            feature_input = _pixel_unshuffle(value, 4)
        else:
            feature_input = value
        feature = self.conv_first(feature_input)
        body_feature = self.conv_body(self.body(feature))
        feature = feature + body_feature
        feature = self.lrelu(
            self.conv_up1(F.interpolate(feature, scale_factor=2, mode="nearest"))
        )
        feature = self.lrelu(
            self.conv_up2(F.interpolate(feature, scale_factor=2, mode="nearest"))
        )
        return self.conv_last(self.lrelu(self.conv_hr(feature)))


def _safe_checkpoint_state(path: Path) -> Mapping[str, Tensor]:
    try:
        checkpoint: Any = torch.load(path, map_location="cpu", weights_only=True)
    except TypeError as exc:
        raise QualityError(
            "This PyTorch build cannot safely load AI weights. "
            "A version supporting torch.load(weights_only=True) is required."
        ) from exc
    except Exception as exc:
        raise QualityError(f"Could not safely load the AI texture model: {exc}") from exc

    if not isinstance(checkpoint, Mapping):
        raise QualityError("The AI texture checkpoint is not a tensor mapping.")
    state = checkpoint.get("params_ema")
    if not isinstance(state, Mapping):
        raise QualityError("The verified checkpoint does not contain official params_ema weights.")
    if not state or any(not isinstance(key, str) for key in state):
        raise QualityError("The params_ema weight mapping is empty or malformed.")
    if any(not isinstance(value, Tensor) for value in state.values()):
        raise QualityError("The params_ema mapping contains a non-tensor value.")
    return state


def _resolve_device(device: str | torch.device | None) -> torch.device:
    if device is None:
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    resolved = torch.device(device)
    if resolved.type == "cuda" and not torch.cuda.is_available():
        raise QualityError("CUDA was requested for texture restoration but is unavailable.")
    return resolved


def load_realesrgan_x2plus(
    model_path: str | os.PathLike[str],
    *,
    device: str | torch.device | None = None,
    half: bool = True,
) -> "RealESRGANx2":
    """Verify and load official x2plus weights into a memory-bounded runner."""

    verified = verify_weight_file(model_path)
    state = _safe_checkpoint_state(verified)
    model = RRDBNet(3, 3, 64, 23, 32, scale=2)
    try:
        incompatible = model.load_state_dict(state, strict=True)
    except Exception as exc:
        raise QualityError(f"The verified AI weights do not match RRDBNet x2plus: {exc}") from exc
    if incompatible.missing_keys or incompatible.unexpected_keys:
        raise QualityError("The verified AI weights did not load strictly.")
    del state
    gc.collect()
    resolved = _resolve_device(device)
    use_half = bool(half and resolved.type == "cuda")
    model.eval().requires_grad_(False).to(device=resolved)
    if use_half:
        model.half()
    return RealESRGANx2(model=model, device=resolved, half=use_half, scale=2)


def _is_cuda_oom(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "out of memory",
            "cublas_status_alloc_failed",
            "cuda error: memory allocation",
            "hip out of memory",
        )
    )


def _alpha_resample() -> int:
    return Image.Resampling.LANCZOS


def _source_alpha(image: Image.Image) -> Image.Image | None:
    if "A" in image.getbands() or "transparency" in image.info:
        return image.convert("RGBA").getchannel("A")
    return None


class RealESRGANx2:
    """Run x2 restoration with CPU assembly and automatic CUDA OOM fallback."""

    def __init__(
        self,
        *,
        model: nn.Module,
        device: str | torch.device,
        half: bool,
        scale: int = 2,
    ) -> None:
        self.model = model
        self.device = _resolve_device(device)
        self.half = bool(half and self.device.type == "cuda")
        self.scale = int(scale)
        if self.scale != 2:
            raise ValueError("This runner supports the x2 model only")

    def _automatic_tile_size(self) -> int:
        if self.device.type != "cuda":
            return 128
        try:
            gib = torch.cuda.get_device_properties(self.device).total_memory / 1024**3
        except Exception:
            return 192
        if gib >= 20:
            return 512
        if gib >= 12:
            return 384
        if gib >= 7:
            return 256
        return 192

    @staticmethod
    def _tile_candidates(requested: int, minimum: int) -> list[int]:
        requested = max(2, int(requested) // 2 * 2)
        minimum = max(32, int(minimum) // 2 * 2)
        values: list[int] = []
        current = requested
        while current >= minimum:
            if current not in values:
                values.append(current)
            current = (current // 2) // 2 * 2
        if minimum not in values:
            values.append(minimum)
        return values

    def _run_tile(self, tile: Tensor) -> Tensor:
        dtype = torch.float16 if self.half else torch.float32
        tile = tile.to(device=self.device, dtype=dtype, non_blocking=True)
        with torch.inference_mode():
            result = self.model(tile)
        return result.detach().float().clamp_(0.0, 1.0).cpu()

    def _infer_tiled(self, image: Tensor, tile_size: int, tile_pad: int) -> np.ndarray:
        _, _, height, width = image.shape
        output = np.empty((height * self.scale, width * self.scale, 3), dtype=np.uint8)
        tiles_x = math.ceil(width / tile_size)
        tiles_y = math.ceil(height / tile_size)
        for y_index in range(tiles_y):
            y0 = y_index * tile_size
            y1 = min(y0 + tile_size, height)
            py0 = max(0, y0 - tile_pad)
            py1 = min(height, y1 + tile_pad)
            for x_index in range(tiles_x):
                x0 = x_index * tile_size
                x1 = min(x0 + tile_size, width)
                px0 = max(0, x0 - tile_pad)
                px1 = min(width, x1 + tile_pad)

                patch = image[:, :, py0:py1, px0:px1]
                prediction = self._run_tile(patch)
                crop_y0 = (y0 - py0) * self.scale
                crop_y1 = crop_y0 + (y1 - y0) * self.scale
                crop_x0 = (x0 - px0) * self.scale
                crop_x1 = crop_x0 + (x1 - x0) * self.scale
                prediction = prediction[0, :, crop_y0:crop_y1, crop_x0:crop_x1]
                pixels = (
                    prediction.permute(1, 2, 0)
                    .mul_(255.0)
                    .round_()
                    .to(torch.uint8)
                    .numpy()
                )
                output[
                    y0 * self.scale : y1 * self.scale,
                    x0 * self.scale : x1 * self.scale,
                    :,
                ] = pixels
                del patch, prediction, pixels
        return output

    def upscale(
        self,
        image: Image.Image,
        *,
        tile_size: int | None = None,
        tile_pad: int = 16,
        minimum_tile: int = 64,
    ) -> Image.Image:
        """Restore and upscale a Pillow RGB/RGBA texture exactly two times.

        Alpha is never sent through the RGB network.  It is independently
        resized with Lanczos and restored after inference, preventing RGB/alpha
        channel corruption.  CUDA OOM retries progressively smaller tiles.
        """

        if not isinstance(image, Image.Image):
            raise TypeError("image must be a PIL.Image.Image")
        original_width, original_height = image.size
        if original_width < 1 or original_height < 1:
            raise ValueError("image dimensions must be positive")
        source_alpha = _source_alpha(image)
        rgb_array = np.asarray(image.convert("RGB"), dtype=np.uint8).copy()
        tensor = (
            torch.from_numpy(rgb_array)
            .permute(2, 0, 1)
            .unsqueeze(0)
            .float()
            .div_(255.0)
        )
        del rgb_array

        pad_height = (-original_height) % 2
        pad_width = (-original_width) % 2
        if pad_height or pad_width:
            pad_mode = "reflect" if original_width > 1 and original_height > 1 else "replicate"
            tensor = F.pad(tensor, (0, pad_width, 0, pad_height), mode=pad_mode)

        requested = tile_size or self._automatic_tile_size()
        pad = max(0, int(tile_pad) // 2 * 2)
        last_oom: RuntimeError | None = None
        result_array: np.ndarray | None = None
        for candidate in self._tile_candidates(requested, minimum_tile):
            try:
                result_array = self._infer_tiled(tensor, candidate, pad)
                break
            except RuntimeError as exc:
                if self.device.type != "cuda" or not _is_cuda_oom(exc):
                    raise
                last_oom = exc
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                gc.collect()
        if result_array is None:
            raise QualityError(
                "AI texture restoration ran out of VRAM even at the minimum tile size."
            ) from last_oom

        result_array = result_array[
            : original_height * self.scale,
            : original_width * self.scale,
            :,
        ]
        output = Image.fromarray(result_array, mode="RGB")
        if source_alpha is not None:
            alpha = source_alpha.resize(output.size, _alpha_resample())
            output.putalpha(alpha)
        return output

    def close(self) -> None:
        """Release model memory before another large 3D/texture stage."""

        try:
            self.model.to("cpu")
        finally:
            if self.device.type == "cuda" and torch.cuda.is_available():
                torch.cuda.empty_cache()
            gc.collect()


def _srgb_to_linear(values: np.ndarray) -> np.ndarray:
    return np.where(
        values <= 0.04045,
        values / 12.92,
        ((values + 0.055) / 1.055) ** 2.4,
    )


def _linear_to_srgb(values: np.ndarray) -> np.ndarray:
    return np.where(
        values <= 0.0031308,
        values * 12.92,
        1.055 * np.maximum(values, 0.0) ** (1.0 / 2.4) - 0.055,
    )


def blend_ai_with_lanczos(
    source: Image.Image,
    ai_result: Image.Image,
    *,
    ai_strength: float = 0.82,
    target_size: tuple[int, int] | None = None,
) -> Image.Image:
    """Blend restored detail with a color-faithful Lanczos baseline.

    Blending is performed in linear light.  This avoids the dark halos and
    color shifts caused by naïve sRGB blending.  Alpha always comes from the
    source image resized to the final size rather than from the RGB network.
    """

    strength = float(ai_strength)
    if not 0.0 <= strength <= 1.0:
        raise ValueError("ai_strength must be between 0 and 1")
    size = target_size or ai_result.size
    if size[0] < 1 or size[1] < 1:
        raise ValueError("target_size must contain positive dimensions")
    baseline = source.convert("RGB").resize(size, Image.Resampling.LANCZOS)
    restored = ai_result.convert("RGB")
    if restored.size != size:
        restored = restored.resize(size, Image.Resampling.LANCZOS)

    # Work in strips: a full 8192² float32 RGB array is about 768 MiB, and a
    # naïve linear-light blend needs several such arrays at once.  Keeping the
    # source images as uint8 and converting 256 rows at a time makes 8K export
    # practical on a 16 GB system.
    base_u8 = np.asarray(baseline, dtype=np.uint8)
    ai_u8 = np.asarray(restored, dtype=np.uint8)
    output_u8 = np.empty((size[1], size[0], 3), dtype=np.uint8)
    for y0 in range(0, size[1], 256):
        y1 = min(size[1], y0 + 256)
        base = base_u8[y0:y1].astype(np.float32) / 255.0
        ai = ai_u8[y0:y1].astype(np.float32) / 255.0
        mixed = (1.0 - strength) * _srgb_to_linear(base)
        mixed += strength * _srgb_to_linear(ai)
        mixed = np.clip(_linear_to_srgb(mixed), 0.0, 1.0)
        output_u8[y0:y1] = np.rint(mixed * 255.0).astype(np.uint8)
    rgb = Image.fromarray(output_u8, mode="RGB")
    source_alpha = _source_alpha(source)
    if source_alpha is not None:
        rgb.putalpha(source_alpha.resize(size, _alpha_resample()))
    return rgb


def restore_color_texture(
    source: Image.Image,
    *,
    target_size: tuple[int, int],
    upscaler: RealESRGANx2 | None,
    ai_strength: float = 0.82,
    tile_size: int | None = None,
    tile_pad: int = 16,
    max_ai_passes: int = 2,
) -> Image.Image:
    """Create a color texture using zero, one or two verified x2 AI passes.

    Passing ``upscaler=None`` is an explicit, safe Lanczos fallback.  The final
    AI result is blended against a direct source-to-target Lanczos resize, so
    repeated x2 passes do not completely replace the source colors.
    """

    width, height = (int(target_size[0]), int(target_size[1]))
    if width < 1 or height < 1:
        raise ValueError("target_size must contain positive dimensions")
    if max_ai_passes < 0:
        raise ValueError("max_ai_passes cannot be negative")
    if upscaler is None or max_ai_passes == 0 or ai_strength == 0:
        output = source.convert("RGB").resize((width, height), Image.Resampling.LANCZOS)
        source_alpha = _source_alpha(source)
        if source_alpha is not None:
            output.putalpha(source_alpha.resize((width, height), _alpha_resample()))
        return output

    restored = source
    passes = 0
    while passes < max_ai_passes and (restored.width < width or restored.height < height):
        restored = upscaler.upscale(restored, tile_size=tile_size, tile_pad=tile_pad)
        passes += 1
    return blend_ai_with_lanczos(
        source,
        restored,
        ai_strength=ai_strength,
        target_size=(width, height),
    )


def resize_normal_map(
    image: Image.Image,
    target_size: tuple[int, int],
    *,
    flip_green: bool = False,
) -> Image.Image:
    """Resize a tangent-space normal map and re-normalize every vector.

    Unlike a generic image upscaler, this preserves unit-length normal vectors
    and never hallucinates color detail into a data texture.
    """

    width, height = (int(target_size[0]), int(target_size[1]))
    if width < 1 or height < 1:
        raise ValueError("target_size must contain positive dimensions")
    source_alpha = _source_alpha(image)
    interpolated = image.convert("RGB").resize((width, height), Image.Resampling.BICUBIC)
    interpolated_u8 = np.asarray(interpolated, dtype=np.uint8)
    encoded = np.empty((height, width, 3), dtype=np.uint8)
    for y0 in range(0, height, 256):
        y1 = min(height, y0 + 256)
        vectors = interpolated_u8[y0:y1].astype(np.float32) / 127.5 - 1.0
        if flip_green:
            vectors[:, :, 1] *= -1.0
        lengths = np.linalg.norm(vectors, axis=2, keepdims=True)
        invalid = lengths < 1e-8
        vectors /= np.maximum(lengths, 1e-8)
        if np.any(invalid):
            vectors[invalid.repeat(3, axis=2)] = 0.0
            vectors[:, :, 2][invalid[:, :, 0]] = 1.0
        encoded[y0:y1] = np.rint(
            np.clip((vectors + 1.0) * 127.5, 0.0, 255.0)
        ).astype(np.uint8)
    output = Image.fromarray(encoded, mode="RGB")
    if source_alpha is not None:
        output.putalpha(source_alpha.resize((width, height), _alpha_resample()))
    return output


def self_test() -> dict[str, Any]:
    """Exercise architecture, tiling, alpha, blending and normals without weights."""

    torch.manual_seed(7)
    tiny = RRDBNet(3, 3, num_feat=8, num_block=1, num_grow_ch=4, scale=2).eval()
    with torch.inference_mode():
        shape = tuple(tiny(torch.rand(1, 3, 8, 10)).shape)
    if shape != (1, 3, 16, 20):
        raise AssertionError(f"RRDBNet x2 shape mismatch: {shape}")

    class _NearestX2(nn.Module):
        def forward(self, value: Tensor) -> Tensor:
            return F.interpolate(value, scale_factor=2, mode="nearest")

    source_pixels = np.zeros((35, 37, 4), dtype=np.uint8)
    source_pixels[:, :, 0] = np.arange(37, dtype=np.uint8)[None, :] * 6
    source_pixels[:, :, 1] = np.arange(35, dtype=np.uint8)[:, None] * 7
    source_pixels[:, :, 2] = 90
    source_pixels[:, :, 3] = np.linspace(0, 255, 37, dtype=np.uint8)[None, :]
    source = Image.fromarray(source_pixels, mode="RGBA")
    runner = RealESRGANx2(model=_NearestX2(), device="cpu", half=False)
    tiled = runner.upscale(source, tile_size=32, tile_pad=4, minimum_tile=32)
    if tiled.size != (74, 70) or tiled.mode != "RGBA":
        raise AssertionError("Tiled RGB/alpha output has the wrong shape or mode")
    expected_rgb = np.repeat(np.repeat(source_pixels[:, :, :3], 2, axis=0), 2, axis=1)
    if not np.array_equal(np.asarray(tiled.convert("RGB")), expected_rgb):
        raise AssertionError("Tiled core stitching changed RGB pixels")

    blended = blend_ai_with_lanczos(source, tiled, ai_strength=0.5, target_size=tiled.size)
    if blended.mode != "RGBA" or blended.size != tiled.size:
        raise AssertionError("AI/Lanczos blend did not preserve RGBA")

    flat = np.zeros((5, 6, 3), dtype=np.uint8)
    flat[:, :, 0] = 128
    flat[:, :, 1] = 128
    flat[:, :, 2] = 255
    normal = resize_normal_map(Image.fromarray(flat, "RGB"), (17, 13))
    decoded = np.asarray(normal, dtype=np.float32) / 127.5 - 1.0
    length_error = float(np.max(np.abs(np.linalg.norm(decoded, axis=2) - 1.0)))
    if length_error > 0.02:
        raise AssertionError(f"Normal vectors were not normalized: {length_error}")

    return {
        "ok": True,
        "architecture_output": list(shape),
        "tiled_rgba_output": list(tiled.size),
        "normal_max_unit_error": round(length_error, 6),
        "weight_required": False,
    }


if __name__ == "__main__":
    print(json.dumps(self_test(), ensure_ascii=False, sort_keys=True))
