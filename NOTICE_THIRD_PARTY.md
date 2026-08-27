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

## Tencent Hunyuan3D 2.1 (optional PBR engine)

- Source: https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1
- Shape/PBR model: https://huggingface.co/tencent/Hunyuan3D-2.1
- License: TENCENT HUNYUAN 3D 2.1 COMMUNITY LICENSE AGREEMENT
- Pinned source commit: `82920d643c0dc2f7bfd7255f45f62d386edfe60c`
- Pinned model revision: `0b94677654c57bb9a6b6845cd7b704ccf551d327`

The public Mujassam AI repository contains only an independent adapter. It does
not commit or redistribute Tencent source code or model weights. An eligible
user downloads the official pinned materials directly from Tencent after an
explicit, versioned acceptance of the Hunyuan3D 2.1 license and Acceptable Use
Policy. The complete current license, upstream Notice, exact checkpoint hashes,
and modification notice are included in `app/engines/hunyuan21/`.

Because the license permits use, reproduction, and distribution only inside its
defined Territory, hosted pull-request validation checks only the repository's
adapter and never fetches or materializes Tencent source or weights. An optional
full-build validation must be manually requested on a Territory-approved
self-hosted runner after explicit license/AUP acceptance and Territory
confirmation. The workflow does not upload an artifact or publish a Release;
its output remains local to the approved self-hosted runner, whose operator is
responsible for retention, protection, and deletion. A global GitHub Release is
not a Territory control. Operational builds must materialize the pinned source
locally after acceptance, or be delivered through a channel that actually
restricts recipients to the Territory.
The full-build path also fails closed before source fetch or package execution
until a complete Windows direct-and-transitive dependency lock with SHA-256
hashes is present.

For a purely personal build that remains on the user's local device and is not
provided as a product, service, integration, or functionality to any third
party, Mujassam AI records `usage_scope=personal_local_only`, stores no provider
legal name, and records `distribution_authorized=false`. This mode does not
authorize redistribution. A third-party product/service build uses the separate
`third_party_provider` scope and still requires the actual provider identity and
non-affiliation disclosure described below.

The Hunyuan3D 2.1 license defines a Territory that excludes the European Union,
United Kingdom, and South Korea; it also includes use restrictions and separate
commercial terms for a licensee whose products or services exceeded one million
monthly active users on the model release date. Consult the complete bundled
license rather than relying on this summary.

Before any service, product, or permitted Territory-restricted distribution,
its publisher must configure and prominently disclose the actual provider's
full legal name. Tencent is not affiliated with, associated with, sponsoring,
or endorsing this independent integration.

### Hunyuan3D 2.1 helper models

- DINOv2 giant — https://huggingface.co/facebook/dinov2-giant — Apache-2.0;
  pinned revision `611a9d42f2335e0f921f1e313ad3c1b7178d206d`; see
  `licenses/DINOv2-Apache-2.0.txt`.
- Real-ESRGAN x4plus — https://github.com/xinntao/Real-ESRGAN — BSD-3-Clause;
  official v0.1.0 checkpoint SHA-256
  `4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1`.

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
