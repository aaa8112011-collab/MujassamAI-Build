[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$PSNativeCommandUseErrorActionPreference = $true

$SourceCommit = "f8db63096c8282cb27354314d896feba5ba6ff8a"
$CudaInstallerMd5 = "8901c95cd9e20b8fc73fc76e96065d03"
$TorchVersion = "2.5.1+cu124"
$TorchVisionVersion = "0.20.1+cu124"
$CudaArchitectures = "7.5;8.0;8.6;8.9+PTX"
$ArchiveName = if ([string]::IsNullOrWhiteSpace($env:ARCHIVE_NAME)) {
    "MujassamAI-Hunyuan-Update-v1.zip"
} else {
    $env:ARCHIVE_NAME
}

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$SourceRoot = Join-Path $env:RUNNER_TEMP "hy3d2-src"
$BuildVenv = Join-Path $env:RUNNER_TEMP "hy3d2-venv"
$Wheelhouse = Join-Path $env:RUNNER_TEMP "hy3d2-wheels"
$BuildRoot = Join-Path $env:RUNNER_TEMP "hy3d2-update"
$Stage = Join-Path $BuildRoot "stage"
$VerifyRoot = Join-Path $BuildRoot "verify"
$GuiTestRoot = Join-Path $BuildRoot "gui-test"
$ReleaseRoot = Join-Path $RepositoryRoot "release"
$Archive = Join-Path $ReleaseRoot $ArchiveName
$Requirements = Join-Path $RepositoryRoot "build\hunyuan2.requirements.lock.txt"
$UnetPatchScript = Join-Path $RepositoryRoot "build\patch_hunyuan_unet.py"

foreach ($Path in @($SourceRoot, $BuildVenv, $Wheelhouse, $BuildRoot, $ReleaseRoot)) {
    if (Test-Path $Path) {
        throw "Refusing to overwrite an existing build path: $Path"
    }
}
foreach ($Path in @(
    (Join-Path $RepositoryRoot "app\worker.py"),
    (Join-Path $RepositoryRoot "app\engines\hunyuan2\hunyuan2_worker.py"),
    (Join-Path $RepositoryRoot "app\engines\hunyuan2\ENGINE-MANIFEST.json"),
    (Join-Path $RepositoryRoot "app\engines\hunyuan2\LICENSE-HUNYUAN3D-2.txt"),
    (Join-Path $RepositoryRoot "app\engines\hunyuan2\NOTICE-HUNYUAN3D-2.txt"),
    $UnetPatchScript,
    $Requirements
)) {
    if (-not (Test-Path $Path -PathType Leaf)) {
        throw "Missing required packaging file: $Path"
    }
}

Write-Host "Fetching official Hunyuan3D-2 source at $SourceCommit"
New-Item -ItemType Directory -Path $SourceRoot | Out-Null
git -C $SourceRoot init --quiet
git -C $SourceRoot remote add origin https://github.com/Tencent-Hunyuan/Hunyuan3D-2.git
git -C $SourceRoot fetch --quiet --depth 1 origin $SourceCommit
git -C $SourceRoot checkout --quiet --detach FETCH_HEAD
$ResolvedCommit = (git -C $SourceRoot rev-parse HEAD).Trim()
if ($ResolvedCommit -cne $SourceCommit) {
    throw "Hunyuan3D source mismatch: expected $SourceCommit, got $ResolvedCommit"
}

$OfficialLicense = Join-Path $SourceRoot "LICENSE"
$OfficialNotice = Join-Path $SourceRoot "NOTICE"
$PackagedLicense = Join-Path $RepositoryRoot "app\engines\hunyuan2\LICENSE-HUNYUAN3D-2.txt"
$PackagedNotice = Join-Path $RepositoryRoot "app\engines\hunyuan2\NOTICE-HUNYUAN3D-2.txt"
if ((Get-FileHash $OfficialLicense -Algorithm SHA256).Hash -cne
    (Get-FileHash $PackagedLicense -Algorithm SHA256).Hash) {
    throw "Packaged Hunyuan3D LICENSE does not match the pinned official source"
}
if ((Get-FileHash $OfficialNotice -Algorithm SHA256).Hash -cne
    (Get-FileHash $PackagedNotice -Algorithm SHA256).Hash) {
    throw "Packaged Hunyuan3D NOTICE does not match the pinned official source"
}

$CudaInstallerName = "cuda_12.4.1_windows_network.exe"
$CudaInstaller = Join-Path $env:RUNNER_TEMP $CudaInstallerName
$CudaInstallerUrl = (
    "https://developer.download.nvidia.com/compute/cuda/12.4.1/" +
    "network_installers/$CudaInstallerName"
)
Write-Host "Installing minimal CUDA 12.4 build toolkit"
Invoke-WebRequest $CudaInstallerUrl -OutFile $CudaInstaller
$ActualCudaMd5 = (Get-FileHash $CudaInstaller -Algorithm MD5).Hash.ToLowerInvariant()
if ($ActualCudaMd5 -cne $CudaInstallerMd5) {
    throw "CUDA installer checksum mismatch: $ActualCudaMd5"
}
$CudaProcess = Start-Process -FilePath $CudaInstaller -ArgumentList @(
    "-s",
    "-n",
    "nvcc_12.4",
    "cudart_12.4",
    "thrust_12.4",
    "cublas_12.4",
    "cublas_dev_12.4",
    "cusolver_12.4",
    "cusolver_dev_12.4",
    "cusparse_12.4",
    "cusparse_dev_12.4",
    "nvjitlink_12.4",
    "cuobjdump_12.4"
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
foreach ($Header in @(
    "cuda_runtime_api.h",
    "cublas_v2.h",
    "cublasLt.h",
    "cusolverDn.h",
    "cusparse.h"
)) {
    if (-not (Test-Path (Join-Path $CudaHome "include\$Header") -PathType Leaf)) {
        throw "Required CUDA header is missing: $Header"
    }
}
$env:CUDA_HOME = $CudaHome
$env:CUDA_PATH = $CudaHome
$env:PATH = "$CudaHome\bin;$env:PATH"

$ProgramFilesX86 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
$VsWhere = Join-Path $ProgramFilesX86 "Microsoft Visual Studio\Installer\vswhere.exe"
$VsInstall = (& $VsWhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath | Select-Object -First 1)
if (-not $VsInstall) {
    throw "Visual Studio 2022 C++ tools were not found"
}
Import-Module "$VsInstall\Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Enter-VsDevShell -VsInstallPath $VsInstall -SkipAutomaticLocation -DevCmdArguments "-arch=x64 -host_arch=x64 -vcvars_ver=14.29"
$ClPath = (Get-Command cl.exe).Source
if ($ClPath -notmatch "\\14\.29\.") {
    throw "MSVC v142/14.29 was requested but cl.exe resolved to $ClPath"
}
Write-Host "Using compiler $ClPath"
& $Nvcc --version

Write-Host "Creating pinned Python build environment"
python -m venv $BuildVenv
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"
& $BuildPython -m pip install --no-cache-dir "setuptools==69.5.1" "wheel==0.45.1" "ninja==1.11.1.3" "pybind11==2.13.6"
& $BuildPython -m pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cu124 "torch==$TorchVersion" "torchvision==$TorchVisionVersion"
& $BuildPython -m pip check

$env:USE_CUDA = "1"
$env:USE_NATIVE_ARCH = "0"
$env:TORCH_CUDA_ARCH_LIST = $CudaArchitectures
$env:MAX_JOBS = "2"
$env:DISTUTILS_USE_SDK = "1"
$env:MSSdk = "1"
New-Item -ItemType Directory -Path $Wheelhouse | Out-Null

Write-Host "Building official Hunyuan rasterizer extensions"
$RasterizerSource = Join-Path $SourceRoot "hy3dgen\texgen\custom_rasterizer"
$MeshProcessorSource = Join-Path $SourceRoot "hy3dgen\texgen\differentiable_renderer"
& $BuildPython -m pip wheel --no-cache-dir --no-deps --no-build-isolation --wheel-dir $Wheelhouse $RasterizerSource
& $BuildPython -m pip wheel --no-cache-dir --no-deps --no-build-isolation --wheel-dir $Wheelhouse $MeshProcessorSource
$RasterizerWheels = @(Get-ChildItem $Wheelhouse -Filter "custom_rasterizer-*.whl")
$MeshProcessorWheels = @(Get-ChildItem $Wheelhouse -Filter "mesh_processor-*.whl")
if ($RasterizerWheels.Count -ne 1 -or $MeshProcessorWheels.Count -ne 1) {
    throw "Expected one custom_rasterizer wheel and one mesh_processor wheel"
}

Write-Host "Staging the root-layout Hunyuan update"
$StageApp = New-Item -ItemType Directory -Path (Join-Path $Stage "app") -Force
$StageQuality = New-Item -ItemType Directory -Path (Join-Path $StageApp "quality") -Force
$StageEngine = New-Item -ItemType Directory -Path (Join-Path $StageApp "engines\hunyuan2") -Force
$StageVendorRoot = New-Item -ItemType Directory -Path (Join-Path $StageEngine "vendor\Hunyuan3D-2") -Force
$PythonPackages = New-Item -ItemType Directory -Path (Join-Path $StageEngine "python_packages") -Force
New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null

Copy-Item (Join-Path $RepositoryRoot "app\worker.py") $StageApp.FullName -Force
Copy-Item (Join-Path $RepositoryRoot "app\quality\*") $StageQuality.FullName -Recurse -Force
foreach ($Name in @(
    "hunyuan2_worker.py",
    "ENGINE-MANIFEST.json",
    "LICENSE-HUNYUAN3D-2.txt",
    "NOTICE-HUNYUAN3D-2.txt",
    "MODIFICATIONS.txt"
)) {
    Copy-Item (Join-Path $RepositoryRoot "app\engines\hunyuan2\$Name") $StageEngine.FullName -Force
}
Copy-Item (Join-Path $SourceRoot "hy3dgen") $StageVendorRoot.FullName -Recurse -Force
Get-ChildItem $StageVendorRoot.FullName -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
$VendorUnetModule = Join-Path $StageVendorRoot.FullName "hy3dgen\texgen\hunyuanpaint\unet\modules.py"
& $BuildPython -I -X utf8 $UnetPatchScript $VendorUnetModule

& $BuildPython -m pip install --no-cache-dir --target $PythonPackages.FullName -r $Requirements
& $BuildPython -m pip install --no-cache-dir --no-deps --target $PythonPackages.FullName $RasterizerWheels[0].FullName $MeshProcessorWheels[0].FullName
$MeshProcessorModules = @(Get-ChildItem $PythonPackages.FullName -File -Filter "mesh_processor*.pyd")
if ($MeshProcessorModules.Count -ne 1) {
    throw "Expected one installed mesh_processor extension, got $($MeshProcessorModules.Count)"
}
$MeshProcessorPackage = Join-Path $StageVendorRoot.FullName "hy3dgen\texgen\differentiable_renderer"
Move-Item $MeshProcessorModules[0].FullName (Join-Path $MeshProcessorPackage $MeshProcessorModules[0].Name) -Force
$BundledTorch = @(Get-ChildItem $PythonPackages.FullName -Force | Where-Object {
    $_.Name -match '^torch($|[-_.])' -or $_.Name -match '^torchvision($|[-_.])'
})
if ($BundledTorch.Count -ne 0) {
    throw "The add-on unexpectedly contains a second PyTorch installation: $($BundledTorch.Name -join ', ')"
}

Write-Host "Compiling current MujassamAI.exe"
$Csc = Join-Path $VsInstall "MSBuild\Current\Bin\Roslyn\csc.exe"
$ReferenceRoot = "C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
if (-not (Test-Path $Csc -PathType Leaf) -or -not (Test-Path $ReferenceRoot -PathType Container)) {
    throw ".NET Framework 4.8 compiler/reference assemblies were not found"
}
$References = @("System.dll", "System.Core.dll", "System.Drawing.dll", "System.Web.Extensions.dll", "System.Windows.Forms.dll") | ForEach-Object {
    $ReferencePath = Join-Path $ReferenceRoot $_
    if (-not (Test-Path $ReferencePath -PathType Leaf)) {
        throw "Missing .NET reference: $ReferencePath"
    }
    "/reference:$ReferencePath"
}
$ManifestPath = Join-Path $BuildRoot "MujassamAI.manifest"
@'
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
'@ | Set-Content $ManifestPath -Encoding utf8
$Executable = Join-Path $Stage "MujassamAI.exe"
$CscArguments = @(
    "/nologo",
    "/noconfig",
    "/target:winexe",
    "/platform:x64",
    "/optimize+",
    "/debug-",
    "/langversion:latest",
    "/win32manifest:$ManifestPath",
    "/out:$Executable"
) + $References + @(
    (Join-Path $RepositoryRoot "app\Program.cs"),
    (Join-Path $RepositoryRoot "app\MainForm.cs")
)
& $Csc @CscArguments
if (-not (Test-Path $Executable -PathType Leaf)) {
    throw "MujassamAI.exe was not produced"
}

Write-Host "Running model-free engine and ABI tests"
& $BuildPython -m py_compile (Join-Path $StageApp "worker.py") (Join-Path $StageEngine "hunyuan2_worker.py")
& $BuildPython -I -X utf8 (Join-Path $StageApp "worker.py") --self-test
& $BuildPython -I -X utf8 (Join-Path $StageEngine "hunyuan2_worker.py") --self-test
$env:MJ_HY_ENGINE = $StageEngine.FullName
$ImportSmokeTest = @'
import os
import sys
import inspect
root = os.environ["MJ_HY_ENGINE"]
sys.path.insert(0, os.path.join(root, "python_packages"))
sys.path.insert(0, os.path.join(root, "vendor", "Hunyuan3D-2"))
import custom_rasterizer
from hy3dgen.texgen.differentiable_renderer import mesh_processor
from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
from hy3dgen.texgen import Hunyuan3DPaintPipeline
from hy3dgen.texgen.hunyuanpaint.unet.modules import UNet2p5DConditionModel
loader_source = inspect.getsource(UNet2p5DConditionModel.from_pretrained)
assert "diffusion_pytorch_model.safetensors" in loader_source
assert "init_empty_weights" in loader_source
assert "weight_keys != model_keys" in loader_source
print("Hunyuan3D source and compiled extensions: OK")
'@
& $BuildPython -I -X utf8 -c $ImportSmokeTest

New-Item -ItemType Directory -Path (Join-Path $GuiTestRoot "app\quality"), (Join-Path $GuiTestRoot "rt"), (Join-Path $GuiTestRoot "models\realesrgan") -Force | Out-Null
Copy-Item $Executable (Join-Path $GuiTestRoot "MujassamAI.exe")
Copy-Item (Join-Path $StageApp "worker.py") (Join-Path $GuiTestRoot "app\worker.py")
Copy-Item (Join-Path $StageQuality "*") (Join-Path $GuiTestRoot "app\quality") -Recurse
New-Item -ItemType File -Path (Join-Path $GuiTestRoot "rt\python.exe") -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $GuiTestRoot "models\realesrgan\RealESRGAN_x2plus.pth") -Force | Out-Null
$GuiReport = Join-Path $GuiTestRoot "self-test.txt"
$GuiProcess = Start-Process -FilePath (Join-Path $GuiTestRoot "MujassamAI.exe") -ArgumentList @("--self-test", $GuiReport) -Wait -PassThru
if ($GuiProcess.ExitCode -ne 0 -or -not (Test-Path $GuiReport -PathType Leaf)) {
    throw "MujassamAI.exe self-test failed with exit code $($GuiProcess.ExitCode)"
}
$GuiText = Get-Content $GuiReport -Raw
if (-not $GuiText.Contains("PortableLayout=OK") -or
    -not $GuiText.Contains("JobSchemaVersion=3") -or
    -not $GuiText.Contains("JobSchema=OK") -or
    -not $GuiText.Contains("Is64BitProcess=True")) {
    Write-Host $GuiText
    throw "MujassamAI.exe self-test reported an invalid v3 layout/schema"
}

$RequiredFiles = @(
    "MujassamAI.exe",
    "app/worker.py",
    "app/quality/realesrgan_x2.py",
    "app/engines/hunyuan2/hunyuan2_worker.py",
    "app/engines/hunyuan2/ENGINE-MANIFEST.json",
    "app/engines/hunyuan2/LICENSE-HUNYUAN3D-2.txt",
    "app/engines/hunyuan2/NOTICE-HUNYUAN3D-2.txt",
    "app/engines/hunyuan2/MODIFICATIONS.txt",
    "app/engines/hunyuan2/vendor/Hunyuan3D-2/hy3dgen/shapegen/pipelines.py",
    "app/engines/hunyuan2/vendor/Hunyuan3D-2/hy3dgen/texgen/pipelines.py",
    "app/engines/hunyuan2/vendor/Hunyuan3D-2/hy3dgen/texgen/hunyuanpaint/unet/modules.py"
)
foreach ($Relative in $RequiredFiles) {
    $NativeRelative = $Relative.Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    if (-not (Test-Path (Join-Path $Stage $NativeRelative) -PathType Leaf)) {
        throw "Staged update is missing $Relative"
    }
}

$Entries = @(Get-ChildItem $Stage -Recurse -File | Sort-Object FullName | ForEach-Object {
    $Relative = $_.FullName.Substring($Stage.Length).TrimStart([IO.Path]::DirectorySeparatorChar)
    $Relative = $Relative.Replace([IO.Path]::DirectorySeparatorChar, [char]'/')
    [ordered]@{
        path = $Relative
        bytes = [Int64]$_.Length
        sha256 = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
})
$UpdateManifest = [ordered]@{
    schema_version = 1
    product = "Mujassam AI Hunyuan3D low-VRAM engine"
    source_commit = $env:GITHUB_SHA
    upstream_commit = $SourceCommit
    archive = $ArchiveName
    files = $Entries
}
$UpdateManifest | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Stage "update-manifest.json") -Encoding utf8
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Archive -CompressionLevel Optimal
if (-not (Test-Path $Archive -PathType Leaf)) {
    throw "Hunyuan update archive was not created"
}

Expand-Archive -Path $Archive -DestinationPath $VerifyRoot
$ExtractedManifestPath = Join-Path $VerifyRoot "update-manifest.json"
$ExtractedManifest = Get-Content $ExtractedManifestPath -Raw | ConvertFrom-Json
foreach ($Entry in $ExtractedManifest.files) {
    if ([string]$Entry.path -match '(^|/)\.\.(/|$)') {
        throw "Unsafe path in update manifest: $($Entry.path)"
    }
    $NativeRelative = ([string]$Entry.path).Replace([char]'/', [IO.Path]::DirectorySeparatorChar)
    $File = Get-Item (Join-Path $VerifyRoot $NativeRelative)
    $Digest = (Get-FileHash $File.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($File.Length -ne [Int64]$Entry.bytes -or $Digest -cne [string]$Entry.sha256) {
        throw "ZIP verification failed for $($Entry.path)"
    }
}
$ArchiveDigest = (Get-FileHash $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
Write-Host "Verified Hunyuan update: $Archive"
Write-Host "SHA-256: $ArchiveDigest"
"MUJASSAM_HUNYUAN_ARCHIVE=$Archive" | Out-File $env:GITHUB_ENV -Append -Encoding utf8
"MUJASSAM_HUNYUAN_STAGE=$Stage" | Out-File $env:GITHUB_ENV -Append -Encoding utf8
"MUJASSAM_HUNYUAN_SHA256=$ArchiveDigest" | Out-File $env:GITHUB_ENV -Append -Encoding utf8
