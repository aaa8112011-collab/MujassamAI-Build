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
SOURCE_COMMIT = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
CI_PROVIDER = "CI validation build — Hunyuan3D 2.1 disabled"
REQUIRED_DISTRIBUTION_NOTICE = (
    "Tencent Hunyuan 3D 2.1 is licensed under the Tencent Hunyuan 3D 2.1 "
    "Community License Agreement, Copyright © 2025 Tencent. All Rights Reserved. "
    "The trademark rights of “Tencent Hunyuan” are owned by Tencent or its affiliate."
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


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
    base_worker = (ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    require("hunyuan3d_2_1_pbr" in base_worker, "Base worker has no 2.1 dispatch")
    require("hunyuan21_worker.py" in base_worker, "Base worker does not dispatch 2.1")

    main_form = (ROOT / "app" / "MainForm.cs").read_text(encoding="utf-8")
    require(
        main_form.count("@@MUJASSAM_PROVIDER_LEGAL_NAME@@") == 1,
        "MainForm must contain exactly one provider build token",
    )
    require(CI_PROVIDER in main_form, "MainForm does not reject the CI provider sentinel")
    distribution_notice = (ENGINE / "NOTICE.txt").read_text(encoding="utf-8")
    require(
        distribution_notice.count("@@MUJASSAM_PROVIDER_LEGAL_NAME@@") == 1,
        "Distribution notice must contain exactly one provider build token",
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

    requirements = (ROOT / "build" / "hunyuan21.requirements.lock.txt").read_text(
        encoding="utf-8"
    )
    for forbidden_runtime_package in (
        "basicsr",
        "realesrgan",
        "pymeshlab",
        "omegaconf",
    ):
        require(
            re.search(
                rf"(?mi)^{re.escape(forbidden_runtime_package)}==", requirements
            )
            is None,
            f"Unused source/GPL runtime package must not be bundled: {forbidden_runtime_package}",
        )

    build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
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
        '"build\\hunyuan21.build.requirements.lock.txt"' in build_script
        and "-not $UseHashedBuildRequirements" in build_script
        and "pip install --no-cache-dir --require-hashes" in build_script
        and "-r $BuildRequirements" in build_script,
        "Hash-locked builds must cover build tools and PyTorch as well as runtime dependencies",
    )
    require(
        "--no-deps --target $PythonPackages.FullName" in build_script
        and "load_realesrgan_x4plus" in build_script,
        "Runtime packaging must use the complete wheel lock and self-contained x4 inference",
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
    for forbidden in (
        "build-hunyuan21-update.ps1",
        "Tencent-Hunyuan",
        "git fetch",
        "Invoke-WebRequest",
    ):
        require(
            forbidden not in static_job,
            f"Hosted adapter validation may not materialize Tencent source: {forbidden}",
        )
    print("Mujassam Hunyuan3D-2.1 adapter static validation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
