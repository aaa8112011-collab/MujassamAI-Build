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
