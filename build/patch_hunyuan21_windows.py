#!/usr/bin/env python3
"""Apply the small, audited Windows/portable patches required by Hunyuan3D-2.1."""

from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(path: Path, old: str, new: str, description: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(
            f"{description}: expected exactly one marker in {path}, found {count}"
        )
    path.write_text(source.replace(old, new), encoding="utf-8", newline="\n")


def patch_source_tree(root: Path) -> None:
    setup_py = root / "hy3dpaint" / "custom_rasterizer" / "setup.py"
    for name in ("rasterizer.cpp", "grid_neighbor.cpp", "rasterizer_gpu.cu"):
        replace_once(
            setup_py,
            f'lib/custom_rasterizer_kernel/{name}',
            f'lib/custom_rasterizer_kernel_for_windows/{name}',
            f"select the official Windows rasterizer source for {name}",
        )
    replace_once(
        setup_py,
        "from setuptools import setup, find_packages\n",
        "# Modified by Mujassam AI: select Tencent's official Windows CUDA "
        "sources for the portable build.\n"
        "from setuptools import setup, find_packages\n",
        "add the required prominent modification notice to setup.py",
    )


def patch_staged_vendor(root: Path) -> None:
    shape_misc = root / "hy3dshape" / "hy3dshape" / "utils" / "misc.py"
    replace_once(
        shape_misc,
        "from omegaconf import OmegaConf, DictConfig, ListConfig\n",
        '''# Modified by Mujassam AI: inference uses reviewed PyYAML wheels only;
# OmegaConf's ANTLR source-only dependency is unnecessary for the portable path.
import yaml

DictConfig = dict
ListConfig = list
''',
        "remove the inference-time OmegaConf dependency",
    )
    replace_once(
        shape_misc,
        '''def get_config_from_file(config_file: str) -> Union[DictConfig, ListConfig]:
    config_file = OmegaConf.load(config_file)

    if 'base_config' in config_file.keys():
        if config_file['base_config'] == "default_base":
            base_config = OmegaConf.create()
            # base_config = get_default_config()
        elif config_file['base_config'].endswith(".yaml"):
            base_config = get_config_from_file(config_file['base_config'])
        else:
            raise ValueError(f"{config_file} must be `.yaml` file or it contains `base_config` key.")

        config_file = {key: value for key, value in config_file if key != "base_config"}

        return OmegaConf.merge(base_config, config_file)

    return config_file
''',
        '''def get_config_from_file(config_file: str) -> Union[DictConfig, ListConfig]:
    with open(config_file, "r", encoding="utf-8") as stream:
        loaded = yaml.safe_load(stream)
    if not isinstance(loaded, (dict, list)):
        raise ValueError(f"{config_file} must contain a YAML mapping or list")
    if isinstance(loaded, dict) and "base_config" in loaded:
        base_reference = loaded.pop("base_config")
        if base_reference == "default_base":
            base_config = {}
        elif isinstance(base_reference, str) and base_reference.endswith(".yaml"):
            base_config = get_config_from_file(base_reference)
        else:
            raise ValueError(f"{config_file} contains an invalid base_config")
        if not isinstance(base_config, dict):
            raise ValueError("base_config must resolve to a YAML mapping")
        base_config.update(loaded)
        return base_config
    return loaded
''',
        "replace OmegaConf config loading with PyYAML",
    )

    mesh_utils = root / "hy3dpaint" / "DifferentiableRenderer" / "mesh_utils.py"
    replace_once(
        mesh_utils,
        "import bpy\n",
        "# Modified by Mujassam AI: Blender is optional in the portable "
        "inference-only build.\ntry:\n    import bpy\nexcept ImportError:\n    bpy = None\n",
        "make Blender an optional export backend",
    )
    replace_once(
        mesh_utils,
        'image_format: str = ".jpg",',
        'image_format: str = ".png",',
        "preserve PBR maps with lossless PNG output",
    )

    texture_pipeline = root / "hy3dpaint" / "textureGenPipeline.py"
    replace_once(
        texture_pipeline,
        "import os\n",
        "# Modified by Mujassam AI: portable local-only PBR output fixes.\nimport os\n",
        "add the required modification notice to textureGenPipeline.py",
    )
    replace_once(
        texture_pipeline,
        'for i in range(len(enhance_images)):',
        'for i in range(len(enhance_images["albedo"])):',
        "resize every selected PBR view instead of only the two map groups",
    )
    replace_once(
        texture_pipeline,
        "self.render.save_mesh(output_mesh_path, downsample=True)",
        "self.render.save_mesh(output_mesh_path, downsample=False)",
        "keep the configured 4K PBR bake",
    )

    image_super = root / "hy3dpaint" / "utils" / "image_super_utils.py"
    replace_once(
        image_super,
        '''class imageSuperNet:
    def __init__(self, config) -> None:
        from realesrgan import RealESRGANer
        from basicsr.archs.rrdbnet_arch import RRDBNet

        model = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32, scale=4)
        upsampler = RealESRGANer(
            scale=4,
            model_path=config.realesrgan_ckpt_path,
            dni_weight=None,
            model=model,
            tile=0,
            tile_pad=10,
            pre_pad=0,
            half=True,
            gpu_id=None,
        )
        self.upsampler = upsampler

    def __call__(self, image):
        output, _ = self.upsampler.enhance(np.array(image))
        output = Image.fromarray(output)
        return output
''',
        '''class imageSuperNet:
    # Modified by Mujassam AI: use the reviewed, self-contained inference-only
    # RRDBNet implementation already shipped by the portable application.  This
    # removes BasicSR/Real-ESRGAN training packages and their unneeded sdists.
    def __init__(self, config) -> None:
        from quality.realesrgan_x2 import load_realesrgan_x4plus

        self.upsampler = load_realesrgan_x4plus(
            config.realesrgan_ckpt_path,
            device=config.device,
            half=True,
        )

    def __call__(self, image):
        return self.upsampler.upscale(image, tile_pad=10)
''',
        "replace the BasicSR/RealESRGAN runtime stack",
    )

    multiview = root / "hy3dpaint" / "utils" / "multiview_utils.py"
    replace_once(
        multiview,
        "import os\n",
        "# Modified by Mujassam AI: load only worker-verified local weights.\nimport os\n",
        "add the required modification notice to multiview_utils.py",
    )
    replace_once(
        multiview,
        "import huggingface_hub\n",
        "",
        "remove runtime network snapshot downloads",
    )
    replace_once(
        multiview,
        "from omegaconf import OmegaConf\n",
        '''# Modified by Mujassam AI: avoid OmegaConf/ANTLR in inference.
import yaml


class _ConfigNode(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def _config_node(value):
    if isinstance(value, dict):
        return _ConfigNode({key: _config_node(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_config_node(item) for item in value]
    return value
''',
        "replace Paint OmegaConf with a small PyYAML mapping",
    )
    replace_once(
        multiview,
        "        cfg = OmegaConf.load(cfg_path)\n",
        '''        with open(cfg_path, "r", encoding="utf-8") as stream:
            cfg = _config_node(yaml.safe_load(stream))
''',
        "load the Paint configuration through PyYAML",
    )
    replace_once(
        multiview,
        '''        model_path = huggingface_hub.snapshot_download(
            repo_id=config.multiview_pretrained_path,
            allow_patterns=["hunyuan3d-paintpbr-v2-1/*"],
        )

        model_path = os.path.join(model_path, "hunyuan3d-paintpbr-v2-1")
''',
        '''        model_path = os.path.abspath(config.multiview_pretrained_path)
        nested_model_path = os.path.join(model_path, "hunyuan3d-paintpbr-v2-1")
        if os.path.isdir(nested_model_path):
            model_path = nested_model_path
        if not os.path.isdir(model_path):
            raise FileNotFoundError(f"Local Hunyuan3D-2.1 Paint model is missing: {model_path}")
''',
        "require the worker-verified local Paint snapshot",
    )
    replace_once(
        multiview,
        "            torch_dtype=torch.float16\n        )",
        "            torch_dtype=torch.float16,\n            local_files_only=True,\n        )",
        "forbid model-loader network fallback",
    )

    paint_package = root / "hy3dpaint" / "hunyuanpaintpbr" / "__init__.py"
    replace_once(
        paint_package,
        "from .unet.model import HunyuanPaint\n",
        "# Modified by Mujassam AI: the portable inference runtime omits the "
        "training-only PyTorch Lightning wrapper.\n",
        "remove the unused PyTorch Lightning training wrapper",
    )
    replace_once(
        paint_package,
        "    'HunyuanPaint',\n",
        "",
        "remove the unused training wrapper export",
    )

    simplify = root / "hy3dpaint" / "utils" / "simplify_mesh_utils.py"
    replace_once(
        simplify,
        "import trimesh\n",
        "# Modified by Mujassam AI: make the unused remesher optional.\nimport trimesh\n",
        "add the required modification notice to simplify_mesh_utils.py",
    )
    replace_once(
        simplify,
        "import pymeshlab\n",
        "",
        "avoid importing the optional remesher during normal PBR inference",
    )
    replace_once(
        simplify,
        "def mesh_simplify_trimesh(inputpath, outputpath, target_count=40000):\n",
        "def mesh_simplify_trimesh(inputpath, outputpath, target_count=40000):\n    import pymeshlab\n",
        "load the optional remesher only when remeshing is explicitly requested",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("source", "vendor"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.mode == "source":
        patch_source_tree(root)
    else:
        patch_staged_vendor(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
