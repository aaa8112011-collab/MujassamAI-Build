[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$PSNativeCommandUseErrorActionPreference = $true

$Spar3dCommit = "fdc311b16809e6a8adc2f5a3407ebb3db1a95bd1"
$ModelRevision = "5699918cb34f55cd7d828493d2725f3038313761"
$PythonVersion = "3.11.9"
$TorchVersion = "2.5.1+cu124"
$TorchVisionVersion = "0.20.1+cu124"
$CudaVersion = "12.4"
$CudaInstallerMd5 = "8901c95cd9e20b8fc73fc76e96065d03"
$ClipCommit = "dcba3cb2e2827b402d2701e7e1c7d9fed8a20ef1"
$AlphaClipCommit = "f1ec829fb2cf14d470d841d66114bf883a838b0d"

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$AppSource = Join-Path $RepositoryRoot "app"
$InstallerSource = Join-Path $RepositoryRoot "installer\Setup.cs"
$DistRoot = Join-Path $RepositoryRoot "dist\MujassamAI-Portable"
$ReleaseRoot = Join-Path $RepositoryRoot "release"
$SourceRoot = Join-Path $env:RUNNER_TEMP "mujassam-spar3d-source"
$BuildVenv = Join-Path $env:RUNNER_TEMP "mujassam-build-venv"
$Wheelhouse = Join-Path $env:RUNNER_TEMP "mujassam-wheelhouse"
$RuntimeArchive = Join-Path $env:RUNNER_TEMP "runtime.zip"
$WeightsRecordPath = Join-Path $env:RUNNER_TEMP "mujassam-weights-record.json"
$ReleasePartSize = 1890000000
$ReleaseTag = if ([string]::IsNullOrWhiteSpace($env:RELEASE_TAG)) {
    "portable-local"
} else {
    $env:RELEASE_TAG
}

function Assert-MissingPath {
    param([Parameter(Mandatory)][string] $Path)
    if (Test-Path $Path) {
        throw "Refusing to overwrite an existing build path: $Path"
    }
}

function Get-DirectorySizeBytes {
    param([Parameter(Mandatory)][string] $Path)
    return (Get-ChildItem $Path -Recurse -File | Measure-Object Length -Sum).Sum
}

function Split-ReleaseFile {
    param(
        [Parameter(Mandatory)][string] $Source,
        [Parameter(Mandatory)][string] $OutputDirectory,
        [Parameter(Mandatory)][string] $Prefix,
        [Parameter(Mandatory)][long] $ChunkSize
    )

    if (-not (Test-Path $Source -PathType Leaf)) {
        throw "Cannot split missing file: $Source"
    }
    if ($ChunkSize -lt 1MB) {
        throw "Release part size must be at least 1 MiB"
    }

    $SourceItem = Get-Item $Source
    $WholeHash = (Get-FileHash $Source -Algorithm SHA256).Hash.ToLowerInvariant()
    $InputStream = [IO.File]::OpenRead($Source)
    $Buffer = [byte[]]::new(8MB)
    $PartNumber = 0
    $PartRecords = [Collections.Generic.List[object]]::new()
    $CurrentPartPath = $null

    try {
        while ($InputStream.Position -lt $InputStream.Length) {
            $PartNumber += 1
            $PartName = "{0}.part{1:D3}" -f $Prefix, $PartNumber
            $PartPath = Join-Path $OutputDirectory $PartName
            $CurrentPartPath = $PartPath
            if (Test-Path $PartPath) {
                throw "Refusing to overwrite release part: $PartPath"
            }

            $OutputStream = [IO.File]::Open(
                $PartPath,
                [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write,
                [IO.FileShare]::None
            )
            try {
                [long] $PartBytes = 0
                while ($PartBytes -lt $ChunkSize) {
                    $Requested = [int][Math]::Min(
                        [long]$Buffer.Length,
                        $ChunkSize - $PartBytes
                    )
                    $Read = $InputStream.Read($Buffer, 0, $Requested)
                    if ($Read -eq 0) {
                        break
                    }
                    $OutputStream.Write($Buffer, 0, $Read)
                    $PartBytes += $Read
                }
            } finally {
                $OutputStream.Dispose()
            }

            $PartHash = (Get-FileHash $PartPath -Algorithm SHA256).Hash.ToLowerInvariant()
            $PartRecords.Add([ordered]@{
                name = $PartName
                bytes = $PartBytes
                sha256 = $PartHash
            })
            $CurrentPartPath = $null
        }
    } catch {
        if ($CurrentPartPath -and (Test-Path $CurrentPartPath)) {
            Remove-Item $CurrentPartPath -Force
        }
        foreach ($record in $PartRecords) {
            $CreatedPart = Join-Path $OutputDirectory $record.name
            if (Test-Path $CreatedPart) {
                Remove-Item $CreatedPart -Force
            }
        }
        throw
    } finally {
        $InputStream.Dispose()
    }

    return [ordered]@{
        file_name = $SourceItem.Name
        bytes = $SourceItem.Length
        sha256 = $WholeHash
        parts = @($PartRecords)
    }
}

function Assert-ReleaseParts {
    param([Parameter(Mandatory)] $Record)

    $PartPaths = [Collections.Generic.List[string]]::new()
    [long] $PartTotal = 0
    foreach ($part in $Record.parts) {
        $PartPath = Join-Path $ReleaseRoot $part.name
        $PartItem = Get-Item $PartPath
        if ($PartItem.Length -ge 1900000000) {
            throw "Release asset is not below 1.9 GB: $($part.name)"
        }
        $ActualPartHash = (
            Get-FileHash $PartPath -Algorithm SHA256
        ).Hash.ToLowerInvariant()
        if ($PartItem.Length -ne [long]$part.bytes -or
            $ActualPartHash -ne [string]$part.sha256) {
            throw "Release part verification failed: $($part.name)"
        }
        $PartTotal += $PartItem.Length
        $PartPaths.Add($PartItem.FullName)
    }
    if ($PartTotal -ne [long]$Record.bytes) {
        throw "Release part byte total does not match $($Record.file_name)"
    }
    return $PartPaths.ToArray()
}

function Ensure-DraftRelease {
    $PreviousNativePreference = $PSNativeCommandUseErrorActionPreference
    try {
        $PSNativeCommandUseErrorActionPreference = $false
        gh release view $ReleaseTag --repo $env:GITHUB_REPOSITORY *> $null
        $ReleaseExists = $LASTEXITCODE -eq 0
    } finally {
        $PSNativeCommandUseErrorActionPreference = $PreviousNativePreference
    }

    if (-not $ReleaseExists) {
        $ReleaseNotes = (
            "Download Setup.exe. It verifies and assembles the split " +
            "runtime and pinned SPAR3D model."
        )
        gh release create $ReleaseTag `
            --repo $env:GITHUB_REPOSITORY `
            --target $env:GITHUB_SHA `
            --title "Mujassam AI Portable $ReleaseTag" `
            --notes $ReleaseNotes `
            --draft
    }
}

function Publish-ReleaseAssets {
    param([Parameter(Mandatory)][string[]] $Paths)
    if ($Paths.Count -eq 0) {
        throw "No release assets were supplied for upload"
    }
    gh release upload $ReleaseTag @Paths `
        --repo $env:GITHUB_REPOSITORY `
        --clobber
}

if ([string]::IsNullOrWhiteSpace($env:HF_TOKEN)) {
    throw "HF_TOKEN is required"
}
if ([string]::IsNullOrWhiteSpace($env:GH_TOKEN)) {
    throw "GH_TOKEN is required"
}

foreach ($requiredAppFile in @("Program.cs", "MainForm.cs", "worker.py")) {
    $requiredPath = Join-Path $AppSource $requiredAppFile
    if (-not (Test-Path $requiredPath -PathType Leaf)) {
        throw "Missing application source file: $requiredPath"
    }
}
if (-not (Test-Path $InstallerSource -PathType Leaf)) {
    throw "Missing installer source file: $InstallerSource"
}
if ($env:GITHUB_REPOSITORY -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
    throw "GITHUB_REPOSITORY must be an owner/repository name"
}
if ($env:GITHUB_SHA -notmatch "^[A-Fa-f0-9]{40}$") {
    throw "GITHUB_SHA must be a full commit SHA"
}
if ($ReleaseTag -notmatch "^[A-Za-z0-9._-]+$") {
    throw "RELEASE_TAG contains unsupported characters"
}

foreach ($temporaryPath in @(
    $DistRoot,
    $ReleaseRoot,
    $SourceRoot,
    $BuildVenv,
    $Wheelhouse,
    $RuntimeArchive,
    $WeightsRecordPath
)) {
    Assert-MissingPath $temporaryPath
}

Write-Host "Fetching pinned SPAR3D source $Spar3dCommit"
New-Item -ItemType Directory -Path $SourceRoot | Out-Null
git -C $SourceRoot init --quiet
git -C $SourceRoot remote add origin https://github.com/Stability-AI/stable-point-aware-3d.git
git -C $SourceRoot fetch --quiet --depth 1 origin $Spar3dCommit
git -C $SourceRoot checkout --quiet --detach FETCH_HEAD

$ResolvedCommit = (git -C $SourceRoot rev-parse HEAD).Trim()
if ($ResolvedCommit -ne $Spar3dCommit) {
    throw "SPAR3D commit mismatch: expected $Spar3dCommit, got $ResolvedCommit"
}

python (Join-Path $PSScriptRoot "patch_windows.py") $SourceRoot

# Install only the CUDA build components needed by torch.utils.cpp_extension.
# No display driver is installed on the hosted runner.
$CudaInstallerName = "cuda_12.4.1_windows_network.exe"
$CudaInstaller = Join-Path $env:RUNNER_TEMP $CudaInstallerName
$CudaInstallerUrl = (
    "https://developer.download.nvidia.com/compute/cuda/12.4.1/" +
    "network_installers/$CudaInstallerName"
)

Write-Host "Installing the minimal CUDA 12.4 build toolkit"
Invoke-WebRequest $CudaInstallerUrl -OutFile $CudaInstaller
$ActualCudaMd5 = (Get-FileHash $CudaInstaller -Algorithm MD5).Hash.ToLowerInvariant()
if ($ActualCudaMd5 -ne $CudaInstallerMd5) {
    throw "CUDA installer checksum mismatch: $ActualCudaMd5"
}

$CudaProcess = Start-Process `
    -FilePath $CudaInstaller `
    -ArgumentList @(
        "-s",
        "-n",
        "nvcc_12.4",
        "cudart_12.4",
        "thrust_12.4",
        "cuobjdump_12.4"
    ) `
    -Wait `
    -PassThru

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

# CUDA 12.4 supports MSVC 192x/193x. Force the v142 toolset bundled with the
# windows-2022 image instead of whichever newer VS 2022 compiler is default.
$VsWhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$VsInstall = (& $VsWhere `
    -latest `
    -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath | Select-Object -First 1)

if (-not $VsInstall) {
    throw "Visual Studio 2022 C++ tools were not found"
}

Import-Module "$VsInstall\Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Enter-VsDevShell `
    -VsInstallPath $VsInstall `
    -SkipAutomaticLocation `
    -DevCmdArguments "-arch=x64 -host_arch=x64 -vcvars_ver=14.29"

$ClPath = (Get-Command cl.exe).Source
if ($ClPath -notmatch "\\14\.29\.") {
    throw "MSVC v142/14.29 was requested but cl.exe resolved to $ClPath"
}
Write-Host "Using compiler $ClPath"
& $Nvcc --version

Write-Host "Creating the Python build/runtime environment"
python -m venv $BuildVenv
$BuildPython = Join-Path $BuildVenv "Scripts\python.exe"

& $BuildPython -m pip install `
    --no-cache-dir `
    "setuptools==69.5.1" `
    wheel `
    ninja

& $BuildPython -m pip install `
    --no-cache-dir `
    --extra-index-url https://download.pytorch.org/whl/cu124 `
    "torch==$TorchVersion" `
    "torchvision==$TorchVisionVersion"

# Remove the two local extension paths from upstream requirements; their
# patched wheels are built and installed explicitly below.
$PortableRequirements = Join-Path $env:RUNNER_TEMP "mujassam-requirements.txt"
$UpstreamRequirements = @(
    Get-Content (Join-Path $SourceRoot "requirements.txt")
)
$ClipLines = @($UpstreamRequirements | Where-Object { $_ -match "openai/CLIP\.git" })
$AlphaClipLines = @(
    $UpstreamRequirements | Where-Object { $_ -match "SunzeY/AlphaCLIP\.git" }
)
if ($ClipLines.Count -ne 1 -or $AlphaClipLines.Count -ne 1) {
    throw "Expected exactly one OpenAI CLIP and one AlphaCLIP requirement"
}

$UpstreamRequirements |
    ForEach-Object {
        if ($_ -match "openai/CLIP\.git") {
            "git+https://github.com/openai/CLIP.git@$ClipCommit"
        } elseif ($_ -match "SunzeY/AlphaCLIP\.git") {
            "git+https://github.com/SunzeY/AlphaCLIP.git@$AlphaClipCommit"
        } else {
            $_
        }
    } |
    Where-Object { $_ -notmatch "^\s*\./(texture_baker|uv_unwrapper)/?\s*$" } |
    Set-Content $PortableRequirements -Encoding utf8

& $BuildPython -m pip install `
    --no-cache-dir `
    --no-build-isolation `
    -r $PortableRequirements

# Include both upstream remesh implementations used by optional UI modes.
& $BuildPython -m pip install `
    --no-cache-dir `
    -r (Join-Path $SourceRoot "requirements-remesh.txt")

$env:USE_CUDA = "1"
$env:USE_NATIVE_ARCH = "0"
$env:TORCH_CUDA_ARCH_LIST = "8.9"
$env:MAX_JOBS = "2"
$env:DISTUTILS_USE_SDK = "1"
$env:MSSdk = "1"

New-Item -ItemType Directory -Path $Wheelhouse | Out-Null

& $BuildPython -m pip wheel `
    --no-cache-dir `
    --no-deps `
    --no-build-isolation `
    --wheel-dir $Wheelhouse `
    (Join-Path $SourceRoot "texture_baker")

& $BuildPython -m pip wheel `
    --no-cache-dir `
    --no-deps `
    --no-build-isolation `
    --wheel-dir $Wheelhouse `
    (Join-Path $SourceRoot "uv_unwrapper")

$TextureWheel = @(Get-ChildItem $Wheelhouse -Filter "texture_baker-*.whl")
$UvWheel = @(Get-ChildItem $Wheelhouse -Filter "uv_unwrapper-*.whl")
if ($TextureWheel.Count -ne 1 -or $UvWheel.Count -ne 1) {
    throw "Expected exactly one texture_baker wheel and one uv_unwrapper wheel"
}

& $BuildPython -m pip install `
    --no-cache-dir `
    --no-deps `
    $TextureWheel[0].FullName `
    $UvWheel[0].FullName

& $BuildPython -m pip check

# Stage the complete application-local interpreter. Moving site-packages from
# the same CPython ABI avoids a second full PyTorch installation on the runner.
Write-Host "Staging the application-local Python runtime"
New-Item -ItemType Directory -Path $DistRoot | Out-Null
$RuntimeRoot = New-Item -ItemType Directory -Path (Join-Path $DistRoot "rt")
$PortableApp = New-Item -ItemType Directory -Path (Join-Path $DistRoot "app")
$PortableVendor = New-Item -ItemType Directory -Path (
    Join-Path $PortableApp.FullName "vendor\stable-point-aware-3d"
)
$ModelRoot = New-Item -ItemType Directory -Path (Join-Path $DistRoot "models\spar3d")
$BackgroundRoot = New-Item -ItemType Directory -Path (
    Join-Path $DistRoot "models\transparent-background"
)
$LicenseRoot = New-Item -ItemType Directory -Path (Join-Path $DistRoot "licenses")

$PythonArchive = Join-Path $env:RUNNER_TEMP "python-$PythonVersion-amd64.zip"
$PythonArchiveUrl = (
    "https://www.python.org/ftp/python/$PythonVersion/" +
    "python-$PythonVersion-amd64.zip"
)
$PythonArchiveSha256 = (
    "4BA90A4AB8990891033D37FF04D2047F" +
    "DAE8948D0D2729A68D3A6A17C585B681"
)

Invoke-WebRequest $PythonArchiveUrl -OutFile $PythonArchive
$ActualPythonSha256 = (Get-FileHash $PythonArchive -Algorithm SHA256).Hash
if ($ActualPythonSha256 -ne $PythonArchiveSha256) {
    throw "Application-local Python archive checksum mismatch: $ActualPythonSha256"
}
Expand-Archive $PythonArchive -DestinationPath $RuntimeRoot.FullName
Remove-Item $PythonArchive -Force

$RuntimeLib = Join-Path $RuntimeRoot.FullName "Lib"
New-Item -ItemType Directory -Path $RuntimeLib -Force | Out-Null

$BuildSitePackages = Join-Path $BuildVenv "Lib\site-packages"
$PortableSitePackages = Join-Path $RuntimeLib "site-packages"
if (Test-Path $PortableSitePackages) {
    Remove-Item $PortableSitePackages -Recurse -Force
}
Move-Item $BuildSitePackages (Join-Path $RuntimeLib "site-packages")

# Build-only packaging tools are not required by the application.
foreach ($pattern in @(
    "pip",
    "pip-*.dist-info",
    "wheel",
    "wheel-*.dist-info",
    "ninja",
    "ninja-*.dist-info"
)) {
    Get-ChildItem $PortableSitePackages -Filter $pattern -Force |
        Remove-Item -Recurse -Force
}

# Keep the application-local runtime isolated while enabling the standard
# library, DLL extension modules, and the moved third-party packages.
@(
    "python311.zip",
    ".",
    "Lib",
    "DLLs",
    "Lib\site-packages",
    "import site"
) | Set-Content (Join-Path $RuntimeRoot.FullName "python311._pth") -Encoding ascii

Remove-Item $BuildVenv -Recurse -Force
Remove-Item $Wheelhouse -Recurse -Force
Remove-Item $PortableRequirements -Force

# Copy the pinned engine without Git metadata, demos, or alternate frontends.
foreach ($engineEntry in @(
    "spar3d",
    "load",
    "run.py",
    "__init__.py",
    "LICENSE.md",
    "README.md"
)) {
    $source = Join-Path $SourceRoot $engineEntry
    if (Test-Path $source) {
        Copy-Item $source $PortableVendor.FullName -Recurse -Force
    }
}

Copy-Item (Join-Path $AppSource "worker.py") $PortableApp.FullName -Force
Copy-Item (Join-Path $SourceRoot "LICENSE.md") (
    Join-Path $DistRoot "LICENSE-SPAR3D.md"
) -Force
foreach ($RepositoryDocument in @("LICENSE", "NOTICE_THIRD_PARTY.md", "VERSION.txt")) {
    $DocumentPath = Join-Path $RepositoryRoot $RepositoryDocument
    if (Test-Path $DocumentPath -PathType Leaf) {
        Copy-Item $DocumentPath $DistRoot -Force
    }
}
Remove-Item $SourceRoot -Recurse -Force

# Compile the supplied WinForms frontend against .NET Framework 4.8.
Write-Host "Compiling MujassamAI.exe"
$Csc = Join-Path $VsInstall "MSBuild\Current\Bin\Roslyn\csc.exe"
$ReferenceRoot = (
    "C:\Program Files (x86)\Reference Assemblies\Microsoft\Framework" +
    "\.NETFramework\v4.8"
)
if (-not (Test-Path $Csc) -or -not (Test-Path $ReferenceRoot)) {
    throw ".NET Framework 4.8 compiler/reference assemblies were not found"
}

$CscReferences = @(
    "System.dll",
    "System.Core.dll",
    "System.Drawing.dll",
    "System.Windows.Forms.dll",
    "System.Web.Extensions.dll"
) | ForEach-Object {
    $ReferencePath = Join-Path $ReferenceRoot $_
    if (-not (Test-Path $ReferencePath -PathType Leaf)) {
        throw "Missing .NET Framework reference assembly: $ReferencePath"
    }
    "/reference:$ReferencePath"
}

$CscArguments = @(
    "/nologo",
    "/noconfig",
    "/target:winexe",
    "/platform:x64",
    "/optimize+",
    "/debug-",
    "/langversion:latest",
    "/out:$(Join-Path $DistRoot 'MujassamAI.exe')"
) + $CscReferences + @(
    (Join-Path $AppSource "Program.cs"),
    (Join-Path $AppSource "MainForm.cs")
)

& $Csc @CscArguments

$GuiSelfTestReport = Join-Path $env:RUNNER_TEMP "mujassam-gui-self-test.txt"
$PreviousNativePreference = $PSNativeCommandUseErrorActionPreference
try {
    $PSNativeCommandUseErrorActionPreference = $false
    & (Join-Path $DistRoot "MujassamAI.exe") --self-test $GuiSelfTestReport
    $GuiSelfTestExitCode = $LASTEXITCODE
} finally {
    $PSNativeCommandUseErrorActionPreference = $PreviousNativePreference
}
if ($GuiSelfTestExitCode -ne 0) {
    $GuiSelfTestDetails = if (Test-Path $GuiSelfTestReport -PathType Leaf) {
        Get-Content $GuiSelfTestReport -Raw
    } else {
        "MujassamAI.exe did not create its self-test report"
    }
    throw "MujassamAI.exe self-test failed: $GuiSelfTestDetails"
}
if (Test-Path $GuiSelfTestReport) {
    Remove-Item $GuiSelfTestReport -Force
}

# Copy the current VC runtime and OpenMP redistributables app-locally.
$RedistBase = Join-Path $VsInstall "VC\Redist\MSVC"
$LatestRedist = Get-ChildItem $RedistBase -Directory |
    Sort-Object { [version]$_.Name } -Descending |
    Select-Object -First 1

if (-not $LatestRedist) {
    throw "Visual C++ redistributable files were not found"
}

Get-ChildItem (Join-Path $LatestRedist.FullName "x64") `
    -Recurse `
    -File `
    -Filter "*.dll" |
    Where-Object {
        $_.Directory.Name -match "^Microsoft\.VC\d+\.(CRT|OpenMP)$"
    } |
    Group-Object Name |
    ForEach-Object {
        Copy-Item $_.Group[0].FullName $RuntimeRoot.FullName -Force
    }

# The cu124 PyTorch wheel carries the CUDA runtime needed on the target PC.
$TorchLib = Join-Path $PortableSitePackages "torch\lib"
$Cudart = Get-ChildItem $TorchLib -Filter "cudart64_12.dll" -File
if (-not $Cudart) {
    throw "The packaged PyTorch wheel does not contain the CUDA 12 runtime"
}

Write-Host "Downloading the pinned runtime model assets into the stage"
python (Join-Path $PSScriptRoot "download_models.py") `
    --mode runtime-assets `
    --model-dir $ModelRoot.FullName `
    --background-dir $BackgroundRoot.FullName `
    --revision $ModelRevision `
    --manifest (Join-Path $DistRoot "MODEL-MANIFEST.json")

# Copy package license files carried in wheel metadata.
Get-ChildItem $PortableSitePackages -Directory -Filter "*.dist-info" |
    ForEach-Object {
        $Destination = New-Item -ItemType Directory -Path (
            Join-Path $LicenseRoot.FullName $_.Name
        )
        Get-ChildItem $_.FullName -File |
            Where-Object { $_.Name -match "^(LICENSE|LICENCE|COPYING|NOTICE)" } |
            Copy-Item -Destination $Destination.FullName -Force
        $Pep639 = Join-Path $_.FullName "licenses"
        if (Test-Path $Pep639) {
            Copy-Item $Pep639 $Destination.FullName -Recurse -Force
        }
    }

$PythonLicense = Join-Path $RuntimeRoot "LICENSE.txt"
if (Test-Path $PythonLicense) {
    $PythonLicenseDirectory = New-Item -ItemType Directory -Path (
        Join-Path $LicenseRoot.FullName "Python-$PythonVersion"
    )
    Copy-Item $PythonLicense $PythonLicenseDirectory.FullName -Force
}

@'
This Stability AI Model is licensed under the Stability AI Community License, Copyright ©  Stability AI Ltd.
All Rights Reserved
'@ | Set-Content (Join-Path $DistRoot "Notice.txt") -Encoding utf8

@'
Powered by Stability AI

Mujassam AI — Portable SPAR3D for Windows
==========================================

This folder is installed by Setup.exe from the matching GitHub Release.
Keep its files together and start MujassamAI.exe.

Requirements on the target computer:
- 64-bit Windows 10/11
- NVIDIA RTX GPU and a current NVIDIA display driver
- RTX 4060 Ti 8 GB should use SPAR3D low-VRAM mode

Python, Visual Studio, and the CUDA Toolkit are included or not required on
the target machine. Generated files are written to Documents\Mujassam AI\Exports.

The SPAR3D Windows backend is experimental. The included Stability AI license
applies to the model and engine. Commercial users must review its registration
and revenue provisions before distributing or operating this package.
'@ | Set-Content (Join-Path $DistRoot "README.txt") -Encoding utf8

# Smoke-test imports without requiring a GPU on the hosted runner.
$PreviousPath = $env:PATH
$env:PATH = "$RuntimeRoot;$TorchLib;$PreviousPath"
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONDONTWRITEBYTECODE = "1"
$ApplicationPython = Join-Path $RuntimeRoot "python.exe"
$PreviousVendorRoot = $env:MUJASSAM_VENDOR_ROOT
$env:MUJASSAM_VENDOR_ROOT = $PortableVendor.FullName

& $ApplicationPython -B (Join-Path $PortableApp.FullName "worker.py") --self-test

& $ApplicationPython -B -c @'
import importlib
import os
import sys
sys.path.insert(0, os.environ["MUJASSAM_VENDOR_ROOT"])
import torch
import spar3d
from spar3d.models.mesh import TRIANGLE_REMESH_AVAILABLE
from spar3d.system import SPAR3D
from spar3d.utils import foreground_crop, remove_background
from transparent_background import Remover
importlib.import_module("texture_baker._C")
importlib.import_module("uv_unwrapper._C")
if not TRIANGLE_REMESH_AVAILABLE:
    raise RuntimeError("triangle remeshing dependency is unavailable")
print("torch", torch.__version__, "cuda", torch.version.cuda)
print("portable worker dependency smoke test: OK")
'@

$env:PATH = $PreviousPath
$env:MUJASSAM_VENDOR_ROOT = $PreviousVendorRoot

$InstalledPackages = & $ApplicationPython -B -c @'
from importlib.metadata import distributions
for item in sorted((d.metadata["Name"], d.version) for d in distributions()):
    print(f"{item[0]}=={item[1]}")
'@

@"
Mujassam AI portable build
SPAR3D commit: $ResolvedCommit
Model revision: $ModelRevision
Python: $PythonVersion application-local x64
PyTorch: $TorchVersion
TorchVision: $TorchVisionVersion
CUDA build toolkit: $CudaVersion
CUDA extension architecture: 8.9
MSVC compiler: $ClPath

Installed Python packages:
$($InstalledPackages -join "`n")
"@ | Set-Content (Join-Path $DistRoot "BUILD-MANIFEST.txt") -Encoding utf8

$TotalBytes = Get-DirectorySizeBytes $DistRoot
Write-Host ("Portable stage size: {0:N2} GB" -f ($TotalBytes / 1GB))

# Create only a runtime ZIP (the large model is deliberately absent), then
# discard the expanded stage before splitting to keep runner disk use bounded.
Write-Host "Compressing the runtime-only portable folder"
New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
if (Test-Path (Join-Path $ModelRoot.FullName "model.safetensors")) {
    throw "The runtime stage must not contain model.safetensors"
}
Compress-Archive `
    -Path (Join-Path $DistRoot "*") `
    -DestinationPath $RuntimeArchive `
    -CompressionLevel Optimal
if (-not (Test-Path $RuntimeArchive -PathType Leaf)) {
    throw "runtime.zip was not created"
}
Remove-Item $DistRoot -Recurse -Force

Write-Host "Splitting runtime.zip into release-sized assets"
$RuntimeRecord = Split-ReleaseFile `
    -Source $RuntimeArchive `
    -OutputDirectory $ReleaseRoot `
    -Prefix "runtime.zip" `
    -ChunkSize $ReleasePartSize
Remove-Item $RuntimeArchive -Force
$RuntimePartPaths = @(Assert-ReleaseParts $RuntimeRecord)
Ensure-DraftRelease
Write-Host "Uploading verified runtime parts to $ReleaseTag"
Publish-ReleaseAssets $RuntimePartPaths
Remove-Item $RuntimePartPaths -Force

# Stream the gated weight directly into release parts. There is intentionally
# never a complete model.safetensors file on the hosted runner.
Write-Host "Streaming the pinned model into release-sized assets"
python (Join-Path $PSScriptRoot "download_models.py") `
    --mode weights `
    --revision $ModelRevision `
    --parts-dir $ReleaseRoot `
    --part-prefix "model.safetensors" `
    --part-size $ReleasePartSize `
    --record $WeightsRecordPath
$ModelRecord = Get-Content $WeightsRecordPath -Raw | ConvertFrom-Json
Remove-Item $WeightsRecordPath -Force
$ModelPartPaths = @(Assert-ReleaseParts $ModelRecord)
Write-Host "Uploading verified model parts to $ReleaseTag"
Publish-ReleaseAssets $ModelPartPaths
Remove-Item $ModelPartPaths -Force

$ReleaseManifest = [ordered]@{
    schema_version = 1
    runtime = $RuntimeRecord
    model = $ModelRecord
    executable = "MujassamAI.exe"
}
$ReleaseManifestPath = Join-Path $ReleaseRoot "release-manifest.json"
$ReleaseManifest |
    ConvertTo-Json -Depth 8 |
    Set-Content $ReleaseManifestPath -Encoding utf8
$ManifestSha256 = (
    Get-FileHash $ReleaseManifestPath -Algorithm SHA256
).Hash.ToLowerInvariant()

# Compile the downloader/installer with constants bound to this exact release.
Write-Host "Compiling Setup.exe for $ReleaseTag"
$ReleaseBaseUrl = (
    "https://github.com/$($env:GITHUB_REPOSITORY)/releases/download/$ReleaseTag"
)
$PatchedSetupSource = Join-Path $env:RUNNER_TEMP "MujassamAI-Setup.cs"
$SetupText = Get-Content $InstallerSource -Raw
$SetupMarkers = @(
    "@@RELEASE_BASE_URL@@",
    "@@GITHUB_REPOSITORY@@",
    "@@RELEASE_TAG@@",
    "@@MANIFEST_SHA256@@"
)
foreach ($SetupMarker in $SetupMarkers) {
    if (-not $SetupText.Contains($SetupMarker)) {
        throw "Setup.cs is missing required build placeholder: $SetupMarker"
    }
}
$SetupText = $SetupText.Replace("@@RELEASE_BASE_URL@@", $ReleaseBaseUrl)
$SetupText = $SetupText.Replace("@@GITHUB_REPOSITORY@@", $env:GITHUB_REPOSITORY)
$SetupText = $SetupText.Replace("@@RELEASE_TAG@@", $ReleaseTag)
$SetupText = $SetupText.Replace("@@MANIFEST_SHA256@@", $ManifestSha256)
$UnresolvedSetupMarkers = @(
    $SetupMarkers | Where-Object { $SetupText.Contains($_) }
)
if ($UnresolvedSetupMarkers.Count -ne 0) {
    throw "Setup.cs still contains a required unresolved build placeholder"
}
$SetupText | Set-Content $PatchedSetupSource -Encoding utf8

$SetupManifestPath = Join-Path $env:RUNNER_TEMP "MujassamAI-Setup.manifest"
@'
<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <assemblyIdentity version="1.0.0.0" name="MujassamAI.Setup" />
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v3">
    <security>
      <requestedPrivileges>
        <requestedExecutionLevel level="asInvoker" uiAccess="false" />
      </requestedPrivileges>
    </security>
  </trustInfo>
  <application xmlns="urn:schemas-microsoft-com:asm.v3">
    <windowsSettings>
      <longPathAware xmlns="http://schemas.microsoft.com/SMI/2016/WindowsSettings">true</longPathAware>
    </windowsSettings>
  </application>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}" />
    </application>
  </compatibility>
  <dependency>
    <dependentAssembly>
      <assemblyIdentity
        type="win32"
        name="Microsoft.Windows.Common-Controls"
        version="6.0.0.0"
        processorArchitecture="*"
        publicKeyToken="6595b64144ccf1df"
        language="*" />
    </dependentAssembly>
  </dependency>
</assembly>
'@ | Set-Content $SetupManifestPath -Encoding utf8

$SetupReferenceNames = @(
    "System.Net.Http.dll",
    "System.IO.Compression.dll",
    "System.IO.Compression.FileSystem.dll",
    "System.Runtime.Serialization.dll"
)
$SetupReferences = $CscReferences + @(
    $SetupReferenceNames | ForEach-Object {
        $ReferencePath = Join-Path $ReferenceRoot $_
        if (-not (Test-Path $ReferencePath -PathType Leaf)) {
            throw "Missing Setup reference assembly: $ReferencePath"
        }
        "/reference:$ReferencePath"
    }
)

$SetupCscArguments = @(
    "/nologo",
    "/noconfig",
    "/target:winexe",
    "/platform:x64",
    "/optimize+",
    "/debug-",
    "/langversion:latest",
    "/win32manifest:$SetupManifestPath",
    "/out:$(Join-Path $ReleaseRoot 'Setup.exe')"
) + $SetupReferences + @($PatchedSetupSource)
& $Csc @SetupCscArguments
Remove-Item $PatchedSetupSource -Force
Remove-Item $SetupManifestPath -Force

$SetupExecutable = Join-Path $ReleaseRoot "Setup.exe"
$SetupSelfTestReport = Join-Path $env:RUNNER_TEMP "mujassam-setup-self-test.txt"
$PreviousNativePreference = $PSNativeCommandUseErrorActionPreference
try {
    $PSNativeCommandUseErrorActionPreference = $false
    & $SetupExecutable --self-test $SetupSelfTestReport
    $SetupSelfTestExitCode = $LASTEXITCODE
} finally {
    $PSNativeCommandUseErrorActionPreference = $PreviousNativePreference
}
if ($SetupSelfTestExitCode -ne 0) {
    $SetupSelfTestDetails = if (Test-Path $SetupSelfTestReport -PathType Leaf) {
        Get-Content $SetupSelfTestReport -Raw
    } else {
        "Setup.exe did not create its self-test report"
    }
    throw "Setup.exe self-test failed: $SetupSelfTestDetails"
}
if (Test-Path $SetupSelfTestReport) {
    Remove-Item $SetupSelfTestReport -Force
}

$FinalAssets = @(
    $ReleaseManifestPath,
    $SetupExecutable
)
Write-Host "Uploading the installer and release manifest"
Publish-ReleaseAssets $FinalAssets
gh release edit $ReleaseTag `
    --repo $env:GITHUB_REPOSITORY `
    --draft=false `
    --latest=false

$ReleaseBytes = Get-DirectorySizeBytes $ReleaseRoot
Write-Host ("Local final assets size: {0:N2} MB" -f ($ReleaseBytes / 1MB))
Write-Host "Published GitHub Release: $ReleaseTag"
