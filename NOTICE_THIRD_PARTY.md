# Third-party notices

The portable bundle is assembled from upstream packages during GitHub Actions. Their own license files are copied into `licenses/` in the generated bundle.

## Tencent Hunyuan3D 2.0

- Source: https://github.com/Tencent-Hunyuan/Hunyuan3D-2
- Shape model: https://huggingface.co/tencent/Hunyuan3D-2mini
- Paint models: https://huggingface.co/tencent/Hunyuan3D-2
- License: TENCENT HUNYUAN 3D 2.0 COMMUNITY LICENSE AGREEMENT
- Pinned source commit: f8db63096c8282cb27354314d896feba5ba6ff8a

The Hunyuan update includes the complete upstream LICENSE and NOTICE beside the
engine adapter. Model weights are downloaded from Tencent's official model
repositories after explicit user acceptance and are not redistributed in the
update. Mujassam AI's adapter and low-VRAM orchestration are modifications; see
app/engines/hunyuan2/MODIFICATIONS.txt.

## Stability AI SPAR3D

- Source: https://github.com/Stability-AI/stable-point-aware-3d
- Model: https://huggingface.co/stabilityai/stable-point-aware-3d
- License: Stability AI Community License

Required attribution notice:

> This product includes Stability AI Materials. 3D Powered by Stability AI.

## Other major components

- Python — Python Software Foundation License.
- PyTorch and TorchVision — BSD-style licenses.
- OpenAI CLIP — MIT License.
- AlphaCLIP — Apache-2.0 License.
- transparent-background / InSPyReNet — MIT license for the package; see its bundled notice and model information.
- Real-ESRGAN x2plus — BSD-3-Clause, Copyright (c) 2021 Xintao Wang. Mujassam AI uses a self-contained PyTorch implementation of the official RRDBNet x2 architecture and accepts only the official `params_ema` checkpoint after exact size/SHA-256 verification. See `licenses/Real-ESRGAN-BSD-3-Clause.txt`.
- BasicSR RRDBNet architecture reference — Apache-2.0, Copyright 2018-2022 BasicSR Authors. See `licenses/BasicSR-Apache-2.0.txt`.
- trimesh, Pillow, NumPy, Hugging Face Hub and Transformers — see the individual license files included in the generated bundle.

Mujassam AI is an independent project and is not affiliated with Tencent,
Stability AI, Roblox Corporation, Epic Games, NVIDIA, Microsoft, OpenAI, or
Hugging Face.
