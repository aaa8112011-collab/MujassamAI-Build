[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if ($PSVersionTable.PSVersion -lt [version]"7.4") {
    throw (
        "Hunyuan3D-2.1 building requires PowerShell 7.4 or newer so native " +
        "git, Python, pip, compiler, and CUDA failures stop the build reliably"
    )
}
$PSNativeCommandUseErrorActionPreference = $true

$SourceCommit = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
$CudaInstallerMd5 = "8901c95cd9e20b8fc73fc76e96065d03"
$TorchVersion = "2.5.1+cu124"
$TorchVisionVersion = "0.20.1+cu124"
$CudaArchitectures = "7.5;8.0;8.6;8.9+PTX"
$RealEsrganSha256 = "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"
$RealEsrganBytes = [Int64]67040989
$ArchiveName = if ([string]::IsNullOrWhiteSpace($env:ARCHIVE_NAME)) {
    "MujassamAI-Hunyuan21-PBR-Update-v1.zip"
} else {
    $env:ARCHIVE_NAME
}

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$SourceRoot = Join-Path $env:RUNNER_TEMP "hy3d21-src"
$BuildVenv = Join-Path $env:RUNNER_TEMP "hy3d21-venv"
$Wheelhouse = Join-Path $env:RUNNER_TEMP "hy3d21-wheels"
$MeshBuild = Join-Path $env:RUNNER_TEMP "hy3d21-mesh-inpaint"
$BuildRoot = Join-Path $env:RUNNER_TEMP "hy3d21-update"
$Stage = Join-Path $BuildRoot "stage"
$VerifyRoot = Join-Path $BuildRoot "verify"
$GuiTestRoot = Join-Path $BuildRoot "gui-test"
$ReleaseRoot = Join-Path $RepositoryRoot "release"
$Archive = Join-Path $ReleaseRoot $ArchiveName
$EngineSource = Join-Path $RepositoryRoot "app\engines\hunyuan21"
$Requirements = Join-Path $RepositoryRoot "build\hunyuan21.requirements.lock.txt"
$BuildRequirements = Join-Path $RepositoryRoot `
    "build\hunyuan21.build.requirements.lock.txt"
$PatchScript = Join-Path $RepositoryRoot "build\patch_hunyuan21_windows.py"
$PersonalLocalUse = $env:MUJASSAM_HY21_LOCAL_PERSONAL_USE -ceq "1"
$UsageScope = if ($PersonalLocalUse) {
    "personal_local_only"
} else {
    "third_party_provider"
}
$ConfiguredProvider = if ([string]::IsNullOrWhiteSpace(
    $env:MUJASSAM_PROVIDER_LEGAL_NAME)) {
    ""
} else {
    $env:MUJASSAM_PROVIDER_LEGAL_NAME.Trim()
}
if ($PersonalLocalUse) {
    if (-not [string]::IsNullOrWhiteSpace($ConfiguredProvider)) {
        throw (
            "A personal-local-only build must not configure or store a " +
            "third-party provider legal name"
        )
    }
    $ProviderLegalName = $null
    $ProviderTokenValue = "not applicable (personal local use only)"
} else {
    $ProviderLegalName = $ConfiguredProvider
    if ([string]::IsNullOrWhiteSpace($ProviderLegalName) -or
        $ProviderLegalName -ceq "CI validation build — Hunyuan3D 2.1 disabled") {
        throw (
            "Third-party Hunyuan3D-2.1 builds require the provider's actual " +
            "full legal name in MUJASSAM_PROVIDER_LEGAL_NAME"
        )
    }
    $ProviderTokenValue = $ProviderLegalName
}
$InvalidProviderCharacters = @($ProviderTokenValue.ToCharArray() | Where-Object {
    [char]::IsControl($_) -or
    [char]::GetUnicodeCategory($_) -in @(
        [Globalization.UnicodeCategory]::LineSeparator,
        [Globalization.UnicodeCategory]::ParagraphSeparator
    )
})
if ($InvalidProviderCharacters.Count -ne 0 -or $ProviderTokenValue.Contains("@@")) {
    throw "The configured Hunyuan3D-2.1 provider value is unsafe"
}

function Get-NormalizedLegalText([string]$Path) {
    $Text = [IO.File]::ReadAllText($Path)
    $Text = $Text.Replace("`r`n", "`n").Replace("`r", "`n")
    if ($Text.Contains([char]0)) {
        throw "Legal text contains NUL: $Path"
    }
    return $Text
}

function Assert-CompleteSha256Lock([string]$Path, [string]$Label) {
    if (-not (Test-Path $Path -PathType Leaf)) {
        throw "Missing $Label dependency lock: $Path"
    }
    $Seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    $Count = 0
    $LineNumber = 0
    foreach ($RawLine in [IO.File]::ReadAllLines($Path)) {
        $LineNumber++
        $Line = $RawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($Line) -or $Line.StartsWith("#")) {
            continue
        }
        if ($Line -notmatch (
            '^([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})$'
        )) {
            throw (
                "$Label lock entry must be one exact wheel pin and one lowercase " +
                "SHA-256 at ${Path}:$LineNumber"
            )
        }
        $NormalizedName = [regex]::Replace(
            $Matches[1].ToLowerInvariant(), '[-_.]+', '-')
        if (-not $Seen.Add($NormalizedName)) {
            throw "Duplicate package in $Label dependency lock: $($Matches[1])"
        }
        $Count++
    }
    if ($Count -eq 0) {
        throw "$Label dependency lock contains no packages: $Path"
    }
    return $Count
}

foreach ($Path in @(
    $SourceRoot, $BuildVenv, $Wheelhouse, $MeshBuild, $BuildRoot, $ReleaseRoot
)) {
    if (Test-Path $Path) {
        throw "Refusing to overwrite an existing build path: $Path"
    }
}
foreach ($Path in @(
    (Join-Path $RepositoryRoot "app\worker.py"),
    (Join-Path $RepositoryRoot "app\MainForm.cs"),
    (Join-Path $RepositoryRoot "app\Program.cs"),
    (Join-Path $RepositoryRoot "NOTICE_THIRD_PARTY.md"),
    (Join-Path $EngineSource "hunyuan21_worker.py"),
    (Join-Path $EngineSource "ENGINE-MANIFEST.json"),
    (Join-Path $EngineSource "LICENSE-HUNYUAN3D-2.1.txt"),
    (Join-Path $EngineSource "NOTICE-HUNYUAN3D-2.1.txt"),
    (Join-Path $EngineSource "NOTICE.txt"),
    (Join-Path $EngineSource "MODIFICATIONS.txt"),
    (Join-Path $RepositoryRoot "licenses\DINOv2-Apache-2.0.txt"),
    $Requirements,
    $PatchScript
)) {
    if (-not (Test-Path $Path -PathType Leaf)) {
        throw "Missing required Hunyuan3D-2.1 packaging file: $Path"
    }
}
$RequireHashedDependencies = `
    $env:MUJASSAM_REQUIRE_HASHED_DEPENDENCIES -eq "1"
$UseHashedRequirements = (Get-Content $Requirements -Raw).Contains("--hash=sha256:")
$UseHashedBuildRequirements = (Test-Path $BuildRequirements -PathType Leaf) -and
    (Get-Content $BuildRequirements -Raw).Contains("--hash=sha256:")
if ($RequireHashedDependencies) {
    $RuntimeLockCount = Assert-CompleteSha256Lock $Requirements "runtime"
    $BuildLockCount = Assert-CompleteSha256Lock $BuildRequirements "build"
    Write-Host "Verified dependency lock syntax: $RuntimeLockCount runtime, $BuildLockCount build"
} elseif ($UseHashedRequirements -or $UseHashedBuildRequirements) {
    throw (
        "Hashed Hunyuan3D-2.1 locks may only be used when " +
        "MUJASSAM_REQUIRE_HASHED_DEPENDENCIES=1"
    )
}
# Fetching the upstream source is itself use/reproduction under the Hunyuan
# license.  A full build must therefore fail before network access unless the
# operator explicitly accepted the current license/AUP and confirmed that this
# runner is physically inside the license Territory.
if ($env:MUJASSAM_HY21_LICENSE_ACCEPTED -cne "1" -or
    $env:MUJASSAM_HY21_TERRITORY_CONFIRMED -cne "1") {
    throw (
        "Hunyuan3D-2.1 source materialization requires explicit license/AUP " +
        "acceptance and confirmation that the build runner is inside the " +
        "license Territory"
    )
}

Write-Host "Fetching official Hunyuan3D-2.1 source at $SourceCommit"
New-Item -ItemType Directory -Path $SourceRoot | Out-Null
git -C $SourceRoot init --quiet
git -C $SourceRoot remote add origin https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git
git -C $SourceRoot fetch --quiet --depth 1 origin $SourceCommit
git -C $SourceRoot checkout --quiet --detach FETCH_HEAD
$ResolvedCommit = (git -C $SourceRoot rev-parse HEAD).Trim()
if ($ResolvedCommit -cne $SourceCommit) {
    throw "Hunyuan3D-2.1 source mismatch: expected $SourceCommit, got $ResolvedCommit"
}
$OfficialLicense = Join-Path $SourceRoot "LICENSE"
$OfficialNotice = Join-Path $SourceRoot "Notice.txt"
$PackagedLicense = Join-Path $EngineSource "LICENSE-HUNYUAN3D-2.1.txt"
$PackagedNotice = Join-Path $EngineSource "NOTICE-HUNYUAN3D-2.1.txt"
$OfficialLicenseText = Get-NormalizedLegalText $OfficialLicense
$OfficialNoticeText = Get-NormalizedLegalText $OfficialNotice
$PackagedLicenseText = Get-NormalizedLegalText $PackagedLicense
$PackagedNoticeText = Get-NormalizedLegalText $PackagedNotice
# The repository text files intentionally have exactly one packaging LF after
# the pinned Git-blob contents.  Permit that known byte only; any other content
# or extra blank line remains a hard failure.
if ($PackagedLicenseText -cne ($OfficialLicenseText + "`n")) {
    throw "Packaged Hunyuan3D-2.1 LICENSE differs from pinned upstream"
}
if ($PackagedNoticeText -cne ($OfficialNoticeText + "`n")) {
    throw "Packaged Hunyuan3D-2.1 Notice differs from pinned upstream"
}

$CudaInstallerName = "cuda_12.4.1_windows_network.exe"
$CudaInstaller = Join-Path $env:RUNNER_TEMP $CudaInstallerName
$CudaInstallerUrl = (
    "https://developer.download.nvidia.com/compute/cuda/12.4.1/" +
    "network_installers/$CudaInstallerName"
)
Write-Host "Installing minimal CUDA 12.4 build toolkit"
Invoke-WebRequest $CudaInstallerUrl -OutFile $CudaInstaller
$CudaSignature = Get-AuthenticodeSignature -FilePath $CudaInstaller
if ($CudaSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
    $null -eq $CudaSignature.SignerCertificate -or
    $CudaSignature.SignerCertificate.Subject -notmatch
        '(^|,\s*)O=NVIDIA Corporation(,|$)' -or
    $CudaSignature.SignerCertificate.Issuer -notmatch
        'DigiCert') {
    throw "CUDA installer does not have a valid NVIDIA/DigiCert Authenticode signature"
}
$ActualCudaSha256 = (
    Get-FileHash $CudaInstaller -Algorithm SHA256).Hash.ToLowerInvariant()
$ActualCudaMd5 = (Get-FileHash $CudaInstaller -Algorithm MD5).Hash.ToLowerInvariant()
if ($ActualCudaMd5 -cne $CudaInstallerMd5) {
    throw "CUDA installer checksum mismatch: $ActualCudaMd5"
}
Write-Host "Verified CUDA installer SHA-256: $ActualCudaSha256"
if (-not [string]::IsNullOrWhiteSpace($env:GITHUB_STEP_SUMMARY)) {
    "- CUDA 12.4.1 network installer SHA-256: ``$ActualCudaSha256``" |
        Out-File $env:GITHUB_STEP_SUMMARY -Append -Encoding utf8
}
$CudaProcess = Start-Process -FilePath $CudaInstaller -ArgumentList @(
    "-s", "-n", "nvcc_12.4", "cudart_12.4", "thrust_12.4",
    "cublas_12.4", "cublas_dev_12.4", "cusolver_12.4",
    "cusolver_dev_12.4", "cusparse_12.4", "cusparse_dev_12.4",
    "nvjitlink_12.4", "cuobjdump_12.4"
) -Wait -PassThru
if ($CudaProcess.ExitCode -notin @(0, 3010)) {
    throw "CUDA installer failed with exit code $($CudaProcess.ExitCode)"
}
Remove-Item $CudaInstaller -Force

$CudaHome = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4"
$Nvcc = Join-Path $CudaHome "bin\nvcc.exe"
if (-not (Test-Path $Nvcc -PathType Leaf)) {
    throw "CUDA compiler was not installed at $Nvcc"
}
$env:CUDA_HOME = $CudaHome
$env:CUDA_PATH = $CudaHome
$env:PATH = "$CudaHome\bin;$env:PATH"

$ProgramFilesX86 = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ProgramFilesX86)
$VsWhere = Join-Path $ProgramFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
$Msvc142Component = "Microsoft.VisualStudio.Component.VC.14.29.16.11.x86.x64"
$VsInstall = (& $VsWhere -latest -products * `
    -requires $Msvc142Component `
    -property installationPath | Select-Object -First 1)
if (-not $VsInstall) {
    throw "Visual Studio with MSVC v142/14.29 x64 was not found"
}
Import-Module "$VsInstall\Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Enter-VsDevShell -VsInstallPath $VsInstall -SkipAutomaticLocation `
    -DevCmdArguments "-arch=x64 -host_arch=x64 -vcvars_ver=14.29"
$ClPath = (Get-Command cl.exe).Source
if ($ClPath -notmatch "\\14\.29\.") {
    throw "MSVC v142/14.29 was requested but cl.exe resolved to $ClPath"
}
Write-Host "Using compiler $ClPath"
& $Nvcc --version

Write-Host "Creating pinned Python build environment"
python -m venv $BuildVenv
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
if ($RequireHashedDependencies) {
    & $BuildPython -m pip install --disable-pip-version-check --no-cache-dir `
        --only-binary=:all: --require-hashes `
        --extra-index-url https://download.pytorch.org/whl/cu124 `
        -r $BuildRequirements
} else {
    # Local, non-distributable developer fallback.  The validation workflow
    # always selects the fully hash-locked branch above.
    & $BuildPython -m pip install --no-cache-dir `
        "setuptools==69.5.1" "wheel==0.45.1" "ninja==1.11.1.3" "pybind11==2.13.6"
    & $BuildPython -m pip install --no-cache-dir `
        --extra-index-url https://download.pytorch.org/whl/cu124 `
        "torch==$TorchVersion" "torchvision==$TorchVisionVersion"
}
& $BuildPython -m pip check

$env:USE_CUDA = "1"
$env:USE_NATIVE_ARCH = "0"
$env:TORCH_CUDA_ARCH_LIST = $CudaArchitectures
$env:MAX_JOBS = "2"
$env:DISTUTILS_USE_SDK = "1"
$env:MSSdk = "1"
New-Item -ItemType Directory -Path $Wheelhouse, $MeshBuild | Out-Null

Write-Host "Selecting the official Windows rasterizer sources"
& $BuildPython -I -X utf8 $PatchScript source $SourceRoot

Write-Host "Building the Hunyuan3D-2.1 CUDA rasterizer"
$RasterizerSource = Join-Path $SourceRoot "hy3dpaint\custom_rasterizer"
& $BuildPython -m pip wheel --no-cache-dir --no-deps --no-build-isolation `
    --wheel-dir $Wheelhouse $RasterizerSource

Write-Host "Building the Hunyuan3D-2.1 mesh inpaint extension"
Copy-Item (Join-Path $SourceRoot `
    "hy3dpaint\DifferentiableRenderer\mesh_inpaint_processor.cpp") $MeshBuild
$MeshSetup = @'
from setuptools import setup
from pybind11.setup_helpers import Pybind11Extension, build_ext

setup(
    name="mesh_inpaint_processor",
    version="0.1.0",
    ext_modules=[Pybind11Extension(
        "mesh_inpaint_processor",
        ["mesh_inpaint_processor.cpp"],
        cxx_std=11,
    )],
    cmdclass={"build_ext": build_ext},
)
'@
[IO.File]::WriteAllText(
    (Join-Path $MeshBuild "setup.py"), $MeshSetup, [Text.UTF8Encoding]::new($false))
& $BuildPython -m pip wheel --no-cache-dir --no-deps --no-build-isolation `
    --wheel-dir $Wheelhouse $MeshBuild

$RasterizerWheels = @(Get-ChildItem $Wheelhouse -Filter "custom_rasterizer-*.whl")
$MeshWheels = @(Get-ChildItem $Wheelhouse -Filter "mesh_inpaint_processor-*.whl")
if ($RasterizerWheels.Count -ne 1 -or $MeshWheels.Count -ne 1) {
    throw "Expected one rasterizer wheel and one mesh-inpaint wheel"
}

Write-Host "Staging the root-layout Hunyuan3D-2.1 PBR update"
$StageApp = New-Item -ItemType Directory -Path (Join-Path $Stage "app") -Force
$StageQuality = New-Item -ItemType Directory -Path `
    (Join-Path $StageApp "quality") -Force
$StageEngine = New-Item -ItemType Directory -Path `
    (Join-Path $StageApp "engines\hunyuan21") -Force
$StageVendor = New-Item -ItemType Directory -Path `
    (Join-Path $StageEngine "vendor\Hunyuan3D-2.1") -Force
$StageShape = New-Item -ItemType Directory -Path `
    (Join-Path $StageVendor "hy3dshape") -Force
$StagePaint = New-Item -ItemType Directory -Path `
    (Join-Path $StageVendor "hy3dpaint") -Force
$PythonPackages = New-Item -ItemType Directory -Path `
    (Join-Path $StageEngine "python_packages") -Force
$EngineModels = New-Item -ItemType Directory -Path `
    (Join-Path $StageEngine "models") -Force
$StageLicenses = New-Item -ItemType Directory -Path `
    (Join-Path $Stage "licenses") -Force
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null

Copy-Item (Join-Path $RepositoryRoot "app\worker.py") $StageApp.FullName -Force
Copy-Item (Join-Path $RepositoryRoot "app\quality\*") `
    $StageQuality.FullName -Recurse -Force
Copy-Item (Join-Path $RepositoryRoot "NOTICE_THIRD_PARTY.md") $Stage -Force
Get-ChildItem $EngineSource -File | Copy-Item -Destination $StageEngine.FullName -Force
$StageManifestPath = Join-Path $StageEngine "ENGINE-MANIFEST.json"
$StageEngineManifest = Get-Content $StageManifestPath -Raw | ConvertFrom-Json
foreach ($LegalFile in @(
    [ordered]@{
        Name = "LICENSE-HUNYUAN3D-2.1.txt"
        Sha256 = [string]$StageEngineManifest.source.license_file_sha256
    },
    [ordered]@{
        Name = "NOTICE-HUNYUAN3D-2.1.txt"
        Sha256 = [string]$StageEngineManifest.source.upstream_notice_file_sha256
    }
)) {
    if ($LegalFile.Sha256 -notmatch '^[0-9a-f]{64}$') {
        throw "Engine manifest has an invalid hash for $($LegalFile.Name)"
    }
    $LegalPath = Join-Path $StageEngine $LegalFile.Name
    $LegalText = [IO.File]::ReadAllText($LegalPath)
    $LegalText = $LegalText.Replace("`r`n", "`n").Replace("`r", "`n")
    if ($LegalText.Contains([char]0)) {
        throw "Staged legal text contains NUL: $($LegalFile.Name)"
    }
    [IO.File]::WriteAllText(
        $LegalPath, $LegalText, [Text.UTF8Encoding]::new($false))
    $LegalHash = (Get-FileHash $LegalPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($LegalHash -cne $LegalFile.Sha256) {
        throw "Canonical staged legal text hash mismatch: $($LegalFile.Name)"
    }
}
$DistributionNoticePath = Join-Path $StageEngine "NOTICE.txt"
$DistributionNoticeText = Get-Content $DistributionNoticePath -Raw
$DistributionNoticeText = $DistributionNoticeText.Replace(
    "`r`n", "`n").Replace("`r", "`n")
$UsageScopeToken = "@@MUJASSAM_HY21_USAGE_SCOPE@@"
$UsageScopeTokenCount = [regex]::Matches(
    $DistributionNoticeText, [regex]::Escape($UsageScopeToken)).Count
if ($UsageScopeTokenCount -ne 1) {
    throw "Expected one usage-scope token in Hunyuan3D-2.1 NOTICE, found $UsageScopeTokenCount"
}
$DistributionNoticeToken = "@@MUJASSAM_PROVIDER_LEGAL_NAME@@"
$DistributionNoticeTokenCount = [regex]::Matches(
    $DistributionNoticeText, [regex]::Escape($DistributionNoticeToken)).Count
if ($DistributionNoticeTokenCount -ne 1) {
    throw "Expected one provider legal-name token in Hunyuan3D-2.1 NOTICE, found $DistributionNoticeTokenCount"
}
$DistributionNoticeText = $DistributionNoticeText.Replace(
    $UsageScopeToken, $UsageScope)
$DistributionNoticeText = $DistributionNoticeText.Replace(
    $DistributionNoticeToken, $ProviderTokenValue)
[IO.File]::WriteAllText(
    $DistributionNoticePath, $DistributionNoticeText,
    [Text.UTF8Encoding]::new($false))
Copy-Item (Join-Path $RepositoryRoot "licenses\*") `
    $StageLicenses.FullName -Force

Copy-Item (Join-Path $SourceRoot "hy3dshape\hy3dshape") `
    $StageShape.FullName -Recurse -Force
foreach ($Directory in @(
    "DifferentiableRenderer", "cfgs", "hunyuanpaintpbr", "utils"
)) {
    Copy-Item (Join-Path $SourceRoot "hy3dpaint\$Directory") `
        $StagePaint.FullName -Recurse -Force
}
foreach ($File in @("textureGenPipeline.py", "convert_utils.py")) {
    Copy-Item (Join-Path $SourceRoot "hy3dpaint\$File") `
        $StagePaint.FullName -Force
}
Get-ChildItem $StageVendor.FullName -Recurse -Directory -Filter "__pycache__" |
    Remove-Item -Recurse -Force
Get-ChildItem $StagePaint.FullName -Recurse -File |
    Where-Object Extension -in @(".cpp", ".cu", ".h", ".sh") |
    Remove-Item -Force
& $BuildPython -I -X utf8 $PatchScript vendor $StageVendor.FullName

Write-Host "Installing isolated Hunyuan3D-2.1 Python dependencies"
if ($UseHashedRequirements) {
    # The lock is a complete, reviewed wheel closure.  --no-deps prevents pip
    # from resolving or downloading anything outside those exact artifacts and
    # avoids installing the base-owned CUDA PyTorch a second time.
    & $BuildPython -m pip install --no-cache-dir --only-binary=:all: `
        --no-deps --target $PythonPackages.FullName `
        --require-hashes -r $Requirements
} else {
    & $BuildPython -m pip install --no-cache-dir `
        --target $PythonPackages.FullName -r $Requirements
}

# Engine-local dependencies must never shadow the portable base CUDA PyTorch.
$AccidentalTorch = @(Get-ChildItem $PythonPackages.FullName -Force | Where-Object {
    $_.Name -match '^(torch|torchvision|torchaudio)($|[-_.])' -or
    $_.Name -in @('torchgen', 'functorch')
})
if ($AccidentalTorch.Count -gt 0) {
    Write-Host "Removing target-only PyTorch: $($AccidentalTorch.Name -join ', ')"
    $AccidentalTorch | Remove-Item -Recurse -Force
}
$TargetSymPy = @(Get-ChildItem $PythonPackages.FullName -Force | Where-Object {
    $_.Name -match '^sympy($|[-_.])'
})
if ($TargetSymPy.Count -gt 0) {
    $TargetSymPy | Remove-Item -Recurse -Force
}
& $BuildPython -m pip install --no-cache-dir --no-deps `
    --target $PythonPackages.FullName `
    $RasterizerWheels[0].FullName $MeshWheels[0].FullName

$MeshModules = @(Get-ChildItem $PythonPackages.FullName -File `
    -Filter "mesh_inpaint_processor*.pyd")
if ($MeshModules.Count -ne 1) {
    throw "Expected one installed mesh_inpaint_processor extension"
}
Move-Item $MeshModules[0].FullName `
    (Join-Path $StagePaint "DifferentiableRenderer\$($MeshModules[0].Name)") -Force
$BundledTorch = @(Get-ChildItem $PythonPackages.FullName -Force | Where-Object {
    $_.Name -match '^(torch|torchvision|torchaudio)($|[-_.])' -or
    $_.Name -in @('torchgen', 'functorch')
})
if ($BundledTorch.Count -ne 0) {
    throw "The Hunyuan3D-2.1 add-on contains a second PyTorch installation"
}

Write-Host "Downloading and pinning the official RealESRGAN x4 model"
$RealEsrganPath = Join-Path $EngineModels "RealESRGAN_x4plus.pth"
Invoke-WebRequest `
    "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth" `
    -OutFile $RealEsrganPath
$RealEsrganFile = Get-Item $RealEsrganPath
$ActualRealEsrganHash = (Get-FileHash $RealEsrganPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($RealEsrganFile.Length -ne $RealEsrganBytes -or
    $ActualRealEsrganHash -cne $RealEsrganSha256) {
    throw "RealESRGAN x4 model verification failed"
}

Write-Host "Compiling current MujassamAI.exe"
$Csc = Join-Path $VsInstall "MSBuild\Current\Bin\Roslyn\csc.exe"
$ReferenceRoot = "C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
$References = @(
    "System.dll", "System.Core.dll", "System.Drawing.dll",
    "System.Web.Extensions.dll", "System.Windows.Forms.dll"
) | ForEach-Object {
    $ReferencePath = Join-Path $ReferenceRoot $_
    if (-not (Test-Path $ReferencePath -PathType Leaf)) {
        throw "Missing .NET reference: $ReferencePath"
    }
    "/reference:$ReferencePath"
}
$ManifestPath = Join-Path $BuildRoot "MujassamAI.manifest"
$AppManifest = @'
<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <assemblyIdentity version="1.0.0.0" name="MujassamAI.Portable" />
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3"><security><requestedPrivileges>
    <requestedExecutionLevel level="asInvoker" uiAccess="false" />
  </requestedPrivileges></security></trustInfo>
  <application xmlns="urn:schemas-microsoft-com:asm.v3"><windowsSettings>
    <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
  </windowsSettings></application>
</assembly>
'@
[IO.File]::WriteAllText($ManifestPath, $AppManifest, [Text.UTF8Encoding]::new($false))
$MainFormSource = Get-Content (Join-Path $RepositoryRoot "app\MainForm.cs") -Raw
$MainFormUsageToken = "@@MUJASSAM_HY21_USAGE_SCOPE@@"
$MainFormUsageTokenCount = [regex]::Matches(
    $MainFormSource, [regex]::Escape($MainFormUsageToken)).Count
if ($MainFormUsageTokenCount -ne 1) {
    throw "Expected one usage-scope token in MainForm.cs, found $MainFormUsageTokenCount"
}
$ProviderToken = "@@MUJASSAM_PROVIDER_LEGAL_NAME@@"
$ProviderTokenCount = [regex]::Matches(
    $MainFormSource, [regex]::Escape($ProviderToken)).Count
if ($ProviderTokenCount -ne 1) {
    throw "Expected one provider legal-name token in MainForm.cs, found $ProviderTokenCount"
}
$EscapedProviderLegalName = $ProviderTokenValue.Replace("\", "\\").Replace('"', '\"')
$MainFormSource = $MainFormSource.Replace($MainFormUsageToken, $UsageScope)
$MainFormSource = $MainFormSource.Replace(
    $ProviderToken, $EscapedProviderLegalName)
$UnresolvedMainFormMarkers = @(
    [regex]::Matches($MainFormSource, '@@[A-Z][A-Z0-9_]*@@') |
        ForEach-Object { $_.Value } |
        Sort-Object -Unique
)
if ($UnresolvedMainFormMarkers.Count -ne 0) {
    throw (
        "Compiled MainForm.cs contains unresolved build marker(s): " +
        ($UnresolvedMainFormMarkers -join ", ")
    )
}
$PatchedMainForm = Join-Path $BuildRoot "MainForm.cs"
[IO.File]::WriteAllText(
    $PatchedMainForm, $MainFormSource, [Text.UTF8Encoding]::new($false))
$Executable = Join-Path $Stage "MujassamAI.exe"
$CscArguments = @(
    "/nologo", "/noconfig", "/target:winexe", "/platform:x64",
    "/optimize+", "/debug-", "/langversion:latest",
    "/win32manifest:$ManifestPath", "/out:$Executable"
) + $References + @(
    (Join-Path $RepositoryRoot "app\Program.cs"),
    $PatchedMainForm
)
& $Csc @CscArguments
if (-not (Test-Path $Executable -PathType Leaf)) {
    throw "MujassamAI.exe was not produced"
}

Write-Host "Running model-free engine and ABI tests"
$EngineWorker = Join-Path $StageEngine "hunyuan21_worker.py"
& $BuildPython -m py_compile (Join-Path $StageApp "worker.py") $EngineWorker
& $BuildPython -I -X utf8 (Join-Path $StageApp "worker.py") --self-test
& $BuildPython -I -X utf8 $EngineWorker --self-test
$env:MJ_HY21_ENGINE = $StageEngine.FullName
$env:MJ_EXPECTED_TORCH = $TorchVersion
$ImportSmokeTest = @'
import os
import sys
root = os.environ["MJ_HY21_ENGINE"]
vendor = os.path.join(root, "vendor", "Hunyuan3D-2.1")
sys.path.insert(0, os.path.join(root, "python_packages"))
sys.path.insert(0, os.path.join(vendor, "hy3dpaint"))
sys.path.insert(0, os.path.join(vendor, "hy3dshape"))
sys.path.insert(0, os.path.realpath(os.path.join(root, "..", "..")))
import torch
assert torch.__version__ == os.environ["MJ_EXPECTED_TORCH"], torch.__version__
assert os.path.commonpath((os.path.realpath(torch.__file__), os.path.realpath(root))) != os.path.realpath(root)
from utils.torchvision_fix import apply_fix
assert apply_fix()
worker_source = open(os.path.join(root, "hunyuan21_worker.py"), encoding="utf-8").read()
paint_source = worker_source[worker_source.index("def _stage_paint"):]
assert paint_source.index("from utils.torchvision_fix import apply_fix") < paint_source.index("if not apply_fix()")
assert paint_source.index("if not apply_fix()") < paint_source.index("from textureGenPipeline import")
import custom_rasterizer
from DifferentiableRenderer import mesh_inpaint_processor
from hy3dshape import Hunyuan3DDiTFlowMatchingPipeline
from hy3dshape.postprocessors import DegenerateFaceRemover, FaceReducer, FloaterRemover
import importlib.metadata
import trimesh
assert importlib.metadata.version("pymeshlab") == "2022.2.post3"
mesh_smoke = trimesh.creation.icosphere(subdivisions=1)
mesh_smoke = FaceReducer()(mesh_smoke, max_facenum=20)
mesh_smoke = FloaterRemover()(mesh_smoke)
mesh_smoke = DegenerateFaceRemover()(mesh_smoke)
assert isinstance(mesh_smoke, trimesh.Trimesh) and len(mesh_smoke.faces) > 0
import inspect
from quality.realesrgan_x2 import REALESRGAN_X4PLUS, load_realesrgan_x4plus
assert REALESRGAN_X4PLUS.sha256 == "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"
assert "basicsr" not in sys.modules and "realesrgan" not in sys.modules
from utils.image_super_utils import imageSuperNet
assert "load_realesrgan_x4plus" in inspect.getsource(imageSuperNet.__init__)
from textureGenPipeline import Hunyuan3DPaintConfig, Hunyuan3DPaintPipeline
from hunyuanpaintpbr.unet.modules import Dino_v2
assert "pytorch_lightning" not in sys.modules

# Exercise the engine's exact checkpoint guard against the packaged PyTorch
# ABI.  In particular, this proves that torch.load accepts the same verified
# file handle used to hash the checkpoint (rather than reopening its path).
import hashlib
import importlib.util
import tempfile
worker_spec = importlib.util.spec_from_file_location(
    "mujassam_h21_smoke_worker", os.path.join(root, "hunyuan21_worker.py")
)
assert worker_spec is not None and worker_spec.loader is not None
engine_worker = importlib.util.module_from_spec(worker_spec)
sys.modules[worker_spec.name] = engine_worker
worker_spec.loader.exec_module(engine_worker)
with tempfile.TemporaryDirectory() as temporary_directory:
    checkpoint = engine_worker.Path(temporary_directory) / "tiny.pth"
    expected_tensor = torch.arange(4)
    torch.save({"value": expected_tensor}, checkpoint)
    checkpoint_bytes = checkpoint.read_bytes()
    checkpoint_sha256 = hashlib.sha256(checkpoint_bytes).hexdigest()
    engine_worker._force_weights_only_torch_load(
        torch, {checkpoint: (len(checkpoint_bytes), checkpoint_sha256)}
    )
    loaded = torch.load(checkpoint)
    assert torch.equal(loaded["value"], expected_tensor)
    try:
        torch.load(checkpoint, mmap=True)
    except RuntimeError as exc:
        assert "mmap was blocked" in str(exc)
    else:
        raise AssertionError("Verified checkpoint guard permitted mmap=True")
print("Hunyuan3D-2.1 source, PBR pipeline, and compiled extensions: OK")
'@
& $BuildPython -I -X utf8 -c $ImportSmokeTest

New-Item -ItemType Directory -Path `
    (Join-Path $GuiTestRoot "app\quality"),
    (Join-Path $GuiTestRoot "rt"),
    (Join-Path $GuiTestRoot "models\realesrgan") -Force | Out-Null
Copy-Item $Executable (Join-Path $GuiTestRoot "MujassamAI.exe")
Copy-Item (Join-Path $StageApp "worker.py") (Join-Path $GuiTestRoot "app\worker.py")
Copy-Item (Join-Path $StageQuality "*") `
    (Join-Path $GuiTestRoot "app\quality") -Recurse
New-Item -ItemType File -Path (Join-Path $GuiTestRoot "rt\python.exe") -Force | Out-Null
New-Item -ItemType File -Path `
    (Join-Path $GuiTestRoot "models\realesrgan\RealESRGAN_x2plus.pth") -Force | Out-Null
$GuiReport = Join-Path $GuiTestRoot "self-test.txt"
$GuiProcess = Start-Process -FilePath (Join-Path $GuiTestRoot "MujassamAI.exe") `
    -ArgumentList @("--self-test", $GuiReport) -Wait -PassThru
if ($GuiProcess.ExitCode -ne 0 -or -not (Test-Path $GuiReport -PathType Leaf)) {
    throw "MujassamAI.exe self-test failed with exit code $($GuiProcess.ExitCode)"
}
$GuiText = Get-Content $GuiReport -Raw
if (-not $GuiText.Contains("PortableLayout=OK") -or
    -not $GuiText.Contains("JobSchema=OK") -or
    -not $GuiText.Contains("Is64BitProcess=True")) {
    Write-Host $GuiText
    throw "MujassamAI.exe self-test reported an invalid layout/schema"
}

$RequiredFiles = @(
    "MujassamAI.exe",
    "NOTICE_THIRD_PARTY.md",
    "app/worker.py",
    "app/engines/hunyuan21/hunyuan21_worker.py",
    "app/engines/hunyuan21/ENGINE-MANIFEST.json",
    "app/engines/hunyuan21/LICENSE-HUNYUAN3D-2.1.txt",
    "app/engines/hunyuan21/NOTICE-HUNYUAN3D-2.1.txt",
    "app/engines/hunyuan21/NOTICE.txt",
    "app/engines/hunyuan21/MODIFICATIONS.txt",
    "app/engines/hunyuan21/models/RealESRGAN_x4plus.pth",
    "licenses/DINOv2-Apache-2.0.txt",
    "app/engines/hunyuan21/vendor/Hunyuan3D-2.1/hy3dshape/hy3dshape/pipelines.py",
    "app/engines/hunyuan21/vendor/Hunyuan3D-2.1/hy3dpaint/textureGenPipeline.py",
    "app/engines/hunyuan21/vendor/Hunyuan3D-2.1/hy3dpaint/utils/torchvision_fix.py",
    "app/engines/hunyuan21/vendor/Hunyuan3D-2.1/hy3dpaint/hunyuanpaintpbr/pipeline.py"
)
foreach ($Relative in $RequiredFiles) {
    $NativeRelative = $Relative.Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path (Join-Path $Stage $NativeRelative) -PathType Leaf)) {
        throw "Staged Hunyuan3D-2.1 update is missing $Relative"
    }
}

$TextExtensions = @(".py", ".json", ".txt", ".md", ".yaml", ".yml", ".manifest")
$UnresolvedMarkerDetails = @(Get-ChildItem $Stage -Recurse -File | Where-Object {
    $_.Extension.ToLowerInvariant() -in $TextExtensions
} | ForEach-Object {
    $MarkerMatches = @([regex]::Matches(
        (Get-Content $_.FullName -Raw), '@@[A-Z][A-Z0-9_]*@@') |
        ForEach-Object { $_.Value } |
        Sort-Object -Unique)
    if ($MarkerMatches.Count -ne 0) {
        "$($_.FullName): $($MarkerMatches -join ', ')"
    }
})
if ($UnresolvedMarkerDetails.Count -ne 0) {
    throw "Staged update contains unresolved marker(s): $($UnresolvedMarkerDetails -join '; ')"
}

$Entries = @(Get-ChildItem $Stage -Recurse -File | Sort-Object FullName | ForEach-Object {
    $Relative = $_.FullName.Substring($Stage.Length).TrimStart(
        [IO.Path]::DirectorySeparatorChar).Replace(
        [IO.Path]::DirectorySeparatorChar, [char]'/')
    [ordered]@{
        path = $Relative
        bytes = [Int64]$_.Length
        sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
})
$UpdateManifest = [ordered]@{
    schema_version = 1
    product = "Mujassam AI Hunyuan3D-2.1 Shape + PBR engine"
    source_commit = $env:GITHUB_SHA
    upstream_commit = $SourceCommit
    usage_scope = $UsageScope
    # This build manifest records provenance; it never grants distribution rights.
    distribution_authorized = $false
    provider_legal_name = $ProviderLegalName
    archive = $ArchiveName
    files = $Entries
}
$ManifestJson = $UpdateManifest | ConvertTo-Json -Depth 6
[IO.File]::WriteAllText(
    (Join-Path $Stage "update-manifest.json"),
    $ManifestJson,
    [Text.UTF8Encoding]::new($false))
Compress-Archive -Path (Join-Path $Stage "*") `
    -DestinationPath $Archive -CompressionLevel Optimal

Expand-Archive -Path $Archive -DestinationPath $VerifyRoot
$ExtractedManifest = Get-Content `
    (Join-Path $VerifyRoot "update-manifest.json") -Raw | ConvertFrom-Json
foreach ($Entry in $ExtractedManifest.files) {
    if ([string]$Entry.path -match '(^|/)\.\.(/|$)') {
        throw "Unsafe path in update manifest: $($Entry.path)"
    }
    $Native = ([string]$Entry.path).Replace(
        [char]'/', [IO.Path]::DirectorySeparatorChar)
    $File = Get-Item (Join-Path $VerifyRoot $Native)
    $Digest = (Get-FileHash $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($File.Length -ne [Int64]$Entry.bytes -or
        $Digest -cne [string]$Entry.sha256) {
        throw "ZIP verification failed for $($Entry.path)"
    }
}
$ArchiveDigest = (Get-FileHash $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Verified Hunyuan3D-2.1 update: $Archive"
Write-Host "SHA-256: $ArchiveDigest"
"MUJASSAM_HUNYUAN21_ARCHIVE=$Archive" | Out-File `
    $env:GITHUB_ENV -Append -Encoding utf8
"MUJASSAM_HUNYUAN21_STAGE=$Stage" | Out-File `
    $env:GITHUB_ENV -Append -Encoding utf8
"MUJASSAM_HUNYUAN21_SHA256=$ArchiveDigest" | Out-File `
    $env:GITHUB_ENV -Append -Encoding utf8
