[CmdletBinding()]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$InstallRoot = (Join-Path $env:USERPROFILE `
        "Documents\MujassamAI-Portable")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if ($PSVersionTable.PSVersion -lt [version]"7.4") {
    throw "يلزم PowerShell 7.4 أو أحدث (pwsh.exe)."
}
$PSNativeCommandUseErrorActionPreference = $true

$SourceCommit = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
$OfficialRepository = "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1.git"
$OfficialLicenseBlobSha256 = `
    "b79ac5e11ce063b6c6570dbe9686a45a03ba08bd248aa6aa82fb342a23a81c0c"
$PackagedLicenseSha256 = `
    "20b7e73b7996a815226ae4c08d18a7891c417749f2de687d1db90b4e36b78789"
$Msvc142Component = "Microsoft.VisualStudio.Component.VC.14.29.16.11.x86.x64"
$CudaHome = "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.4"
$TemporaryPrefix = "MujassamAI-hy21-rasterizer-"

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-NormalizedLegalText([string]$Path) {
    $Utf8 = [Text.UTF8Encoding]::new($false, $true)
    $Text = [IO.File]::ReadAllText($Path, $Utf8)
    $Text = $Text.Replace("`r`n", "`n").Replace("`r", "`n")
    if ($Text.Contains([char]0)) {
        throw "Legal text contains an invalid NUL: $Path"
    }
    return $Text
}

function Assert-NormalDirectory([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label غير موجود: $Path"
    }
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "رفض symlink/junction في $Label`: $Path"
    }
}

function Assert-NormalFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label غير موجود: $Path"
    }
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "رفض symlink في $Label`: $Path"
    }
}

function Assert-NoReparsePointInExistingPath(
    [string]$Root,
    [string]$Candidate
) {
    $RootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $CandidateFull = [IO.Path]::GetFullPath($Candidate)
    if (-not $CandidateFull.StartsWith(
        $RootFull + '\', [StringComparison]::OrdinalIgnoreCase) -and
        -not [string]::Equals(
            $CandidateFull, $RootFull,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "المسار خارج الجذر المسموح: $CandidateFull"
    }
    $Current = $RootFull
    foreach ($Part in @(
        $CandidateFull.Substring($RootFull.Length).TrimStart('\').Split('\') |
            Where-Object { $_ -ne "" }
    )) {
        if (Test-Path -LiteralPath $Current) {
            $CurrentItem = Get-Item -LiteralPath $Current -Force
            if ($CurrentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "رفض reparse point داخل المسار: $Current"
            }
        }
        $Current = Join-Path $Current $Part
    }
    if (Test-Path -LiteralPath $Current) {
        $CurrentItem = Get-Item -LiteralPath $Current -Force
        if ($CurrentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "رفض reparse point داخل المسار: $Current"
        }
    }
}

function Assert-SafeOwnedTemporaryDirectory([string]$Path) {
    $FullPath = [IO.Path]::GetFullPath($Path)
    $TemporaryRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\') + '\'
    if (-not $FullPath.StartsWith(
            $TemporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFileName($FullPath).StartsWith(
            $TemporaryPrefix, [StringComparison]::Ordinal)) {
        throw "رفض تنظيف مسار مؤقت غير مملوك للإصلاح: $FullPath"
    }
}

function Remove-OwnedTemporaryDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    Assert-SafeOwnedTemporaryDirectory $Path
    $Item = Get-Item -LiteralPath $Path -Force
    if (-not $Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "رفض تنظيف مسار مؤقت غير عادي: $Path"
    }
    Remove-Item -LiteralPath $Path -Recurse -Force
}

function Get-Application([string]$Name) {
    $Command = Get-Command $Name -CommandType Application `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $Command) {
        throw "الأداة المطلوبة غير موجودة: $Name"
    }
    return $Command.Source
}

function Assert-MujassamProcessesStopped([string]$Root) {
    # HOTFIX_PROCESS_RECHECK: called both before compilation and immediately
    # before backup/replacement, closing the long-build reopen window.
    $RunningApplication = @(Get-Process -Name "MujassamAI" `
        -ErrorAction SilentlyContinue)
    $RunningPortablePython = @(Get-Process -Name "python" `
        -ErrorAction SilentlyContinue | Where-Object {
            try {
                $_.Path.StartsWith(
                    $Root + '\', [StringComparison]::OrdinalIgnoreCase)
            } catch {
                $false
            }
        })
    if ($RunningApplication.Count -ne 0 -or
        $RunningPortablePython.Count -ne 0) {
        throw "أغلق Mujassam AI وانتظر توقف عمليته، ثم شغّل الإصلاح مرة أخرى."
    }
}

function Test-OfficialOrigin([string]$Origin) {
    $Normalized = $Origin.Trim().TrimEnd('/')
    if ($Normalized.EndsWith(".git", [StringComparison]::OrdinalIgnoreCase)) {
        $Normalized = $Normalized.Substring(0, $Normalized.Length - 4)
    }
    return [string]::Equals(
        $Normalized,
        "https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1",
        [StringComparison]::OrdinalIgnoreCase)
}

function Export-ExistingPinnedSource(
    [string]$Git,
    [string]$Destination,
    [string]$Archive,
    [string]$HooksDirectory
) {
    $Candidates = @(Get-ChildItem -LiteralPath $env:TEMP -Directory `
        -Filter "MujassamAI-hy21-*" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTimeUtc -Descending)
    foreach ($CandidateRoot in $Candidates) {
        if ($CandidateRoot.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            continue
        }
        $Candidate = Join-Path $CandidateRoot.FullName "hy3d21-src"
        $CandidateGit = Join-Path $Candidate ".git"
        if (-not (Test-Path -LiteralPath $CandidateGit -PathType Container)) {
            continue
        }
        $CandidateItem = Get-Item -LiteralPath $Candidate -Force
        $CandidateGitItem = Get-Item -LiteralPath $CandidateGit -Force
        if (($CandidateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            ($CandidateGitItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            continue
        }
        try {
            $HooksConfig = "core.hooksPath=$HooksDirectory"
            $Resolved = (& $Git -c $HooksConfig -C $Candidate `
                rev-parse HEAD 2>$null).Trim()
            $Origin = (& $Git -c $HooksConfig -C $Candidate `
                remote get-url origin 2>$null).Trim()
            if ($Resolved -cne $SourceCommit -or -not (Test-OfficialOrigin $Origin)) {
                continue
            }
            # Export committed objects, never the possibly modified old worktree.
            & $Git -c $HooksConfig -C $Candidate archive --format=zip `
                "--output=$Archive" $SourceCommit -- `
                LICENSE hy3dpaint/custom_rasterizer
            Expand-Archive -LiteralPath $Archive -DestinationPath $Destination
            Write-Host "استخدمت نسخة المصدر الرسمية الموجودة محليًا؛ لا يوجد تنزيل مصدر." `
                -ForegroundColor Green
            return $true
        } catch {
            if (Test-Path -LiteralPath $Archive) {
                Remove-Item -LiteralPath $Archive -Force
            }
            if (Test-Path -LiteralPath $Destination) {
                Remove-Item -LiteralPath $Destination -Recurse -Force
            }
        }
    }
    return $false
}

function Fetch-PinnedSparseSource(
    [string]$Git,
    [string]$Destination,
    [string]$HooksDirectory
) {
    Write-Host (
        "لا توجد نسخة مصدر محلية. سينزل الآن مصدر custom_rasterizer الرسمي " +
        "الصغير فقط؛ لن تُنزّل نماذج ولا أوزان ولن يُعاد بناء البرنامج."
    ) -ForegroundColor Yellow
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $HooksConfig = "core.hooksPath=$HooksDirectory"
    & $Git -c $HooksConfig -C $Destination init --quiet
    & $Git -c $HooksConfig -C $Destination config `
        core.hooksPath $HooksDirectory
    & $Git -c $HooksConfig -C $Destination config core.autocrlf false
    & $Git -c $HooksConfig -C $Destination remote add origin $OfficialRepository
    & $Git -c $HooksConfig -C $Destination sparse-checkout init --no-cone
    & $Git -c $HooksConfig -C $Destination sparse-checkout set --no-cone `
        "/LICENSE" "/hy3dpaint/custom_rasterizer/"
    & $Git -c $HooksConfig -C $Destination fetch `
        --quiet --depth 1 --filter=blob:none `
        origin $SourceCommit
    & $Git -c $HooksConfig -C $Destination checkout `
        --quiet --detach FETCH_HEAD
    $Resolved = (& $Git -c $HooksConfig -C $Destination `
        rev-parse HEAD).Trim()
    if ($Resolved -cne $SourceCommit) {
        throw "مصدر Hunyuan3D-2.1 لا يطابق commit المثبت: $Resolved"
    }
    $Origin = (& $Git -c $HooksConfig -C $Destination `
        remote get-url origin).Trim()
    if (-not (Test-OfficialOrigin $Origin)) {
        throw "مصدر Hunyuan3D-2.1 ليس من مستودع Tencent الرسمي."
    }
}

function Invoke-RasterizerSmoke(
    [string]$Python,
    [string]$Packages,
    [string]$ModuleDirectory
) {
    # HOTFIX_GPU_MICRO_SMOKE: executes the exact CUDA path that previously
    # raised "expected scalar type UInt64 but found Long".
    $Smoke = @'
import pathlib
import sys

packages = pathlib.Path(sys.argv[1]).resolve(strict=True)
module_dir = pathlib.Path(sys.argv[2]).resolve(strict=True)
sys.path.insert(0, str(module_dir))
sys.path.insert(1, str(packages))

import torch
import custom_rasterizer_kernel as rasterizer

loaded = pathlib.Path(rasterizer.__file__).resolve(strict=True)
if not loaded.is_relative_to(module_dir):
    raise RuntimeError(f"smoke imported the wrong rasterizer: {loaded}")
if not torch.cuda.is_available():
    raise RuntimeError("CUDA is unavailable in the portable runtime")

vertices = torch.tensor(
    [[-0.75, -0.75, 0.25, 1.0],
     [ 0.75, -0.75, 0.25, 1.0],
     [ 0.00,  0.75, 0.25, 1.0]],
    dtype=torch.float32,
    device="cuda",
)
faces = torch.tensor([[0, 1, 2]], dtype=torch.int32, device="cuda")
depth = torch.empty((0,), dtype=torch.float32, device="cuda")
face_indices, barycentric = rasterizer.rasterize_image(
    vertices, faces, depth, 8, 8, 1.0e-6, 0
)
torch.cuda.synchronize()
if face_indices.dtype != torch.int32 or tuple(face_indices.shape) != (8, 8):
    raise RuntimeError("rasterizer face-index output has the wrong ABI")
if barycentric.dtype != torch.float32 or tuple(barycentric.shape) != (8, 8, 3):
    raise RuntimeError("rasterizer barycentric output has the wrong ABI")
if not bool(torch.isfinite(barycentric).all().item()):
    raise RuntimeError("rasterizer produced non-finite barycentric values")
print("MJRASTERHOTFIXSMOKE|OK|1")
'@
    & $Python -I -X utf8 -c $Smoke $Packages $ModuleDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "فشل اختبار CUDA المصغّر للـrasterizer. لم يتغير التثبيت."
    }
}

function Set-FileAtomically(
    [string]$Source,
    [string]$Destination,
    [string]$ExpectedSha256
) {
    # HOTFIX_SAFE_REPLACE: same-directory candidate and explicit backup make
    # File.Replace atomic and recoverable on the destination volume.
    $Parent = [IO.Path]::GetDirectoryName($Destination)
    $Candidate = Join-Path $Parent (
        "MujassamAI-rasterizer-new-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $ReplacementBackup = Join-Path $Parent (
        "MujassamAI-rasterizer-replaced-" +
        [Guid]::NewGuid().ToString("N") + ".tmp")
    $Replaced = $false
    try {
        [IO.File]::Copy($Source, $Candidate, $false)
        if ((Get-Sha256 $Candidate) -cne $ExpectedSha256) {
            throw "فشل فحص الملف المؤقت قبل الاستبدال: $Destination"
        }
        [IO.File]::Replace(
            $Candidate, $Destination, $ReplacementBackup, $true)
        $Replaced = $true
        if ((Get-Sha256 $Destination) -cne $ExpectedSha256) {
            throw "فشل فحص الملف بعد الاستبدال: $Destination"
        }
    } catch {
        $ReplaceFailure = $_
        if ($Replaced -and
            (Test-Path -LiteralPath $ReplacementBackup -PathType Leaf)) {
            # Restore the original file atomically before returning the error.
            [IO.File]::Replace(
                $ReplacementBackup, $Destination, $Candidate, $true)
        }
        throw $ReplaceFailure
    } finally {
        foreach ($Temporary in @($Candidate, $ReplacementBackup)) {
            if (Test-Path -LiteralPath $Temporary -PathType Leaf) {
                [IO.File]::Delete($Temporary)
            }
        }
    }
}

if (-not $IsWindows -or
    -not [Environment]::Is64BitOperatingSystem -or
    -not [Environment]::Is64BitProcess) {
    throw "هذا الإصلاح يتطلب Windows x64 وPowerShell x64."
}

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$PatchScript = Join-Path $PSScriptRoot "patch_hunyuan21_windows.py"
$UpdatedWorker = Join-Path $RepositoryRoot `
    "app\engines\hunyuan21\hunyuan21_worker.py"
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$EngineRoot = Join-Path $InstallRoot "app\engines\hunyuan21"
$Packages = Join-Path $EngineRoot "python_packages"
$InstalledWorker = Join-Path $EngineRoot "hunyuan21_worker.py"
$EngineManifestPath = Join-Path $EngineRoot "ENGINE-MANIFEST.json"
$PackagedLicense = Join-Path $EngineRoot "LICENSE-HUNYUAN3D-2.1.txt"
$InstalledNotice = Join-Path $EngineRoot "NOTICE.txt"
$PortablePython = Join-Path $InstallRoot "rt\python.exe"
$PortableSitePackages = Join-Path $InstallRoot "rt\Lib\site-packages"

Assert-NormalDirectory $InstallRoot "مجلد MujassamAI-Portable"
foreach ($Directory in @($EngineRoot, $Packages, $PortableSitePackages)) {
    Assert-NoReparsePointInExistingPath $InstallRoot $Directory
    Assert-NormalDirectory $Directory "مجلد المحرك"
}
foreach ($RequiredFile in @(
    $PatchScript, $UpdatedWorker, $InstalledWorker, $EngineManifestPath,
    $PackagedLicense, $InstalledNotice, $PortablePython
)) {
    if ($RequiredFile.StartsWith(
        $InstallRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        Assert-NoReparsePointInExistingPath $InstallRoot $RequiredFile
    }
    Assert-NormalFile $RequiredFile "ملف مطلوب"
}

$EngineManifest = Get-Content -LiteralPath $EngineManifestPath -Raw |
    ConvertFrom-Json
if ([string]$EngineManifest.source.commit -cne $SourceCommit -or
    [string]$EngineManifest.runtime_abi.python -cne "3.11.9" -or
    [string]$EngineManifest.runtime_abi.pytorch -cne "2.5.1+cu124" -or
    [string]$EngineManifest.runtime_abi.cuda_runtime -cne "12.4" -or
    [string]$EngineManifest.runtime_abi.platform -cne "win_amd64") {
    throw "التثبيت لا يطابق Hunyuan3D-2.1 ABI/source المطلوب للإصلاح."
}
if ((Get-Sha256 $PackagedLicense) -cne $PackagedLicenseSha256) {
    throw "بصمة ترخيص Hunyuan3D-2.1 المثبت غير صحيحة."
}
$NoticeText = [IO.File]::ReadAllText($InstalledNotice)
if (-not $NoticeText.Contains("Configured usage scope: personal_local_only")) {
    throw "هذا الإصلاح مخصص للتثبيت الشخصي المحلي فقط."
}

$InstalledPyds = @(Get-ChildItem -LiteralPath $Packages -Recurse -File `
    -Filter "custom_rasterizer_kernel*.pyd")
if ($InstalledPyds.Count -ne 1) {
    throw "توقعت ملف custom_rasterizer_kernel.pyd واحدًا، وجدت $($InstalledPyds.Count)."
}
$InstalledPyd = $InstalledPyds[0].FullName
Assert-NoReparsePointInExistingPath $InstallRoot $InstalledPyd
Assert-NormalFile $InstalledPyd "ملف rasterizer المثبت"

Assert-MujassamProcessesStopped $InstallRoot

$Git = Get-Application "git.exe"
$PyLauncher = Get-Application "py.exe"
$BuildPythonProbeJson = & $PyLauncher -3.11 -I -X utf8 -c `
    'import json,struct,sys; print(json.dumps({"executable":sys.executable,"version":".".join(map(str,sys.version_info[:3])),"bits":struct.calcsize("P")*8}))'
$BuildPythonProbe = $BuildPythonProbeJson | ConvertFrom-Json
if ([string]$BuildPythonProbe.version -cne "3.11.9" -or
    [int]$BuildPythonProbe.bits -ne 64 -or
    [string]::IsNullOrWhiteSpace([string]$BuildPythonProbe.executable)) {
    throw (
        "يلزم تثبيت CPython 3.11.9 x64 الكامل. py.exe -3.11 أعاد: " +
        "$($BuildPythonProbe.version) ($($BuildPythonProbe.bits)-bit)."
    )
}
$BuildPython = [IO.Path]::GetFullPath([string]$BuildPythonProbe.executable)
Assert-NormalFile $BuildPython "CPython 3.11.9 الكامل"
$BuildPythonRoot = [IO.Path]::GetDirectoryName($BuildPython)
$BuildPythonInclude = Join-Path $BuildPythonRoot "Include\Python.h"
$BuildPythonImportLibrary = Join-Path $BuildPythonRoot "libs\python311.lib"
Assert-NormalFile $BuildPythonInclude "CPython Include/Python.h"
Assert-NormalFile $BuildPythonImportLibrary "CPython libs/python311.lib"
# HOTFIX_FULL_BUILD_PYTHON: the embeddable portable runtime intentionally has
# no C headers/import library.  This exact full CPython drives compilation only;
# every runtime and CUDA smoke still uses $PortablePython.
Write-Host "Full CPython build driver: $BuildPython" -ForegroundColor Green
$ProgramFilesX86 = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ProgramFilesX86)
$VsWhere = Join-Path $ProgramFilesX86 `
    "Microsoft Visual Studio\Installer\vswhere.exe"
Assert-NormalFile $VsWhere "Visual Studio Installer/vswhere"
$VsInstall = (& $VsWhere -latest -products * -requires $Msvc142Component `
    -property installationPath | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($VsInstall)) {
    throw "مكوّن MSVC v142/14.29 x64 غير مثبت في Visual Studio Build Tools."
}
$VsInstall = $VsInstall.Trim()
Import-Module "$VsInstall\Common7\Tools\Microsoft.VisualStudio.DevShell.dll"
Enter-VsDevShell -VsInstallPath $VsInstall -SkipAutomaticLocation `
    -DevCmdArguments "-arch=x64 -host_arch=x64 -vcvars_ver=14.29"
$ClPath = (Get-Command cl.exe -CommandType Application).Source
if ($ClPath -notmatch '\\14\.29\.') {
    throw "طلبت MSVC 14.29 لكن cl.exe الحالي هو: $ClPath"
}

$Nvcc = Join-Path $CudaHome "bin\nvcc.exe"
Assert-NormalFile $Nvcc "CUDA 12.4 nvcc"
$env:CUDA_HOME = $CudaHome
$env:CUDA_PATH = $CudaHome
$env:PATH = "$CudaHome\bin;$env:PATH"
$env:USE_CUDA = "1"
$env:USE_NATIVE_ARCH = "0"
$env:MAX_JOBS = "2"
$env:DISTUTILS_USE_SDK = "1"
$env:MSSdk = "1"
$env:PYTHONNOUSERSITE = "1"

$RuntimeProbe = @'
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).resolve(strict=True)))
import setuptools
import torch
if sys.version_info[:3] != (3, 11, 9) or sys.maxsize <= 2**32:
    raise RuntimeError(f"unexpected portable Python: {sys.version}")
if torch.__version__ != "2.5.1+cu124":
    raise RuntimeError(f"unexpected PyTorch: {torch.__version__}")
if not torch.cuda.is_available():
    raise RuntimeError("CUDA is unavailable")
major, minor = torch.cuda.get_device_capability()
print(f"{major}.{minor}")
'@
$GpuCapability = (& $PortablePython -I -X utf8 -c $RuntimeProbe $Packages).Trim()
if ($LASTEXITCODE -ne 0 -or $GpuCapability -notmatch '^([0-9]+)\.([0-9]+)$') {
    throw "تعذر فحص Python/PyTorch/CUDA داخل النسخة المحمولة."
}
$GpuMajor = [int]$Matches[1]
$GpuMinor = [int]$Matches[2]
if ($GpuMajor -lt 7 -or ($GpuMajor -eq 7 -and $GpuMinor -lt 5)) {
    throw "قدرة CUDA $GpuCapability أقدم من ABI المحرك المدعوم."
}
$env:TORCH_CUDA_ARCH_LIST = if (
    $GpuMajor -gt 8 -or ($GpuMajor -eq 8 -and $GpuMinor -gt 9)
) {
    "8.9+PTX"
} else {
    $GpuCapability
}
Write-Host "Python/PyTorch/CUDA: OK — GPU capability $GpuCapability" `
    -ForegroundColor Green
Write-Host "MSVC 14.29 + CUDA 12.4: OK" -ForegroundColor Green

$AcceptanceProbe = @'
import importlib.util
import os
import pathlib
import sys

worker_path = pathlib.Path(sys.argv[1]).resolve(strict=True)
expected = (
    pathlib.Path(os.environ["LOCALAPPDATA"])
    / "MujassamAI" / "Licenses"
    / "acceptance-v2-1.txt"
).resolve(strict=True)
spec = importlib.util.spec_from_file_location(
    "mujassam_hy21_installed_acceptance", worker_path
)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load the installed Hunyuan3D-2.1 worker")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
validated = module._validate_license_acceptance().resolve(strict=True)
if validated != expected:
    raise RuntimeError(f"acceptance resolved to an unexpected path: {validated}")
print("MJHUNYUAN21ACCEPTANCE|OK|1")
'@
# HOTFIX_REUSE_ACCEPTANCE: this calls the installed worker's complete,
# fail-closed validation of exact version/commit/license hash, Territory,
# personal-local scope, non-distribution, acknowledgement lines and timestamp.
& $PortablePython -I -X utf8 -c $AcceptanceProbe $InstalledWorker
if ($LASTEXITCODE -ne 0) {
    throw (
        "موافقة Hunyuan3D-2.1 الحالية غير صالحة. افتح Mujassam AI مرة " +
        "واحدة وجدّد الموافقة، ثم أعد تشغيل الإصلاح."
    )
}

$TemporaryRoot = Join-Path $env:TEMP (
    $TemporaryPrefix + [Guid]::NewGuid().ToString("N"))
Assert-SafeOwnedTemporaryDirectory $TemporaryRoot
[IO.Directory]::CreateDirectory($TemporaryRoot) | Out-Null
$SourceRoot = Join-Path $TemporaryRoot "source"
$SourceArchive = Join-Path $TemporaryRoot "source.zip"
$BuildTemp = Join-Path $TemporaryRoot "build"
$BuildLib = Join-Path $TemporaryRoot "out"
$env:TORCH_EXTENSIONS_DIR = Join-Path $TemporaryRoot "torch-extensions"
$OwnedHooksDirectory = Join-Path $TemporaryRoot "empty-git-hooks"
$OwnedGitConfig = Join-Path $TemporaryRoot "empty-gitconfig"
[IO.Directory]::CreateDirectory($OwnedHooksDirectory) | Out-Null
[IO.File]::WriteAllText(
    $OwnedGitConfig, "", [Text.UTF8Encoding]::new($false))
Assert-NormalDirectory $OwnedHooksDirectory "مجلد Git hooks الفارغ"
Assert-NormalFile $OwnedGitConfig "ملف Git config الفارغ"
$PreviousGitLfsSkipSmudge = $env:GIT_LFS_SKIP_SMUDGE
$PreviousGitNoReplaceObjects = $env:GIT_NO_REPLACE_OBJECTS
$PreviousGitConfigGlobal = $env:GIT_CONFIG_GLOBAL
$PreviousGitConfigNoSystem = $env:GIT_CONFIG_NOSYSTEM
# HOTFIX_GIT_TRUST: source operations ignore replace/graft substitution,
# system/global URL rewrites and every Git hook.  The pinned commit and license
# hash remain independent post-fetch trust checks.
$env:GIT_LFS_SKIP_SMUDGE = "1"
$env:GIT_NO_REPLACE_OBJECTS = "1"
$env:GIT_CONFIG_GLOBAL = $OwnedGitConfig
$env:GIT_CONFIG_NOSYSTEM = "1"

try {
    $ReusedSource = Export-ExistingPinnedSource `
        $Git $SourceRoot $SourceArchive $OwnedHooksDirectory
    if (-not $ReusedSource) {
        Fetch-PinnedSparseSource $Git $SourceRoot $OwnedHooksDirectory
    }

    $OfficialLicense = Join-Path $SourceRoot "LICENSE"
    Assert-NormalFile $OfficialLicense "ترخيص المصدر الرسمي"
    if ((Get-Sha256 $OfficialLicense) -cne $OfficialLicenseBlobSha256) {
        throw "بصمة LICENSE في المصدر الرسمي المثبت لا تطابق commit المقفول."
    }
    if ((Get-NormalizedLegalText $PackagedLicense) -cne
        ((Get-NormalizedLegalText $OfficialLicense) + "`n")) {
        throw "الترخيص المثبت يختلف عن ترخيص المصدر الرسمي المقفول."
    }

    Write-Host "تطبيق إصلاح UInt64/Long على مصدر Windows rasterizer..."
    & $PortablePython -I -X utf8 $PatchScript source $SourceRoot
    if ($LASTEXITCODE -ne 0) {
        throw "فشل تطبيق إصلاح مصدر rasterizer."
    }
    # A second pass proves the exact-count patch is idempotent.
    & $PortablePython -I -X utf8 $PatchScript `
        rasterizer-runtime $SourceRoot
    if ($LASTEXITCODE -ne 0) {
        throw "فشل تحقق idempotency لإصلاح rasterizer."
    }

    [IO.Directory]::CreateDirectory($BuildTemp) | Out-Null
    [IO.Directory]::CreateDirectory($BuildLib) | Out-Null
    $RasterizerSource = Join-Path $SourceRoot `
        "hy3dpaint\custom_rasterizer"
    $SetupPy = Join-Path $RasterizerSource "setup.py"
    Assert-NormalFile $SetupPy "custom_rasterizer setup.py"
    $BuildDriver = @'
import pathlib
import runpy
import sys

portable_site = pathlib.Path(sys.argv[1]).resolve(strict=True)
packages = pathlib.Path(sys.argv[2]).resolve(strict=True)
setup_py = pathlib.Path(sys.argv[3]).resolve(strict=True)
sys.path[:0] = [str(portable_site), str(packages)]

import setuptools
import torch

loaded_setuptools = pathlib.Path(setuptools.__file__).resolve(strict=True)
loaded_torch = pathlib.Path(torch.__file__).resolve(strict=True)
if not loaded_setuptools.is_relative_to(portable_site):
    raise RuntimeError(f"build imported non-portable setuptools: {loaded_setuptools}")
if setuptools.__version__ != "69.5.1":
    raise RuntimeError(f"build imported unexpected setuptools: {setuptools.__version__}")
if not loaded_torch.is_relative_to(portable_site):
    raise RuntimeError(f"build imported non-portable torch: {loaded_torch}")
if torch.__version__ != "2.5.1+cu124" or str(torch.version.cuda) != "12.4":
    raise RuntimeError(f"build imported unexpected torch ABI: {torch.__version__}")
sys.argv = [str(setup_py), *sys.argv[4:]]
runpy.run_path(str(setup_py), run_name="__main__")
'@
    Write-Host "تجميع custom_rasterizer_kernel.pyd فقط (لا يوجد full build)..." `
        -ForegroundColor Cyan
    Push-Location $RasterizerSource
    try {
        & $BuildPython -I -X utf8 -c $BuildDriver `
            $PortableSitePackages $Packages $SetupPy build_ext `
            --build-temp $BuildTemp --build-lib $BuildLib
        if ($LASTEXITCODE -ne 0) {
            throw "فشل تجميع custom_rasterizer_kernel.pyd."
        }
    } finally {
        Pop-Location
    }

    $BuiltPyds = @(Get-ChildItem -LiteralPath $BuildLib -Recurse -File `
        -Filter "custom_rasterizer_kernel*.pyd")
    if ($BuiltPyds.Count -ne 1) {
        throw "التجميع لم ينتج ملف rasterizer واحدًا: $($BuiltPyds.Count)."
    }
    $BuiltPyd = $BuiltPyds[0].FullName
    if ($BuiltPyds[0].Name -cnotmatch `
        '^custom_rasterizer_kernel\.cp311-win_amd64\.pyd$') {
        throw "ملف rasterizer الناتج لا يطابق CPython 3.11 win_amd64 ABI."
    }
    $BuiltPydDirectory = [IO.Path]::GetDirectoryName($BuiltPyd)
    Invoke-RasterizerSmoke $PortablePython $Packages $BuiltPydDirectory

    $WorkerSyntaxCheck = @'
import pathlib
import sys
path = pathlib.Path(sys.argv[1]).resolve(strict=True)
compile(path.read_bytes(), str(path), "exec")
print("MJWORKERSYNTAX|OK|1")
'@
    & $PortablePython -I -X utf8 -c $WorkerSyntaxCheck $UpdatedWorker
    if ($LASTEXITCODE -ne 0) {
        throw "ملف worker الجديد غير صالح؛ لم يتغير التثبيت."
    }

    $LocalApplicationData = [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::LocalApplicationData)
    $BackupParent = Join-Path $LocalApplicationData "MujassamAI\Backups"
    Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupParent
    [IO.Directory]::CreateDirectory($BackupParent) | Out-Null
    Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupParent
    $BackupRoot = Join-Path $BackupParent (
        "hunyuan21-rasterizer-" +
        (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" +
        [Guid]::NewGuid().ToString("N").Substring(0, 8))
    [IO.Directory]::CreateDirectory($BackupRoot) | Out-Null
    Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupRoot
    $BackupPyd = Join-Path $BackupRoot $InstalledPyds[0].Name
    $BackupWorker = Join-Path $BackupRoot "hunyuan21_worker.py"
    Assert-MujassamProcessesStopped $InstallRoot
    [IO.File]::Copy($InstalledPyd, $BackupPyd, $false)
    [IO.File]::Copy($InstalledWorker, $BackupWorker, $false)
    $OriginalPydSha256 = Get-Sha256 $InstalledPyd
    $OriginalWorkerSha256 = Get-Sha256 $InstalledWorker
    if ((Get-Sha256 $BackupPyd) -cne $OriginalPydSha256 -or
        (Get-Sha256 $BackupWorker) -cne $OriginalWorkerSha256) {
        throw "فشل فحص النسخة الاحتياطية؛ لم يتغير التثبيت."
    }

    $BuiltPydSha256 = Get-Sha256 $BuiltPyd
    $UpdatedWorkerSha256 = Get-Sha256 $UpdatedWorker
    $Applied = [Collections.Generic.List[object]]::new()
    try {
        $Applied.Add([pscustomobject]@{
            Destination = $InstalledPyd
            Backup = $BackupPyd
            Sha256 = $OriginalPydSha256
        })
        Set-FileAtomically $BuiltPyd $InstalledPyd $BuiltPydSha256
        $Applied.Add([pscustomobject]@{
            Destination = $InstalledWorker
            Backup = $BackupWorker
            Sha256 = $OriginalWorkerSha256
        })
        Set-FileAtomically `
            $UpdatedWorker $InstalledWorker $UpdatedWorkerSha256

        # Test the installed location in a fresh process before declaring success.
        $InstalledPydDirectory = [IO.Path]::GetDirectoryName($InstalledPyd)
        Invoke-RasterizerSmoke `
            $PortablePython $Packages $InstalledPydDirectory
        & $PortablePython -I -X utf8 $InstalledWorker `
            --rasterizer-self-test --texture-mode native_2k
        if ($LASTEXITCODE -ne 0) {
            throw "فشل فحص worker النهائي؛ سيُسترجع الملفان السابقان."
        }
    } catch {
        $InstallFailure = $_
        Write-Warning "فشل التثبيت/الفحص؛ استرجاع الملفين السابقين."
        $RollbackErrors = [Collections.Generic.List[string]]::new()
        for ($Index = $Applied.Count - 1; $Index -ge 0; $Index--) {
            $Entry = $Applied[$Index]
            try {
                Set-FileAtomically `
                    $Entry.Backup $Entry.Destination $Entry.Sha256
            } catch {
                $RollbackErrors.Add($_.Exception.Message)
            }
        }
        if ($RollbackErrors.Count -ne 0) {
            throw (
                "$($InstallFailure.Exception.Message) | فشل الاسترجاع: " +
                ($RollbackErrors -join " | ") + " | النسخة: $BackupRoot"
            )
        }
        throw $InstallFailure
    }

    $Receipt = [ordered]@{
        schema_version = 1
        completed_utc = (Get-Date).ToUniversalTime().ToString("o")
        source_commit = $SourceCommit
        install_root = $InstallRoot
        old_rasterizer_sha256 = $OriginalPydSha256
        new_rasterizer_sha256 = $BuiltPydSha256
        old_worker_sha256 = $OriginalWorkerSha256
        new_worker_sha256 = $UpdatedWorkerSha256
        gpu_smoke = "MJRASTERHOTFIXSMOKE|OK|1"
        model_download = $false
        full_build = $false
    }
    # HOTFIX_NONFATAL_RECEIPT: the verified transaction is already complete;
    # an AV/indexer lock on this optional receipt must not report a false failure.
    try {
        [IO.File]::WriteAllText(
            (Join-Path $BackupRoot "hotfix-receipt.json"),
            ($Receipt | ConvertTo-Json -Depth 4),
            [Text.UTF8Encoding]::new($false))
    } catch {
        Write-Warning (
            "اكتمل الإصلاح لكن تعذر كتابة إيصال النسخة الاحتياطية: " +
            $_.Exception.Message
        )
    }

    Write-Host ""
    Write-Host "تم إصلاح Rasterizer واختباره على CUDA بنجاح." `
        -ForegroundColor Green
    Write-Host "لم تُنزّل أي نماذج ولم يُعد بناء البرنامج؛ جُمّع ملف .pyd واحد فقط."
    Write-Host "النسخة الاحتياطية: $BackupRoot"
    Write-Host "افتح Mujassam AI وجرّب إنشاء المجسم الآن."
} finally {
    if ($null -eq $PreviousGitLfsSkipSmudge) {
        Remove-Item Env:GIT_LFS_SKIP_SMUDGE -ErrorAction SilentlyContinue
    } else {
        $env:GIT_LFS_SKIP_SMUDGE = $PreviousGitLfsSkipSmudge
    }
    if ($null -eq $PreviousGitNoReplaceObjects) {
        Remove-Item Env:GIT_NO_REPLACE_OBJECTS -ErrorAction SilentlyContinue
    } else {
        $env:GIT_NO_REPLACE_OBJECTS = $PreviousGitNoReplaceObjects
    }
    if ($null -eq $PreviousGitConfigGlobal) {
        Remove-Item Env:GIT_CONFIG_GLOBAL -ErrorAction SilentlyContinue
    } else {
        $env:GIT_CONFIG_GLOBAL = $PreviousGitConfigGlobal
    }
    if ($null -eq $PreviousGitConfigNoSystem) {
        Remove-Item Env:GIT_CONFIG_NOSYSTEM -ErrorAction SilentlyContinue
    } else {
        $env:GIT_CONFIG_NOSYSTEM = $PreviousGitConfigNoSystem
    }
    # HOTFIX_BEST_EFFORT_CLEANUP: installation success must survive a temporary
    # compiler file held briefly by antivirus or the Windows indexer.
    try {
        Remove-OwnedTemporaryDirectory $TemporaryRoot
    } catch {
        Write-Warning (
            "تعذر تنظيف مجلد التجميع المؤقت؛ يمكن حذفه لاحقًا: " +
            "$TemporaryRoot ($($_.Exception.Message))"
        )
    }
}
