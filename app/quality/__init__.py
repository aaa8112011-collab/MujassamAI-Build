"""High-quality texture post-processing used by Mujassam AI.

This package intentionally has no dependency on ``basicsr`` or the
``realesrgan`` PyPI package.  It uses the PyTorch and Pillow copies that are
already shipped by the portable application.
"""

from .realesrgan_x2 import (
    REALESRGAN_X2PLUS,
    REALESRGAN_X4PLUS,
    ModelSpec,
    QualityError,
    RealESRGANx2,
    RRDBNet,
    WeightVerificationError,
    blend_ai_with_lanczos,
    load_realesrgan_x2plus,
    load_realesrgan_x4plus,
    resize_normal_map,
    restore_color_texture,
    self_test,
    verify_weight_file,
)

__all__ = [
    "REALESRGAN_X2PLUS",
    "REALESRGAN_X4PLUS",
    "ModelSpec",
    "QualityError",
    "RealESRGANx2",
    "RRDBNet",
    "WeightVerificationError",
    "blend_ai_with_lanczos",
    "load_realesrgan_x2plus",
    "load_realesrgan_x4plus",
    "resize_normal_map",
    "restore_color_texture",
    "self_test",
    "verify_weight_file",
]
