#!/usr/bin/env python3
"""Apply the reviewed low-RAM safetensors loader to pinned Hunyuan3D source."""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


IMPORT_ANCHOR = "import torch\nimport torch.nn as nn"
IMPORT_REPLACEMENT = """import torch
from accelerate import init_empty_weights
from accelerate.utils import set_module_tensor_to_device
from safetensors import safe_open
import torch.nn as nn"""

ORIGINAL_LOADER = """    @staticmethod
    def from_pretrained(pretrained_model_name_or_path, **kwargs):
        torch_dtype = kwargs.pop('torch_dtype', torch.float32)
        config_path = os.path.join(pretrained_model_name_or_path, 'config.json')
        unet_ckpt_path = os.path.join(pretrained_model_name_or_path, 'diffusion_pytorch_model.bin')
        with open(config_path, 'r', encoding='utf-8') as file:
            config = json.load(file)
        unet = UNet2DConditionModel(**config)
        unet = UNet2p5DConditionModel(unet)
        unet_ckpt = torch.load(unet_ckpt_path, map_location='cpu', weights_only=True)
        unet.load_state_dict(unet_ckpt, strict=True)
        unet = unet.to(torch_dtype)
        return unet"""

PATCHED_LOADER = """    @staticmethod
    def from_pretrained(pretrained_model_name_or_path, **kwargs):
        torch_dtype = kwargs.pop('torch_dtype', torch.float32)
        config_path = os.path.join(pretrained_model_name_or_path, 'config.json')
        checkpoint_path = os.path.join(
            pretrained_model_name_or_path,
            'diffusion_pytorch_model.safetensors',
        )
        with open(config_path, 'r', encoding='utf-8') as file:
            config = json.load(file)

        # The stock loader first materializes the ~7.3 GB FP32 model, deep-copies
        # it, then loads another full state dict. Build the same architecture on
        # the meta device and stream the official FP16 safetensors tensors into
        # CPU memory instead. This is essential on 16 GB RAM / 8 GB VRAM PCs.
        with init_empty_weights(include_buffers=False):
            unet = UNet2DConditionModel(**config)
            unet = UNet2p5DConditionModel(unet)

        expected_shapes = {
            name: tuple(tensor.shape)
            for name, tensor in unet.state_dict().items()
        }
        model_keys = set(expected_shapes)
        with safe_open(checkpoint_path, framework='pt', device='cpu') as checkpoint:
            weight_keys = set(checkpoint.keys())
            if weight_keys != model_keys:
                missing = sorted(model_keys - weight_keys)
                unexpected = sorted(weight_keys - model_keys)
                raise RuntimeError(
                    'Hunyuan Paint UNet checkpoint keys do not match the pinned model; '
                    f'missing={missing[:8]}, unexpected={unexpected[:8]}'
                )
            for name in sorted(model_keys):
                value = checkpoint.get_tensor(name)
                if tuple(value.shape) != expected_shapes[name]:
                    raise RuntimeError(
                        f'Hunyuan Paint UNet tensor shape mismatch for {name}: '
                        f'{tuple(value.shape)} != {expected_shapes[name]}'
                    )
                target_dtype = torch_dtype if value.is_floating_point() else value.dtype
                set_module_tensor_to_device(
                    unet,
                    name,
                    device='cpu',
                    dtype=target_dtype,
                    value=value,
                )
                del value

        remaining_meta = [
            name
            for name, tensor in unet.state_dict().items()
            if tensor.device.type == 'meta'
        ]
        if remaining_meta:
            raise RuntimeError(
                'Hunyuan Paint UNet still has unloaded tensors: '
                + ', '.join(remaining_meta[:8])
            )
        return unet"""


def patch_file(path: Path) -> None:
    original = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    if original.count(IMPORT_ANCHOR) != 1:
        raise RuntimeError("Pinned Hunyuan UNet import anchor changed")
    if original.count(ORIGINAL_LOADER) != 1:
        raise RuntimeError("Pinned Hunyuan UNet loader body changed")
    patched = original.replace(IMPORT_ANCHOR, IMPORT_REPLACEMENT, 1)
    patched = patched.replace(ORIGINAL_LOADER, PATCHED_LOADER, 1)
    required = (
        "from accelerate import init_empty_weights",
        "from accelerate.utils import set_module_tensor_to_device",
        "from safetensors import safe_open",
        "diffusion_pytorch_model.safetensors",
        "weight_keys != model_keys",
    )
    if any(marker not in patched for marker in required):
        raise RuntimeError("Low-memory Hunyuan UNet patch validation failed")

    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(patched)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("module", type=Path)
    args = parser.parse_args()
    patch_file(args.module.resolve(strict=True))
    print(f"Patched Hunyuan Paint UNet loader: {args.module}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
