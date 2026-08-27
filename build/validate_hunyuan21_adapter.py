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
LOCAL_BUILD_SCRIPT = ROOT / "build" / "build-hunyuan21-local.ps1"
LOCAL_BUILD_LAUNCHER = ROOT / "build" / "Build-Hunyuan21-Local.cmd"
LOCAL_BUILD_RECOVERY = ROOT / "build" / "resume-hunyuan21-local.ps1"
LOCAL_INSTALLER = ROOT / "installer" / "install-hunyuan21-local.ps1"
LOCAL_RESTORE = ROOT / "installer" / "restore-hunyuan21-local.ps1"
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
    preflight = worker_text.find('_validate_base_runtime(payload["texture_mode"])')
    prepare = worker_text.find("image_info = _prepare_image")
    download = worker_text.find("model_root, dino_root, downloaded = _download_models")
    require(
        0 <= preflight < prepare < download,
        "Exact base-runtime preflight must run before image/model setup",
    )
    paint_fix = worker_text.find("from utils.torchvision_fix import apply_fix")
    paint_import = worker_text.find(
        "from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline"
    )
    require(
        0 <= paint_fix < paint_import and "if not apply_fix():" in worker_text,
        "Paint must apply the reviewed torchvision compatibility shim before import",
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
