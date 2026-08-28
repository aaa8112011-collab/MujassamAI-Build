#!/usr/bin/env python3
"""Model-free checks for the repository-owned Hunyuan3D-2.1 adapter."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "app" / "engines" / "hunyuan21"
WORKER = ENGINE / "hunyuan21_worker.py"
WORKFLOW = ROOT / ".github" / "workflows" / "hunyuan21-pbr-release.yml"
BUILD_SCRIPT = ROOT / "build" / "build-hunyuan21-update.ps1"
RASTERIZER_HOTFIX = ROOT / "build" / "hotfix-hunyuan21-rasterizer.ps1"
PAINT_8GB_HOTFIX = ROOT / "build" / "hotfix-hunyuan21-paint-8gb.ps1"
LOCAL_BUILD_SCRIPT = ROOT / "build" / "build-hunyuan21-local.ps1"
LOCAL_BUILD_LAUNCHER = ROOT / "build" / "Build-Hunyuan21-Local.cmd"
LOCAL_BUILD_RECOVERY = ROOT / "build" / "resume-hunyuan21-local.ps1"
LOCAL_INSTALLER = ROOT / "installer" / "install-hunyuan21-local.ps1"
LOCAL_RESTORE = ROOT / "installer" / "restore-hunyuan21-local.ps1"
LOCAL_REMOVER = ROOT / "installer" / "remove-hunyuan21-local.ps1"
LOCAL_GUIDE = ROOT / "docs" / "HUNYUAN21-LOCAL-UAE.md"
SOURCE_COMMIT = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
CI_PROVIDER = "CI validation build — Hunyuan3D 2.1 disabled"
REQUIRED_DISTRIBUTION_NOTICE = (
    "Tencent Hunyuan 3D 2.1 is licensed under the Tencent Hunyuan 3D 2.1 "
    "Community License Agreement, Copyright © 2025 Tencent. All Rights Reserved. "
    "The trademark rights of “Tencent Hunyuan” are owned by Tencent or its affiliate."
)
LOCK_ENTRY_RE = re.compile(
    r"^([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})$"
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def canonical_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def read_complete_sha256_lock(
    path: Path, label: str
) -> dict[str, tuple[str, str, str]]:
    require(path.is_file(), f"Missing {label} dependency lock: {path}")
    entries: dict[str, tuple[str, str, str]] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = LOCK_ENTRY_RE.fullmatch(line)
        require(
            match is not None,
            f"{label} lock entry is not one exact pin and SHA-256 at {path}:{number}",
        )
        display_name, version, digest = match.groups()
        name = canonical_package_name(display_name)
        require(name not in entries, f"Duplicate package in {label} lock: {display_name}")
        entries[name] = (display_name, version, digest)
    require(entries, f"{label} dependency lock contains no packages: {path}")
    return entries


def normalized_lf_sha256(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalized_lf_bytes(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8-sig")
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def literal_constants(path: Path) -> dict[str, object]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value_node = node.value
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            try:
                values[target.id] = ast.literal_eval(value_node)
            except (TypeError, ValueError):
                pass
    return values


def python_function_source(path: Path, name: str) -> str:
    """Return one top-level function without depending on fixed line numbers."""

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            require(node.end_lineno is not None, f"Could not bound {name} in {path}")
            return "\n".join(source.splitlines()[node.lineno - 1 : node.end_lineno])
    raise RuntimeError(f"Missing required function {name} in {path}")


def main() -> int:
    manifest_path = ENGINE / "ENGINE-MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == 2, "Unexpected engine manifest schema")
    source = manifest.get("source", {})
    require(source.get("commit") == SOURCE_COMMIT, "Engine source commit is not pinned")
    require(
        source.get("repository")
        == "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1",
        "Engine source repository changed",
    )
    require(
        source.get("global_public_release_of_upstream_source") is False,
        "Public upstream-source release must remain disabled",
    )

    license_path = ENGINE / str(source.get("license_file", ""))
    notice_path = ENGINE / str(source.get("upstream_notice_file", ""))
    require(license_path.is_file(), "Pinned upstream license is missing")
    require(notice_path.is_file(), "Pinned upstream notice is missing")
    require(
        normalized_lf_sha256(license_path) == source.get("license_file_sha256"),
        "Packaged license hash does not match ENGINE-MANIFEST.json",
    )
    require(
        normalized_lf_sha256(notice_path) == source.get("upstream_notice_file_sha256"),
        "Packaged upstream notice hash does not match ENGINE-MANIFEST.json",
    )
    for path, key in (
        (license_path, "upstream_license_git_blob_sha256"),
        (notice_path, "upstream_notice_git_blob_sha256"),
    ):
        packaged = normalized_lf_bytes(path)
        require(
            packaged.endswith(b"\n")
            and hashlib.sha256(packaged[:-1]).hexdigest() == source.get(key),
            f"{path.name} is not the exact upstream Git blob plus one packaging LF",
        )

    constants = literal_constants(WORKER)
    models = manifest.get("models", {})
    h21_model = models.get("hunyuan3d_2_1", {})
    dino_model = models.get("dinov2_giant", {})
    x4_model = models.get("realesrgan_x4plus", {})
    require(constants.get("SOURCE_COMMIT") == SOURCE_COMMIT, "Worker source pin drifted")
    require(
        constants.get("PERSONAL_LOCAL_USAGE_SCOPE") == "personal_local_only"
        and constants.get("THIRD_PARTY_USAGE_SCOPE") == "third_party_provider"
        and constants.get("CI_PROVIDER_SENTINEL") == CI_PROVIDER,
        "Worker usage-scope constants drifted",
    )
    require(
        constants.get("MODEL_REVISION") == h21_model.get("revision"),
        "Worker Hunyuan model revision drifted",
    )
    require(
        constants.get("DINO_REVISION") == dino_model.get("revision"),
        "Worker DINO revision drifted",
    )
    require(
        constants.get("LICENSE_SHA256") == source.get("license_file_sha256"),
        "Worker license acceptance hash drifted",
    )
    require(
        constants.get("REALESRGAN_X4_BYTES") == x4_model.get("bytes")
        and constants.get("REALESRGAN_X4_SHA256") == x4_model.get("sha256"),
        "Worker RealESRGAN pin drifted",
    )
    runtime_abi = manifest.get("runtime_abi", {})
    require(
        constants.get("REQUIRED_PYTHON") == (3, 11, 9)
        and runtime_abi.get("python") == "3.11.9",
        "Worker/base Python ABI drifted",
    )
    require(
        constants.get("REQUIRED_TORCH") == runtime_abi.get("pytorch")
        and constants.get("REQUIRED_TORCHVISION") == runtime_abi.get("torchvision"),
        "Worker/base PyTorch ABI drifted",
    )
    security = manifest.get("security_requirements", {})
    require(
        security.get("weights_only_is_not_a_safe_pickle_guarantee_for_pinned_runtime")
        is True
        and security.get("checkpoint_trust_boundary", "").startswith(
            "only byte-for-byte allowlisted official checkpoints"
        )
        and set(security.get("known_runtime_advisories", ()))
        == {"CVE-2025-32434", "CVE-2026-24747"},
        "Pinned PyTorch trust-boundary/advisory disclosure drifted",
    )
    require(
        security.get("hash_and_deserialize_from_same_open_file_handle") is True
        and security.get("torch_load_mmap_blocked") is True,
        "Checkpoint load TOCTOU guard manifest drifted",
    )
    license_gate = manifest.get("license_gate", {})
    require(
        license_gate.get("source_materialization_license_acceptance_env")
        == "MUJASSAM_HY21_LICENSE_ACCEPTED=1"
        and license_gate.get("source_materialization_territory_confirmation_env")
        == "MUJASSAM_HY21_TERRITORY_CONFIRMED=1"
        and license_gate.get("hosted_pull_request_validation_materializes_upstream_source")
        is False
        and license_gate.get("full_build_output_is_not_uploaded_or_published") is True
        and license_gate.get("public_github_release_with_tencent_source_is_disabled")
        is True,
        "License/materialization gate manifest drifted",
    )
    require(
        license_gate.get("personal_local_usage_scope") == "personal_local_only"
        and license_gate.get("personal_local_mode_requires_provider_identity") is False
        and license_gate.get("personal_local_mode_distribution_authorized") is False
        and license_gate.get("third_party_usage_scope") == "third_party_provider"
        and license_gate.get(
            "third_party_mode_requires_provider_identity_and_tencent_non_affiliation_disclosure"
        )
        is True
        and license_gate.get("personal_local_build_env")
        == "MUJASSAM_HY21_LOCAL_PERSONAL_USE=1",
        "Personal-local/provider license-scope manifest drifted",
    )
    runtime_abi = manifest.get("runtime_abi", {})
    require(
        constants.get("REQUIRED_PYTHON") == (3, 11, 9)
        and runtime_abi.get("python") == "3.11.9"
        and constants.get("REQUIRED_TORCH") == runtime_abi.get("pytorch")
        and constants.get("REQUIRED_TORCHVISION") == runtime_abi.get("torchvision")
        and constants.get("REQUIRED_CUDA_RUNTIME") == runtime_abi.get("cuda_runtime"),
        "Worker preflight ABI drifted from ENGINE-MANIFEST.json",
    )

    worker_text = WORKER.read_text(encoding="utf-8")
    require('if __name__ == "__main__":' in worker_text, "Engine worker has no CLI")
    require("def _self_test()" in worker_text, "Engine worker has no self-test")
    require(
        "_force_weights_only_torch_load" in worker_text,
        "Engine worker checkpoint guard is missing",
    )
    require(
        'if not isinstance(source, (str, os.PathLike)) and hasattr(source, "name"):'
        in worker_text
        and "result = probe.load(own_path)" in worker_text,
        "Checkpoint guard must preserve pathlib.Path parent directories",
    )
    require(
        'kwargs.pop("mmap", None)' in worker_text
        and "mmap_result = probe.load(str(own_path), mmap=True)" in worker_text,
        "Checkpoint guard must neutralize mmap and use the verified open handle",
    )
    rasterizer_preflight = python_function_source(WORKER, "_rasterizer_cuda_preflight")
    require(
        "import custom_rasterizer_kernel" in rasterizer_preflight
        and "custom_rasterizer_kernel.rasterize_image(" in rasterizer_preflight
        and "torch.cuda.synchronize(" in rasterizer_preflight
        and "dtype=torch.int32" in rasterizer_preflight
        and "8, 8" in rasterizer_preflight,
        "Worker preflight must execute a tiny CUDA rasterizer triangle",
    )
    orchestrate = python_function_source(WORKER, "_orchestrate")
    rasterizer_call = orchestrate.find("_run_rasterizer_preflight(")
    prepare = orchestrate.find("image_info = _prepare_image")
    download = orchestrate.find("model_root, dino_root, downloaded = _download_models")
    resume_restore = orchestrate.find("_restore_shape_resume(")
    shape_stage = orchestrate.find('_run_stage("shape"')
    resume_save = orchestrate.find("_save_shape_resume(")
    paint_stage = orchestrate.find('_run_stage("paint"')
    publish = orchestrate.find("staging.rename(final_dir)")
    published_flag = orchestrate.find("published = True", publish)
    resume_delete = orchestrate.find("_delete_shape_resume(")
    require(
        0 <= rasterizer_call < prepare < download < shape_stage,
        "The isolated CUDA/runtime preflight must run before image, model, and Shape setup",
    )
    rasterizer_launcher = python_function_source(WORKER, "_run_rasterizer_preflight")
    require(
        "_run_worker_child(" in rasterizer_launcher
        and '"--rasterizer-self-test"' in rasterizer_launcher,
        "Rasterizer preflight must run in an isolated worker process",
    )
    worker_main = python_function_source(WORKER, "main")
    rasterizer_mode = worker_main.find("if args.rasterizer_self_test:")
    engine_gate = worker_main.find("_validate_engine_pack()", rasterizer_mode)
    license_gate_call = worker_main.find("_validate_license_acceptance()", rasterizer_mode)
    base_runtime = worker_main.find("_validate_base_runtime(args.texture_mode)", rasterizer_mode)
    cuda_smoke = worker_main.find("_rasterizer_cuda_preflight()", rasterizer_mode)
    require(
        0 <= rasterizer_mode < engine_gate < license_gate_call < base_runtime < cuda_smoke,
        "Rasterizer child must enforce engine, license, ABI, then CUDA smoke gates",
    )
    require(
        0 <= resume_restore < shape_stage < resume_save < paint_stage,
        "Shape resume must restore before Shape and save before Paint",
    )
    require(
        0 <= publish < published_flag < resume_delete,
        "Shape resume may be deleted only after the final asset is published",
    )
    worker_child = python_function_source(WORKER, "_run_worker_child")
    require(
        'raw.startswith("MJERROR|")' in worker_child
        and 'raw.split("|", 2)' in worker_child
        and "last_child_error = (child_code, child_message)" in worker_child
        and "return StageProcessResult(code, last_child_error)" in worker_child,
        "Stage relay must parse, sanitize, and return the child MJERROR details",
    )
    require(
        "raise EngineError(*preflight_result.child_error)" in orchestrate
        and "raise EngineError(*shape_result.child_error)" in orchestrate
        and "raise EngineError(*paint_result.child_error)" in orchestrate
        and "raise EngineError(*finalize_result.child_error)" in orchestrate,
        "Parent worker must preserve specific child-stage MJERROR details",
    )
    paint_fix = worker_text.find("from utils.torchvision_fix import apply_fix")
    paint_import = worker_text.find(
        "from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline"
    )
    require(
        0 <= paint_fix < paint_import and "if not apply_fix():" in worker_text,
        "Paint must apply the reviewed torchvision compatibility shim before import",
    )
    stage_paint = python_function_source(WORKER, "_stage_paint")
    paint_low_vram = python_function_source(
        WORKER, "_install_low_vram_paint_runtime"
    )
    streaming_bake = python_function_source(WORKER, "_install_streaming_pbr_bake")
    require(
        "enable_vae_slicing" in paint_low_vram
        and "enable_slicing" in paint_low_vram
        and "_LowVramSuperResolutionOneShot" in paint_low_vram
        and "_LowVramMultiviewOneShot" in paint_low_vram
        and "_install_streaming_pbr_bake(" in paint_low_vram,
        "Paint 8GB runtime must slice VAE batches and stage GPU model lifetimes",
    )
    low_vram_install = stage_paint.find("_install_low_vram_paint_runtime(")
    paint_execute = stage_paint.find("pipeline(", low_vram_install)
    require(
        0 <= low_vram_install < paint_execute
        and "Painting 12 selected views at 768" in stage_paint,
        "Paint low-VRAM scheduling must be installed before fixed-quality execution",
    )
    require(
        "view_processor.render.back_project(" in streaming_bake
        and "weight * (project_cos_map**view_processor.config.bake_exp)"
        in streaming_bake
        and "_streaming_projection_is_redundant(painted_sum, view_sum)"
        in streaming_bake
        and "texture_merge.add_(project_texture * project_cos_map)"
        in streaming_bake
        and "trust_map_merge.add_(project_cos_map)" in streaming_bake
        and "del project_texture, project_cos_map, project_boundary_map"
        in streaming_bake
        and "project_textures" not in streaming_bake
        and "project_boundary_maps" not in streaming_bake,
        "Native 4K bake must preserve Tencent merge order while retaining one projection",
    )
    require(
        "def _paint_low_vram_runtime_self_test()" in worker_text
        and '"paint_low_vram_runtime": _paint_low_vram_runtime_self_test()'
        in worker_text
        and constants.get("PAINT_MAX_VIEWS") == 12
        and constants.get("PAINT_VIEW_RESOLUTION") == 768
        and constants.get("PBR_TEXTURE_SIZE") == 4096
        and constants.get("SHAPE_RESUME_PIPELINE_REVISION")
        == "mujassam-hy21-shape-cleanup-v1",
        "Paint memory scheduling must retain 12/768/4K and the existing Shape checkpoint identity",
    )
    low_vram_self_test = python_function_source(
        WORKER, "_paint_low_vram_runtime_self_test"
    )
    require(
        "import numpy" not in low_vram_self_test
        and "import torch" not in low_vram_self_test,
        "Paint low-VRAM self-test must remain runnable with the Python stdlib only",
    )
    require(
        "_SENSITIVE_PATTERN" in worker_text
        and "_URL_USERINFO_PATTERN" in worker_text
        and "secret_redaction" in worker_text,
        "Engine worker log-secret redaction is missing",
    )
    require(
        'distribution_lines == ["distribution_authorized=false"]' in worker_text
        and "and not distribution_lines" in worker_text,
        "Worker personal/third-party distribution-record contract drifted",
    )
    base_worker = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    require("hunyuan3d_2_1_pbr" in base_worker, "Base worker has no 2.1 dispatch")
    require("hunyuan21_worker.py" in base_worker, "Base worker does not dispatch 2.1")

    main_form = (ROOT / "app" / "MainForm.cs").read_text(encoding="utf-8")
    require(
        main_form.count("@@MUJASSAM_PROVIDER_LEGAL_NAME@@") == 1,
        "MainForm must contain exactly one provider build token",
    )
    require(
        main_form.count("@@MUJASSAM_HY21_USAGE_SCOPE@@") == 1
        and 'Hunyuan21PersonalLocalUsageScope = "personal_local_only"' in main_form
        and 'Hunyuan21ThirdPartyUsageScope = "third_party_provider"' in main_form
        and '"distribution_authorized=false\\r\\n"' in main_form,
        "MainForm personal-local acceptance contract is incomplete",
    )
    require(CI_PROVIDER in main_form, "MainForm does not reject the CI provider sentinel")
    distribution_notice = (ENGINE / "NOTICE.txt").read_text(encoding="utf-8")
    require(
        distribution_notice.count("@@MUJASSAM_PROVIDER_LEGAL_NAME@@") == 1,
        "Distribution notice must contain exactly one provider build token",
    )
    require(
        distribution_notice.count("@@MUJASSAM_HY21_USAGE_SCOPE@@") == 1
        and "Configured usage scope: @@MUJASSAM_HY21_USAGE_SCOPE@@"
        in distribution_notice
        and "personal use on the local device" in distribution_notice
        and "does not authorize distributing" in distribution_notice,
        "Distribution notice lacks the personal-local non-distribution scope",
    )
    require(
        distribution_notice.startswith(REQUIRED_DISTRIBUTION_NOTICE + "\n")
        and distribution_notice.count(REQUIRED_DISTRIBUTION_NOTICE) == 1,
        "Distribution notice does not preserve the exact license-mandated notice",
    )
    require(
        "Actual provider of this integration: @@MUJASSAM_PROVIDER_LEGAL_NAME@@"
        in distribution_notice
        and "Tencent is not affiliated with, associated with, sponsoring, or endorsing"
        in distribution_notice,
        "Provider/non-affiliation disclosure is incomplete",
    )

    require(not (ENGINE / "vendor").exists(), "Tencent source must not be committed")
    require(not (ENGINE / "python_packages").exists(), "Bundled dependencies must not be committed")
    forbidden_weights = [
        path
        for suffix in ("*.pth", "*.ckpt", "*.safetensors")
        for path in ENGINE.rglob(suffix)
    ]
    require(not forbidden_weights, f"Model weights must not be committed: {forbidden_weights}")

    runtime_requirements = read_complete_sha256_lock(
        ROOT / "build" / "hunyuan21.requirements.lock.txt", "runtime"
    )
    build_requirements = read_complete_sha256_lock(
        ROOT / "build" / "hunyuan21.build.requirements.lock.txt", "build"
    )
    for forbidden_runtime_package in (
        "basicsr",
        "realesrgan",
        "omegaconf",
    ):
        require(
            canonical_package_name(forbidden_runtime_package)
            not in runtime_requirements,
            f"Unused source/GPL runtime package must not be bundled: {forbidden_runtime_package}",
        )
    for base_owned_package in ("torch", "torchvision"):
        require(
            base_owned_package not in runtime_requirements,
            f"Portable-base package must not be duplicated in runtime lock: {base_owned_package}",
        )
    required_runtime_packages = {
        "accelerate",
        "diffusers",
        "einops",
        "huggingface-hub",
        "numpy",
        "opencv-python-headless",
        "pillow",
        "pygltflib",
        "pymeshlab",
        "pyyaml",
        "safetensors",
        "scikit-image",
        "scipy",
        "timm",
        "tokenizers",
        "torchdiffeq",
        "tqdm",
        "transformers",
        "trimesh",
        "xatlas",
    }
    require(
        required_runtime_packages <= set(runtime_requirements),
        "Runtime dependency lock is missing a direct engine package",
    )
    require(
        runtime_requirements["pymeshlab"][1] == "2022.2.post3",
        "Runtime dependency lock must preserve the pinned upstream PyMeshLab version",
    )
    required_build_pins = {
        "ninja": "1.11.1.3",
        "pybind11": "2.13.6",
        "setuptools": "69.5.1",
        "torch": "2.5.1+cu124",
        "torchvision": "0.20.1+cu124",
        "wheel": "0.45.1",
    }
    for package, expected_version in required_build_pins.items():
        require(
            package in build_requirements
            and build_requirements[package][1] == expected_version,
            f"Build dependency lock is missing required pin: {package}=={expected_version}",
        )

    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
    require(
        '$PSVersionTable.PSVersion -lt [version]"7.4"' in build_script
        and "$PSNativeCommandUseErrorActionPreference = $true" in build_script,
        "Full build must require reliable native-command failure propagation",
    )
    acknowledgement_gate = re.search(
        r'if \(\$env:MUJASSAM_HY21_LICENSE_ACCEPTED -cne "1" -or\s+'
        r'\$env:MUJASSAM_HY21_TERRITORY_CONFIRMED -cne "1"\) \{',
        build_script,
    )
    require(
        acknowledgement_gate is not None,
        "Build script must fail closed on both source-materialization acknowledgements",
    )
    require(
        len(re.findall(r"MUJASSAM_HY21_LICENSE_ACCEPTED -cne", build_script)) == 1
        and len(re.findall(r"MUJASSAM_HY21_TERRITORY_CONFIRMED -cne", build_script))
        == 1,
        "Build script must have exactly one authoritative acknowledgement gate",
    )
    source_fetch = build_script.find("git -C $SourceRoot fetch")
    source_directory = build_script.find(
        'New-Item -ItemType Directory -Path $SourceRoot'
    )
    require(source_fetch >= 0, "Build script source fetch is missing")
    require(source_directory >= 0, "Build script source materialization is missing")
    require(
        acknowledgement_gate.start() < source_directory < source_fetch,
        "Source-materialization acknowledgements must be checked before filesystem or network materialization",
    )
    require(
        'Copy-Item (Join-Path $RepositoryRoot "NOTICE_THIRD_PARTY.md") $Stage -Force'
        in build_script
        and '"NOTICE_THIRD_PARTY.md",' in build_script,
        "The built engine update must include and require the third-party notice",
    )
    require(
        "$BuildRequirements = Join-Path $RepositoryRoot" in build_script
        and '"build\\hunyuan21.build.requirements.lock.txt"' in build_script
        and 'Assert-CompleteSha256Lock $BuildRequirements "build"' in build_script
        and "--only-binary=:all: --require-hashes" in build_script
        and "-r $BuildRequirements" in build_script,
        "Hash-locked builds must cover build tools and PyTorch as well as runtime dependencies",
    )
    require(
        "--no-deps --target $PythonPackages.FullName" in build_script
        and "load_realesrgan_x4plus" in build_script,
        "Runtime packaging must use the complete wheel lock and self-contained x4 inference",
    )
    require(
        'importlib.metadata.version("pymeshlab") == "2022.2.post3"'
        in build_script
        and "mesh_smoke = FaceReducer()(mesh_smoke, max_facenum=20)"
        in build_script
        and "mesh_smoke = FloaterRemover()(mesh_smoke)" in build_script
        and "mesh_smoke = DegenerateFaceRemover()(mesh_smoke)" in build_script,
        "Full-build smoke test must exercise the pinned PyMeshLab postprocessors",
    )
    require(
        "from hunyuanpaintpbr.unet.modules import Dino_v2" in build_script
        and '"pytorch_lightning" not in sys.modules' in build_script,
        "Full-build smoke test must cover the inference-only Paint package exports",
    )
    require(
        "$TextFile = $_" in build_script
        and "$Text = [IO.File]::ReadAllText($TextFile.FullName)" in build_script
        and "$MujassamMarkerPattern = '@@MUJASSAM_[A-Z][A-Z0-9_]*@@'"
        in build_script
        and "$Text, $MujassamMarkerPattern" in build_script
        and "(Get-Content $_.FullName -Raw)" not in build_script,
        "Staged marker scan must be null-safe and limited to Mujassam placeholders",
    )
    vendor_patch = (ROOT / "build" / "patch_hunyuan21_windows.py").read_text(
        encoding="utf-8"
    )
    require(
        "training-only PyTorch Lightning wrapper" in vendor_patch
        and 'paint_package = root / "hy3dpaint" / "hunyuanpaintpbr" / "__init__.py"'
        in vendor_patch,
        "Staged Paint package must exclude the unused training-only wrapper",
    )
    rasterizer_patch = python_function_source(
        ROOT / "build" / "patch_hunyuan21_windows.py", "patch_windows_rasterizer"
    )
    require(
        '"custom_rasterizer_kernel_for_windows"' in rasterizer_patch
        and '"rasterizer_gpu.cu"' in rasterizer_patch
        and "int64_t maxint" in rasterizer_patch
        and "static_cast<int64_t>(MAXINT)" in rasterizer_patch
        and "torch::full({height, width}, maxint, INT64_options)"
        in rasterizer_patch,
        "Windows rasterizer patch must create z_min with signed torch::kInt64 storage",
    )
    require(
        'old_accessor = "(uint64_t*)z_min.data_ptr<uint64_t>()"'
        in rasterizer_patch
        and "reinterpret_cast<uint64_t*>(z_min.data_ptr<int64_t>())"
        in rasterizer_patch
        and re.search(r'"old accessor"\s*:\s*3', rasterizer_patch) is not None
        and re.search(r'"new accessor"\s*:\s*3', rasterizer_patch) is not None
        and "patched_source.count(new_accessor) != 3" in rasterizer_patch,
        "Windows rasterizer patch must replace exactly three mismatched typed pointers",
    )
    patch_source_tree = python_function_source(
        ROOT / "build" / "patch_hunyuan21_windows.py", "patch_source_tree"
    )
    require(
        "patch_windows_rasterizer(root)" in patch_source_tree
        and 'choices=("source", "vendor", "rasterizer-runtime")' in vendor_patch,
        "Rasterizer correction must apply to full and targeted runtime builds",
    )
    require(
        "import custom_rasterizer_kernel" in build_script
        and 'print("Running CUDA rasterizer triangle smoke")' in build_script
        and "custom_rasterizer_kernel.rasterize_image(" in build_script
        and "torch.cuda.synchronize(rasterizer_vertices.device)" in build_script
        and 'print("CUDA rasterizer triangle smoke: OK")' in build_script,
        "Full build must execute the real CUDA rasterizer entry point",
    )
    require(RASTERIZER_HOTFIX.is_file(), "Targeted rasterizer hotfix script is missing")
    rasterizer_hotfix = RASTERIZER_HOTFIX.read_text(encoding="utf-8")
    require(
        "HOTFIX_GPU_MICRO_SMOKE" in rasterizer_hotfix
        and "rasterizer.rasterize_image(" in rasterizer_hotfix
        and "torch.cuda.synchronize()" in rasterizer_hotfix
        and "MJRASTERHOTFIXSMOKE|OK|1" in rasterizer_hotfix,
        "Hotfix must execute the real CUDA rasterizer entry point",
    )
    compiled_smoke = rasterizer_hotfix.find(
        "Invoke-RasterizerSmoke $PortablePython $Packages $BuiltPydDirectory"
    )
    first_install = rasterizer_hotfix.find(
        "Set-FileAtomically $BuiltPyd $InstalledPyd", compiled_smoke
    )
    installed_smoke = rasterizer_hotfix.find(
        "--rasterizer-self-test --texture-mode native_2k", first_install
    )
    require(
        0 <= compiled_smoke < first_install < installed_smoke,
        "Hotfix must test the candidate before replacement and the installed worker after it",
    )
    require(
        "HOTFIX_SAFE_REPLACE" in rasterizer_hotfix
        and "$Parent = [IO.Path]::GetDirectoryName($Destination)"
        in rasterizer_hotfix
        and re.search(
            r"\[IO\.File\]::Replace\(\s*\$Candidate,\s*\$Destination,\s*"
            r"\$ReplacementBackup,\s*\$true\s*\)",
            rasterizer_hotfix,
        )
        is not None
        and "$Entry.Backup $Entry.Destination $Entry.Sha256" in rasterizer_hotfix
        and "$Destination, $null" not in rasterizer_hotfix,
        "Hotfix replacement must be atomic, explicitly backed up, and recoverable",
    )
    require(
        "HOTFIX_REUSE_ACCEPTANCE" in rasterizer_hotfix
        and "module._validate_license_acceptance()" in rasterizer_hotfix
        and "Read-Host" not in rasterizer_hotfix
        and "Start-Process -FilePath \"notepad.exe\"" not in rasterizer_hotfix,
        "Hotfix must reuse the installed fail-closed acceptance record without reprompting",
    )
    require(
        "$Replaced = $true" in rasterizer_hotfix
        and "$ReplacementBackup, $Destination, $Candidate, $true"
        in rasterizer_hotfix,
        "Hotfix must recover internally if verification fails after File.Replace",
    )
    require(
        "$Packages $SetupPy build_ext" in rasterizer_hotfix
        and rasterizer_hotfix.count("build_ext") == 1
        and '--build-temp $BuildTemp --build-lib $BuildLib' in rasterizer_hotfix
        and '$env:GIT_LFS_SKIP_SMUDGE = "1"' in rasterizer_hotfix
        and '"/LICENSE" "/hy3dpaint/custom_rasterizer/"' in rasterizer_hotfix
        and "--filter=blob:none" in rasterizer_hotfix
        and "model_download = $false" in rasterizer_hotfix
        and "full_build = $false" in rasterizer_hotfix,
        "Hotfix must fetch/build only the pinned custom rasterizer and no model data",
    )
    require(
        "HOTFIX_FULL_BUILD_PYTHON" in rasterizer_hotfix
        and "$PyLauncher -3.11" in rasterizer_hotfix
        and '"Include\\Python.h"' in rasterizer_hotfix
        and '"libs\\python311.lib"' in rasterizer_hotfix
        and "sys.path[:0] = [str(portable_site), str(packages)]"
        in rasterizer_hotfix
        and "& $BuildPython -I -X utf8 -c $BuildDriver" in rasterizer_hotfix
        and "& $PortablePython -I -X utf8 -c $BuildDriver"
        not in rasterizer_hotfix
        and "custom_rasterizer_kernel\\.cp311-win_amd64\\.pyd"
        in rasterizer_hotfix,
        "Hotfix must compile with full CPython headers while linking the portable ABI",
    )
    require(
        "HOTFIX_GIT_TRUST" in rasterizer_hotfix
        and '$env:GIT_NO_REPLACE_OBJECTS = "1"' in rasterizer_hotfix
        and "$env:GIT_CONFIG_GLOBAL = $OwnedGitConfig" in rasterizer_hotfix
        and '$env:GIT_CONFIG_NOSYSTEM = "1"' in rasterizer_hotfix
        and "core.hooksPath=$HooksDirectory" in rasterizer_hotfix
        and rasterizer_hotfix.count("$PreviousGit") >= 8
        and rasterizer_hotfix.count("Remove-Item Env:GIT_") == 4,
        "Hotfix Git source retrieval must ignore external substitutions/hooks and restore its environment",
    )
    require(
        "HOTFIX_PROCESS_RECHECK" in rasterizer_hotfix
        and rasterizer_hotfix.count(
            "Assert-MujassamProcessesStopped $InstallRoot"
        )
        == 2,
        "Hotfix must check for running Mujassam processes before build and immediately before replacement",
    )
    require(
        "HOTFIX_NONFATAL_RECEIPT" in rasterizer_hotfix
        and "HOTFIX_BEST_EFFORT_CLEANUP" in rasterizer_hotfix
        and "Remove-OwnedTemporaryDirectory $TemporaryRoot" in rasterizer_hotfix,
        "Optional receipt and temporary cleanup failures must not invalidate a verified installation",
    )
    for forbidden_hotfix_action in (
        "snapshot_download(",
        "hf_hub_download(",
        ".safetensors",
        ".ckpt",
        ".pth",
        "pip install",
        "Invoke-WebRequest",
        "build-hunyuan21-update.ps1",
        "dotnet build",
        "PyInstaller",
    ):
        require(
            forbidden_hotfix_action not in rasterizer_hotfix,
            f"Rasterizer-only hotfix may not materialize models/full build: {forbidden_hotfix_action}",
        )
    require(PAINT_8GB_HOTFIX.is_file(), "Paint 8GB worker hotfix is missing")
    paint_hotfix = PAINT_8GB_HOTFIX.read_text(encoding="utf-8")
    require(
        "def _install_low_vram_paint_runtime(" in paint_hotfix
        and "def _install_streaming_pbr_bake(" in paint_hotfix
        and len(
            re.findall(
                r"(?m)^\s*Assert-MujassamProcessesStopped\s+\$InstallRoot\s*$",
                paint_hotfix,
            )
        )
        == 3
        and len(
            re.findall(
                r"(?m)^[ \t]*Assert-MujassamProcessesStopped[ \t]+"
                r"\$InstallRoot[ \t]*\r?\n[ \t]*\[IO\.File\]::Replace\(",
                paint_hotfix,
            )
        )
        == 2
        and "MJPAINTWORKERSYNTAX|OK|1" in paint_hotfix
        and "module._validate_license_acceptance()" in paint_hotfix
        and "[IO.File]::Replace(" in paint_hotfix
        and re.search(
            r"Set-FileAtomically\s+`\s*\$BackupWorker\s+\$InstalledWorker\s+"
            r"\$OriginalWorkerSha256\s+\$InstallRoot",
            paint_hotfix,
        )
        is not None
        and "--rasterizer-self-test --texture-mode native_2k" in paint_hotfix
        and "$InstalledWorker --self-test" in paint_hotfix,
        "Paint hotfix must atomically install, validate, and roll back one worker only",
    )
    for forbidden_paint_hotfix_action in (
        "Invoke-WebRequest",
        "snapshot_download",
        "pip install",
        "build_ext",
        "dotnet build",
        "git clone",
        "git pull",
    ):
        require(
            forbidden_paint_hotfix_action not in paint_hotfix,
            "Paint worker hotfix may not download/build: "
            + forbidden_paint_hotfix_action,
        )
    require(
        '$env:MUJASSAM_HY21_LOCAL_PERSONAL_USE -ceq "1"' in build_script
        and '$UsageScope = if ($PersonalLocalUse)' in build_script
        and '$UsageScopeToken = "@@MUJASSAM_HY21_USAGE_SCOPE@@"' in build_script
        and "$MainFormSource.Replace($MainFormUsageToken, $UsageScope)" in build_script
        and "usage_scope = $UsageScope" in build_script
        and "distribution_authorized = $false" in build_script
        and "provider_legal_name = $ProviderLegalName" in build_script
        and build_script.count("@@[A-Z][A-Z0-9_]*@@") >= 1
        and "@@MUJASSAM_[A-Z][A-Z0-9_]*@@" in build_script
        and '$MainFormSource.Contains("@@")' not in build_script,
        "Build script does not materialize the personal-local scope safely",
    )

    local_build = LOCAL_BUILD_SCRIPT.read_text(encoding="utf-8")
    local_recovery = LOCAL_BUILD_RECOVERY.read_text(encoding="utf-8")
    local_installer = LOCAL_INSTALLER.read_text(encoding="utf-8")
    local_restore = LOCAL_RESTORE.read_text(encoding="utf-8")
    require(LOCAL_REMOVER.is_file(), "Mini-only H21 remover is missing")
    local_remover = LOCAL_REMOVER.read_text(encoding="utf-8")
    move_transaction = local_remover[
        local_remover.index("function Move-ToTransactionQuarantine") :
        local_remover.index("function Undo-TransactionEntry")
    ]
    read_transaction = local_remover[
        local_remover.index("function Read-TransactionJournal") :
        local_remover.index("function Assert-HarmlessQuarantineWithoutJournal")
    ]
    recover_transactions = local_remover[
        local_remover.index("function Recover-PendingQuarantineTransactions") :
        local_remover.index("function Complete-QuarantineTransaction")
    ]
    complete_transaction = local_remover[
        local_remover.index("function Complete-QuarantineTransaction") :
        local_remover.index("function Restore-OriginalFileTransactional")
    ]
    marker_free_executable = local_remover[
        local_remover.index("function Test-MarkerFreeMiniExecutable") :
        local_remover.index("function Test-MiniCompletionMarker")
    ]
    gui_self_test = local_remover[
        local_remover.index("$GuiSelfTestReport = Join-Path") :
        local_remover.index(
            'Write-Host "تشغيل فحص Mini worker ومحرك Hunyuan2 Mini',
            local_remover.index("$GuiSelfTestReport = Join-Path"),
        )
    ]
    require(
        LOCAL_BUILD_LAUNCHER.is_file() and LOCAL_GUIDE.is_file()
        and "I ACCEPT" in local_build
        and "UNITED ARAB EMIRATES" in local_build
        and "PERSONAL LOCAL USE" in local_build
        and '$PSVersionTable.PSVersion -lt [version]"7.4"' in local_build
        and '$env:MUJASSAM_HY21_LOCAL_PERSONAL_USE = "1"' in local_build
        and '$env:MUJASSAM_REQUIRE_HASHED_DEPENDENCIES = "1"' in local_build
        and "Remove-Item Env:MUJASSAM_PROVIDER_LEGAL_NAME" in local_build
        and "build-hunyuan21-update.ps1" in local_build,
        "Local UAE builder does not preserve explicit acceptance and hash-lock gates",
    )
    require(
        '[string]$FailedBuildRoot' in local_recovery
        and "c8c99ed6683d31600edeeb47f883986e77797120" in local_recovery
        and SOURCE_COMMIT in local_recovery
        and "^MujassamAI-hy21-[0-9a-f]{32}$" in local_recovery
        and "[IO.Path]::GetDirectoryName($BuildTemporaryRoot)" in local_recovery
        and "[IO.FileAttributes]::ReparsePoint" in local_recovery
        and "$FailedRepositoryCommit $RepositoryCommit --" in local_recovery,
        "Failed-build recovery must remain explicit, commit-pinned, and path-confined",
    )
    require(
        "$Text = [IO.File]::ReadAllText($TextFile.FullName)" in local_recovery
        and "$MujassamMarkerPattern = '@@MUJASSAM_[A-Z][A-Z0-9_]*@@'"
        in local_recovery
        and "$Text, $MujassamMarkerPattern" in local_recovery
        and "source_commit = $FailedRepositoryCommit" in local_recovery
        and 'usage_scope = "personal_local_only"' in local_recovery
        and "distribution_authorized = $false" in local_recovery
        and "provider_legal_name = $null" in local_recovery
        and "Expand-Archive -LiteralPath $PartialArchive" in local_recovery
        and "Get-Sha256 $DownloadedArchive" in local_recovery
        and "-ExpectedSha256 $ArchiveSha256" in local_recovery,
        "Failed-build recovery must scan, manifest, verify, copy, and install safely",
    )
    require(
        "Get-ChildItem -LiteralPath $TemporaryRoot" not in local_recovery
        and "Remove-Item -LiteralPath $BuildTemporaryRoot" not in local_recovery,
        "Recovery may neither auto-select nor delete a retained failed-build root",
    )
    require(
        '"personal_local_only"' in local_installer
        and "distribution_authorized" in local_installer
        and "provider_legal_name" in local_installer
        and '"2.5.1+cu124"' in local_installer
        and '"0.20.1+cu124"' in local_installer
        and "[Environment+SpecialFolder]::LocalApplicationData" in local_installer
        and '"MujassamAI\\Backups"' in local_installer
        and "update-manifest.json" in local_installer,
        "Offline installer lacks scope, ABI, manifest, or backup enforcement",
    )
    require(
        "$InstallerTemporaryRoot = [IO.Path]::GetFullPath(" in local_installer
        and "[IO.Path]::GetTempPath()).TrimEnd" in local_installer
        and '"MujassamAI-hy21-install-"' in local_installer
        and "[IO.Directory]::CreateDirectory($StagingRoot)" in local_installer
        and "Assert-WritableDirectory $InstallerTemporaryRoot" in local_installer
        and "Remove-SafeStagingDirectory $StagingRoot" in local_installer
        and "Assert-WritableDirectory $RootPath" in local_installer,
        "Offline installer must use verified writable roots and constrained cleanup",
    )
    require(
        all(
            provider_mutation not in local_installer
            for provider_mutation in ("New-Item", "Copy-Item", "Remove-Item")
        ),
        "Offline installer filesystem mutations must use explicit .NET APIs",
    )
    require(
        "$ReplacementBackup, $true" in local_installer
        and "$RollbackReplacementBackup, $true" in local_installer
        and "$Destination, $null, $true" not in local_installer
        and "Remove-TemporaryFileBestEffort" in local_installer,
        "File.Replace must use explicit same-directory backup paths",
    )
    require(
        "install-receipt.json" in local_restore
        and "ShouldProcess" in local_restore
        and "installed_sha256" in local_restore
        and "Assert-NoReparsePointInExistingPath" in local_restore
        and "$RestoreReplacementBackup, $true" in local_restore
        and "$Destination, $null, $true" not in local_restore
        and all(
            provider_mutation not in local_restore
            for provider_mutation in ("New-Item", "Copy-Item", "Remove-Item")
        ),
        "Local restore path lacks receipt, confirmation, hash, or reparse enforcement",
    )
    require(
        "REMOVE_HY21_LATEST_COMPATIBLE_MINI_BASELINE" in local_remover
        and "REMOVE_HY21_OLDEST_MINI_BASELINE" not in local_remover
        and "Sort-Object { [IO.Path]::GetFileName($_) } -Descending"
        in local_remover
        and "$Baseline = $TrustedBaselines[0]" in local_remover
        and "Get-TrustedMiniBaseline" in local_remover
        and "Test-MarkerFreeMiniWorker" in local_remover
        and "Test-MarkerFreeMiniExecutable $BackupExecutable" in local_remover
        and '"hunyuan3d_2_1_pbr", "hunyuan21_worker.py"' in local_remover
        and "$OverwrittenMap.ContainsKey($H21WorkerRelative)" in local_remover
        and "$CreatedMap.ContainsKey($H21WorkerRelative)" in local_remover
        and "Get-Sha256 $BackupFile" in local_remover
        and "install-receipt.json" in local_remover,
        "H21 removal must choose the latest compatible, marker-free, hash-verified Mini baseline",
    )
    require(
        "Test-AllowedH21ReceiptRelativePath" in local_remover
        and '"MujassamAI.exe", "NOTICE_THIRD_PARTY.md", "app/worker.py"'
        in local_remover
        and '"app/quality", "licenses", "app/engines/hunyuan21"'
        in local_remover
        and "Get-ReceiptPathMap $Overwritten" in local_remover
        and "Get-ReceiptPathMap $Created" in local_remover
        and "if (-not (Test-AllowedH21ReceiptRelativePath $Relative))"
        in local_remover
        and "Test-ProtectedMiniRelativePath" in local_remover
        and all(
            f'"{protected}"' in local_remover
            for protected in (
                "app/engines/hunyuan2",
                "rt",
                "models",
                "app/vendor",
                "exports",
                "export",
                "output",
                "outputs",
                "app/exports",
                "app/export",
                "app/output",
                "app/outputs",
            )
        )
        and '"MujassamAI-Exports"' in local_remover
        and "$MiniEngineState" in local_remover
        and "تغيّر ملف خارج Hunyuan3D 2.1" in local_remover
        and "تغيّر ملف أضافه التحديث خارج مجلد H21" in local_remover,
        "H21 receipts and cleanup targets must use a positive allowlist and protect Mini/exports/H2",
    )
    require(
        "function Get-NormalizedFullPath" in local_remover
        and "[IO.Path]::GetPathRoot($Full)" in local_remover
        and "$Full.Length -gt $VolumeRoot.Length" in local_remover
        and "$Full.TrimEnd([char[]]@('\\', '/'))" in local_remover
        and "function Test-PathInsideOrEqual" in local_remover
        and "function Get-ContainedPath" in local_remover
        and "Assert-NoReparsePointInExistingPath" in local_remover
        and "Assert-PathNotProtected" in local_remover
        and "$ForbiddenInstallRoots" in local_remover
        and "مسار التثبيت واسع أو حساس أكثر من اللازم" in local_remover,
        "H21 removal must normalize roots without collapsing volume roots and confine every target",
    )
    require(
        "REMOVE_HY21_EXACT_ENGINE_TREE" in local_remover
        and '"app/engines/hunyuan21"' in local_remover
        and '"Engines\\Hunyuan3D-2.1"' in local_remover
        and '"Licenses\\acceptance-v2-1.txt"' in local_remover
        and "$AnyH21BackupPattern" in local_remover
        and "^hunyuan21(?:-rasterizer|-paint-8gb)?-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$"
        in local_remover
        and "$PbrUpdateArtifactPattern" in local_remover
        and "MujassamAI-Hunyuan21-PBR-Update-v1(?:-[0-9]{8}-[0-9]{6}"
        in local_remover
        and "install-[0-9a-f]{32}" in local_remover
        and "rasterizer-[0-9a-f]{32}" in local_remover
        and ".MujassamAI-hy21-staging-[0-9a-f]{8,32}" in local_remover
        and "MujassamAI-HunyuanBackup-[0-9a-f]{8}" in local_remover
        and "MujassamAI-Previous" not in local_remover
        and "MujassamAI-Failed" not in local_remover
        and "Assert-NormalTree" in local_remover
        and "Assert-DirectChildDirectory" in local_remover,
        "H21 cleanup must use exact timestamped backup/ZIP/TEMP names and reparse-safe roots",
    )
    require(
        "function New-QuarantineTransaction" in local_remover
        and "function Write-TransactionJournalAtomic" in local_remover
        and "function Move-ToTransactionQuarantine" in local_remover
        and "function Undo-TransactionEntry" in local_remover
        and "function Undo-QuarantineTransaction" in local_remover
        and "function Complete-QuarantineTransaction" in local_remover
        and "schema_version = 2" in local_remover
        and "quarantine_item = [string]$Entry.ItemName" in local_remover
        and "containment_root = [string]$Entry.ContainmentRoot" in local_remover
        and "entries = $Entries" in local_remover
        and 'State = "planned"' in move_transaction
        and '$MoveRecord.State = "moved"' in move_transaction
        and move_transaction.index("Write-TransactionJournalAtomic")
        < move_transaction.index("[IO.Directory]::Move(")
        and move_transaction.index("Write-TransactionJournalAtomic")
        < move_transaction.index("[IO.File]::Move(")
        and move_transaction.index('$MoveRecord.State = "moved"')
        < move_transaction.rindex("Write-TransactionJournalAtomic")
        and "$Index = $Transaction.Moves.Count - 1" in local_remover
        and "quarantine ليس على volume الهدف نفسه" in local_remover
        and "ReplacementSha256" in local_remover
        and "Undo-QuarantineTransaction $RestoreTransaction" in local_remover
        and "Undo-QuarantineTransaction $CleanupTransaction" in local_remover
        and "Set-QuarantineTransactionCommitted $CleanupTransaction"
        in local_remover
        and '$Transaction.State = "committed"' in local_remover
        and '[string]$Transaction.State -cne "committed"'
        in complete_transaction
        and '$Entry.State = "purged"' in complete_transaction
        and complete_transaction.index('$Entry.State = "purged"')
        < complete_transaction.index(
            "Write-TransactionJournalAtomic",
            complete_transaction.index('$Entry.State = "purged"'),
        )
        < complete_transaction.rindex("[IO.File]::Delete($MarkerPath)")
        and "رُفض purge لمعاملة غير committed" in complete_transaction,
        "H21 removal must journal before moves, roll back active work, and resumably purge committed work",
    )
    require(
        "$Purpose -cnotin" in local_remover
        and "$JournalPurpose -cnotin" in read_transaction
        and "$JournalState -cnotin" in read_transaction
        and "$Kind -cnotin" in read_transaction
        and "$EntryState -cnotin" in read_transaction
        and "$QuarantineItem -cne $ItemName" in read_transaction
        and '$JournalState -ceq "committed"' in read_transaction
        and '$JournalPurpose -ceq "restore"' in read_transaction
        and '$Kind -ceq "file"' in read_transaction
        and '$EntryState -ceq "replacement-written"' in read_transaction,
        "Transaction purpose/state/kind/entry enums must be validated with exact case",
    )
    require(
        "function Get-RecoveryContainmentRoot" in local_remover
        and "$ExpectedContainmentRoot = Get-RecoveryContainmentRoot"
        in read_transaction
        and "$ContainmentRoot, $ExpectedContainmentRoot" in read_transaction
        and "$OriginalVolume, $QuarantineVolume" in read_transaction
        and "Test-AllowedH21ReceiptRelativePath $Relative" in local_remover
        and "Test-ProtectedMiniRelativePath $Relative" in local_remover
        and "$Name -cmatch $PbrUpdateArtifactPattern" in local_remover
        and "$Name -cmatch $AnyH21BackupPattern" in local_remover
        and "Assert-PathNotProtected $PathFull" in local_remover
        and "transaction يملك quarantine مكررًا على volume واحد"
        in recover_transactions,
        "Startup recovery must re-derive allowlisted containment and enforce same-volume transactions",
    )
    startup_recovery = local_remover.rindex(
        "Recover-PendingQuarantineTransactions `"
    )
    required_file_loop = local_remover.index(
        "foreach ($RequiredRelative in @(", startup_recovery
    )
    require(
        startup_recovery < required_file_loop
        and "$AnyActive" in recover_transactions
        and "Undo-QuarantineTransaction $Transaction" in recover_transactions
        and "Complete-QuarantineTransaction $Transaction" in recover_transactions
        and "transaction active يحتوي item purged" in recover_transactions,
        "Startup must roll back active journals or resume committed purges before requiring Mini files",
    )
    require(
        "function Test-MiniCompletionMarker" in local_remover
        and "function Write-MiniCompletionMarker" in local_remover
        and '"mini-only-restored-v1.json"' in local_remover
        and "schema_version = 1" in local_remover
        and "install_root = $InstallRoot" in local_remover
        and all(
            marker_hash in local_remover
            for marker_hash in (
                "mujassam_exe_sha256",
                "mini_worker_sha256",
                "hunyuan2_worker_sha256",
                "hunyuan2_manifest_sha256",
            )
        )
        and "$HasValidCompletionMarker" in local_remover
        and "$CurrentWorkerIsMarkerFree" in local_remover
        and "$CurrentExecutableIsMarkerFree" in local_remover
        and "نسخة Mini الحالية مثبتة بعلامة اكتمال موثوقة" in local_remover,
        "A hash-bound completion marker must make an already-restored Mini rerun idempotent",
    )
    require(
        "[switch]$DeepCleanup" in local_remover
        and "REMOVE_HY21_DEEP_CLEANUP_GATE" in local_remover
        and "if (-not $DeepCleanup)" in local_remover
        and "if ($DeepCleanup -and" in local_remover
        and "للتنظيف الكامل أعد الأمر نفسه مع -DeepCleanup" in local_remover
        and "ShouldProcess" in local_remover
        and "Assert-DeepCleanupPhaseReady" in local_remover
        and "Assert-MujassamProcessesStopped" in local_remover,
        "Large H21 cleanup and backup purging must require explicit -DeepCleanup and fresh process gates",
    )
    require(
        "REMOVE_HY21_BASELINE_PREFLIGHT" in local_remover
        and "function Test-MarkerFreeMiniExecutable" in local_remover
        and "$Bytes[0] -ne 0x4d" in marker_free_executable
        and "$Bytes[1] -ne 0x5a" in marker_free_executable
        and "[Text.Encoding]::UTF8.GetString($Bytes)" in marker_free_executable
        and "[Text.Encoding]::Unicode.GetString($Bytes)" in marker_free_executable
        and "$Utf16EvenText" in marker_free_executable
        and "$Utf16OddText" in marker_free_executable
        and '"Hunyuan3D 2.1 Ultimate"' in marker_free_executable
        and local_remover.count("Test-MarkerFreeMiniExecutable") >= 5
        and "Test-MarkerFreeMiniExecutable $PreflightMiniExecutable"
        in local_remover
        and "Test-MarkerFreeMiniExecutable $MiniExecutable" in local_remover
        and "& $PortablePython -I -X utf8 $PreflightMiniWorker --self-test"
        in local_remover
        and local_remover.index(
            "& $PortablePython -I -X utf8 $PreflightMiniWorker --self-test"
        )
        < local_remover.index("$PSCmdlet.ShouldProcess("),
        "Baseline/current/preflight/post-restore Mini PE files must be marker-free in ASCII/UTF-16",
    )
    require(
        "function Test-TreeContainsUserOutput" in local_remover
        and "$Pending = [Collections.Generic.Stack[string]]::new()"
        in local_remover
        and "MujassamAI-Exports|\\.git" in local_remover
        and local_remover.count("Test-TreeContainsUserOutput") >= 4
        and "Test-PathInsideOrEqual $RootPath $DirectoryFull" in local_remover
        and "Test-PathInsideOrEqual $Context.InstallRoot $PathFull"
        in local_remover
        and "تُرك مجلد setup لأنه يحتوي export/output أو .git" in local_remover
        and "setup artifact recovery يحتوي export/output أو .git"
        in local_remover
        and "ظهر export/output أو .git داخل مجلد setup قبل cleanup"
        in local_remover,
        "Live cleanup and recovery must symmetrically skip nested user output and overlapping setup trees",
    )
    require(
        "Select-InactiveH21CleanupDirectories" in local_remover
        and "Get-CimInstance -ClassName Win32_Process" in local_remover
        and "build-hunyuan21-local.ps1" in local_remover
        and "install-hunyuan21-local.ps1" in local_remover
        and "$DeferredGlobalCleanupWarning" in local_remover,
        "Global H21 TEMP/staging cleanup must defer while another build/install is active",
    )
    require(
        "REMOVE_HY21_DEFER_RECEIPT_TREE" in local_remover
        and local_remover.count("if (Test-H21EngineRelativePath $Relative)") >= 4
        and "continue" in local_remover
        and "Assert-NormalTree validates the exact root once" in local_remover,
        "Receipt files inside H21 must defer to one exact-tree cleanup for performance",
    )
    require(
        '"app/engines/hunyuan2/hunyuan2_worker.py"' in local_remover
        and '"app/engines/hunyuan2/ENGINE-MANIFEST.json"' in local_remover
        and "$Hunyuan2WorkerSha256" in local_remover
        and "$Hunyuan2ManifestSha256" in local_remover
        and "& $MiniExecutable" not in local_remover
        and "$GuiSelfTestProcess = Start-Process `" in gui_self_test
        and "-FilePath $MiniExecutable `" in gui_self_test
        and "-ArgumentList $GuiSelfTestArguments `" in gui_self_test
        and "-Wait -PassThru -ErrorAction Stop" in gui_self_test
        and "$GuiSelfTestExitCode = $GuiSelfTestProcess.ExitCode"
        in gui_self_test
        and "$GuiSelfTestExitCode -ne 0" in gui_self_test
        and "} finally {" in gui_self_test
        and "$GuiSelfTestProcess.Dispose()" in gui_self_test
        and gui_self_test.index("$GuiSelfTestProcess.Dispose()")
        < gui_self_test.index("Remove-TemporaryFileBestEffort $GuiSelfTestReport")
        and "PortableLayout=OK" in local_remover
        and "JobSchema=OK" in local_remover
        and "Is64BitProcess=True" in local_remover
        and "& $PortablePython -I -X utf8 $MiniWorker --self-test" in local_remover
        and "& $PortablePython -I -X utf8 $Hunyuan2Worker --self-test" in local_remover,
        "H21 removal must wait for the GUI winexe, inspect ExitCode/report, and self-test Mini/H2",
    )
    remover_self_test = local_remover.rindex(
        "& $PortablePython -I -X utf8 $Hunyuan2Worker --self-test"
    )
    remover_completion = local_remover.index("Write-MiniCompletionMarker `")
    remover_deep_gate = local_remover.index("REMOVE_HY21_DEEP_CLEANUP_GATE")
    remover_cleanup = local_remover.index("REMOVE_HY21_EXACT_ENGINE_TREE")
    require(
        remover_self_test < remover_completion < remover_deep_gate < remover_cleanup,
        "Destructive H21 cleanup must start only after Mini tests, completion, and the explicit gate",
    )
    for forbidden_remover_action in (
        "Invoke-WebRequest",
        "Invoke-RestMethod",
        "curl.exe",
        "github.com",
        "New-Item",
        "Copy-Item",
        "Remove-Item",
        "Move-Item",
        "Rename-Item",
        "Set-Content",
        "Add-Content",
        "Out-File",
    ):
        require(
            forbidden_remover_action not in local_remover,
            f"Mini-only remover may not use network/provider mutation: {forbidden_remover_action}",
        )
    for forbidden_installer_network in (
        "Invoke-WebRequest",
        "Invoke-RestMethod",
        "curl.exe",
        "github.com",
    ):
        require(
            forbidden_installer_network not in local_installer,
            f"Offline installer may not use network/release path: {forbidden_installer_network}",
        )
    require(
        not (ROOT / ".github" / "workflows" / "hunyuan21-lock-capture.yml").exists(),
        "Temporary lock-capture workflow must not remain in the final branch",
    )

    workflow = WORKFLOW.read_text(encoding="utf-8")
    for forbidden in (
        "actions/upload-artifact",
        "actions/download-artifact",
        "contents: write",
        "GH_TOKEN",
        "gh release",
    ):
        require(forbidden not in workflow, f"Validation workflow may not distribute: {forbidden}")
    require(
        workflow.count("persist-credentials: false") == 2,
        "Both checkout steps must discard GitHub credentials before build execution",
    )
    require(
        '"build/resume-hunyuan21-local.ps1"' in workflow,
        "Hosted static validation must parse the failed-build recovery script",
    )
    require(
        '"build/hotfix-hunyuan21-rasterizer.ps1"' in workflow,
        "Hosted static validation must parse the targeted rasterizer hotfix",
    )
    require(
        '"build/hotfix-hunyuan21-paint-8gb.ps1"' in workflow,
        "Hosted static validation must parse the Paint 8GB worker hotfix",
    )
    require(
        '"installer/remove-hunyuan21-local.ps1"' in workflow
        and "MJHUNYUAN21REMOVESELFTEST|OK|2" in workflow
        and "MJHUNYUAN21DEEPSELFTEST|OK|2" in workflow
        and "MJHUNYUAN21ROLLBACKSELFTEST|OK|1" in workflow
        and "MJHUNYUAN21ACTIVERECOVERYSELFTEST|OK|1" in workflow
        and "MJHUNYUAN21COMMITTEDRECOVERYSELFTEST|OK|1" in workflow
        and "MJHUNYUAN21PBRBASEREJECTSELFTEST|OK|1" in workflow
        and "MJHUNYUAN21SETUPPROTECTSELFTEST|OK|1" in workflow
        and "MJHUNYUAN21GUIWAITSELFTEST|OK|1" in workflow
        and workflow.count("& ./installer/remove-hunyuan21-local.ps1") >= 3
        and "DeepCleanup = $true" in workflow
        and 'schema_version = 2' in workflow
        and 'state = "active"' in workflow
        and 'state = "committed"' in workflow
        and 'quarantine_item = "item-000000"' in workflow
        and "containment_root = $Install" in workflow
        and '"MujassamAI\\Engines\\Hunyuan3D-2\\models\\mini.keep"' in workflow
        and '"Downloads\\MujassamAI-Exports\\model.glb"' in workflow
        and '"Downloads\\MujassamAI-Build-UAE-test\\repo.keep"' in workflow
        and "MujassamAI-Hunyuan21-PBR-Update-v1-20260827-104834.zip"
        in workflow
        and '"HunyuanUpdateCache", "UpdaterLogs", "InstallerCache", "InstallerLogs"'
        in workflow
        and '"MujassamAI-Previous-123456abcdef"' in workflow
        and '"MujassamAI-Failed-fedcba654321"' in workflow
        and '(Join-Path $PreviousSetup "keep")' in workflow
        and '(Join-Path $FailedSetup "keep")' in workflow
        and '(Join-Path $NestedExportSetup "nested\\Exports\\model.glb")'
        in workflow
        and "Previous/Failed are intentionally outside the exact cleanup"
        in workflow
        and '"MujassamAI\\mini-only-restored-v1.json"' in workflow
        and "Transactional rollback did not restore the exact PBR state"
        in workflow
        and "Rollback left an active quarantine behind" in workflow
        and "Active-restore fixture failed to hide the required worker"
        in workflow
        and "Committed partial-purge recovery remains"
        in workflow
        and "RejectedPbrExeBackup" in workflow
        and '/target:winexe "/out:$OriginalExe"' in workflow
        and '/target:winexe "/out:$FailingMiniExe"' in workflow
        and "$GuiWaitPattern" in workflow
        and "-Wait\\s+-PassThru\\s+-ErrorAction\\s+Stop" in workflow
        and "$GuiSelfTestProcess.Dispose()" in workflow
        and "exit code:\\s*23" in workflow
        and 'source.Contains("MJSELFTEST|OK|3")' in workflow
        and 'source.Contains("MJHUNYUANSELFTEST|OK|1")' in workflow
        and 'File.ReadAllText(script)' in workflow
        and "^hunyuan21(?:-rasterizer|-paint-8gb)?-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$"
        in workflow,
        "Hosted validation must exercise deep cleanup, idempotence, preservation, and rollback",
    )
    require(
        "importlib.util.spec_from_file_location(" in workflow
        and "worker._paint_low_vram_runtime_self_test()" in workflow
        and "MJHUNYUAN21LOWVRAMSELFTEST|OK|1" in workflow
        and "python -S -" in workflow,
        "Hosted static validation must directly execute the stdlib-only Paint low-VRAM self-test",
    )
    require(
        "Compile the repository-owned WinForms adapter" in workflow
        and "app/Program.cs app/MainForm.cs" in workflow,
        "Hosted adapter validation must compile the WinForms sources",
    )
    for preflight_contract in (
        '"VERSION.txt"',
        '"BUILD-MANIFEST.txt"',
        '"MODEL-MANIFEST.json"',
        '"realesrgan_x2.py"',
        '"models.json"',
        '"RealESRGAN_x2plus.pth"',
        '"ckpt_base.pth"',
        '"stable-point-aware-3d", "spar3d", "system.py"',
        '"stable-point-aware-3d", "spar3d", "utils.py"',
        '"stable-point-aware-3d", "run.py"',
        "MJABI|3.11.9|2.5.1+cu124|0.20.1+cu124",
    ):
        require(
            preflight_contract in workflow,
            f"Generated H21 updater lacks base preflight: {preflight_contract}",
        )
    generic_updater = (ROOT / "installer" / "HunyuanUpdateSetup.cs").read_text(
        encoding="utf-8"
    )
    require(
        "app/engines/hunyuan21" not in generic_updater,
        "H21-only compatibility checks must not modify the generic Hunyuan2 updater",
    )
    require(
        "runs-on: [self-hosted, Windows, X64, hy21-territory]" in workflow,
        "Full build must run only on the Territory-approved self-hosted label set",
    )
    require(
        'MUJASSAM_REQUIRE_HASHED_DEPENDENCIES: "1"' in workflow,
        "Full build must fail closed until the complete hashed Windows lock exists",
    )
    require(
        'MUJASSAM_HY21_LOCAL_PERSONAL_USE: "1"' in workflow
        and "CI validation build — Hunyuan3D 2.1 disabled" not in workflow,
        "Non-publishing full validation must use the personal-local scope",
    )
    full_job_condition = re.search(
        r"(?m)^\s+if:\s*(github\.event_name[^\r\n]+)$", workflow
    )
    require(full_job_condition is not None, "Full build job condition is missing")
    condition = full_job_condition.group(1)
    for gate in (
        "github.event_name == 'workflow_dispatch'",
        "github.ref == 'refs/heads/main'",
        "inputs.full_build",
        "inputs.license_accepted",
        "inputs.territory_confirmed",
    ):
        require(gate in condition, f"Full build condition is missing: {gate}")
    require(
        "MUJASSAM_HY21_LICENSE_ACCEPTED: ${{ inputs.license_accepted && '1' || '0' }}"
        in workflow
        and "MUJASSAM_HY21_TERRITORY_CONFIRMED: ${{ inputs.territory_confirmed && '1' || '0' }}"
        in workflow,
        "Full build acknowledgement inputs must map to the fail-closed environment flags",
    )
    require(
        '"MujassamAI.exe", "NOTICE_THIRD_PARTY.md", "app/worker.py"' in workflow,
        "Generated updater must require the bundled third-party notice",
    )
    static_start = workflow.index("  adapter-static-validation:")
    full_start = workflow.index("  build-and-package:")
    static_job = workflow[static_start:full_start]
    require(
        re.search(
            r"(?mi)^\s*(?:&\s*)?(?:\./)?build[/\\]build-hunyuan21-update\.ps1",
            static_job,
        )
        is None,
        "Hosted adapter validation may parse but must not execute the full build",
    )
    for forbidden in ("Tencent-Hunyuan", "git fetch", "Invoke-WebRequest"):
        require(
            forbidden not in static_job,
            f"Hosted adapter validation may not materialize Tencent source: {forbidden}",
        )
    print("Mujassam Hunyuan3D-2.1 adapter static validation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
