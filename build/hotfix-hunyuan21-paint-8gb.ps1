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

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
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
    if (Test-Path -LiteralPath $Current) {
        $RootItem = Get-Item -LiteralPath $Current -Force
        if ($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "رفض reparse point داخل المسار: $Current"
        }
    }
    foreach ($Part in @(
        $CandidateFull.Substring($RootFull.Length).TrimStart('\').Split('\') |
            Where-Object { $_ -ne "" }
    )) {
        $Current = Join-Path $Current $Part
        if (Test-Path -LiteralPath $Current) {
            $CurrentItem = Get-Item -LiteralPath $Current -Force
            if ($CurrentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "رفض reparse point داخل المسار: $Current"
            }
        }
    }
}

function Assert-MujassamProcessesStopped([string]$Root) {
    # PAINT_HOTFIX_PROCESS_RECHECK: invoked before backup and directly inside
    # the atomic helper before every replacement, including either rollback.
    $RunningApplication = @(Get-Process -Name "MujassamAI" `
        -ErrorAction SilentlyContinue)
    $RunningPortablePython = @(
        foreach ($ProcessName in @("python", "pythonw")) {
            Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
                Where-Object {
                    try {
                        $_.Path.StartsWith(
                            $Root + '\',
                            [StringComparison]::OrdinalIgnoreCase)
                    } catch {
                        $false
                    }
                }
        }
    )
    if ($RunningApplication.Count -ne 0 -or
        $RunningPortablePython.Count -ne 0) {
        throw "أغلق Mujassam AI وانتظر توقف عمليته، ثم شغّل الإصلاح مرة أخرى."
    }
}

function Set-FileAtomically(
    [string]$Source,
    [string]$Destination,
    [string]$ExpectedSha256,
    [string]$InstallRoot
) {
    # PAINT_HOTFIX_SAFE_REPLACE: candidate and replacement backup are beside
    # the installed worker, keeping File.Replace atomic on one volume.
    Assert-NormalFile $Source "ملف worker المصدر"
    Assert-NormalFile $Destination "ملف worker المثبت"
    $Parent = [IO.Path]::GetDirectoryName($Destination)
    Assert-NormalDirectory $Parent "مجلد worker المثبت"
    $Candidate = Join-Path $Parent (
        "MujassamAI-worker-new-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $ReplacementBackup = Join-Path $Parent (
        "MujassamAI-worker-replaced-" +
        [Guid]::NewGuid().ToString("N") + ".tmp")
    $OriginalSha256 = Get-Sha256 $Destination
    $Replaced = $false
    try {
        [IO.File]::Copy($Source, $Candidate, $false)
        Assert-NormalFile $Candidate "نسخة worker المؤقتة"
        if ((Get-Sha256 $Candidate) -cne $ExpectedSha256) {
            throw "فشل فحص worker المؤقت قبل الاستبدال."
        }
        Assert-MujassamProcessesStopped $InstallRoot
        [IO.File]::Replace(
            $Candidate, $Destination, $ReplacementBackup, $true)
        $Replaced = $true
        Assert-NormalFile $Destination "ملف worker المثبت"
        if ((Get-Sha256 $Destination) -cne $ExpectedSha256) {
            throw "فشل فحص worker بعد الاستبدال."
        }
    } catch {
        $ReplaceFailure = $_
        if ($Replaced -and
            (Test-Path -LiteralPath $ReplacementBackup -PathType Leaf)) {
            try {
                Assert-MujassamProcessesStopped $InstallRoot
                [IO.File]::Replace(
                    $ReplacementBackup, $Destination, $Candidate, $true)
                if ((Get-Sha256 $Destination) -cne $OriginalSha256) {
                    throw "بصمة worker المسترجع غير صحيحة."
                }
            } catch {
                throw (
                    "$($ReplaceFailure.Exception.Message) | " +
                    "فشل الاسترجاع الداخلي: $($_.Exception.Message)"
                )
            }
        }
        throw $ReplaceFailure
    } finally {
        foreach ($Temporary in @($Candidate, $ReplacementBackup)) {
            if (Test-Path -LiteralPath $Temporary -PathType Leaf) {
                try {
                    [IO.File]::Delete($Temporary)
                } catch {
                    # PAINT_HOTFIX_BEST_EFFORT_CLEANUP: an antivirus lock on a
                    # disposable sidecar must not turn a verified update into
                    # a false failure.
                    Write-Warning (
                        "اكتمل الاستبدال لكن تعذر تنظيف ملف مؤقت: " +
                        "$Temporary ($($_.Exception.Message))"
                    )
                }
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
$InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$UpdatedWorker = Join-Path $RepositoryRoot `
    "app\engines\hunyuan21\hunyuan21_worker.py"
$EngineRoot = Join-Path $InstallRoot "app\engines\hunyuan21"
$InstalledWorker = Join-Path $EngineRoot "hunyuan21_worker.py"
$PortablePython = Join-Path $InstallRoot "rt\python.exe"

Assert-NormalDirectory $RepositoryRoot "مجلد مستودع MujassamAI"
Assert-NoReparsePointInExistingPath $RepositoryRoot $UpdatedWorker
Assert-NormalFile $UpdatedWorker "ملف worker الجديد"
Assert-NormalDirectory $InstallRoot "مجلد MujassamAI-Portable"
foreach ($InstalledPath in @(
    $EngineRoot, $InstalledWorker, $PortablePython
)) {
    Assert-NoReparsePointInExistingPath $InstallRoot $InstalledPath
}
Assert-NormalDirectory $EngineRoot "مجلد Hunyuan3D-2.1 المثبت"
Assert-NormalFile $InstalledWorker "ملف worker المثبت"
Assert-NormalFile $PortablePython "Python المحمول"

Write-Host "فحص ملف Paint/worker الجديد..." -ForegroundColor Cyan
$WorkerSyntaxCheck = @'
import pathlib
import sys

path = pathlib.Path(sys.argv[1]).resolve(strict=True)
compile(path.read_bytes(), str(path), "exec")
print("MJPAINTWORKERSYNTAX|OK|1")
'@
& $PortablePython -I -X utf8 -c $WorkerSyntaxCheck $UpdatedWorker
if ($LASTEXITCODE -ne 0) {
    throw "ملف worker الجديد غير صالح؛ لم يتغير التثبيت."
}

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
    raise RuntimeError("cannot load installed Hunyuan3D-2.1 worker")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
validated = module._validate_license_acceptance().resolve(strict=True)
if validated != expected:
    raise RuntimeError(f"acceptance resolved to unexpected path: {validated}")
print("MJHUNYUAN21ACCEPTANCE|OK|1")
'@
# PAINT_HOTFIX_REUSE_ACCEPTANCE: يعيد استخدام تحقق الموافقة الصارم داخل
# worker المثبت؛ لا يوجد أي prompt.
& $PortablePython -I -X utf8 -c $AcceptanceProbe $InstalledWorker
if ($LASTEXITCODE -ne 0) {
    throw (
        "موافقة Hunyuan3D-2.1 الحالية غير صالحة. افتح Mujassam AI مرة " +
        "واحدة وجدّد الموافقة، ثم أغلقه وأعد تشغيل الإصلاح."
    )
}

$LocalApplicationData = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::LocalApplicationData)
Assert-NormalDirectory $LocalApplicationData "LocalAppData"
$BackupParent = Join-Path $LocalApplicationData "MujassamAI\Backups"
Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupParent
[IO.Directory]::CreateDirectory($BackupParent) | Out-Null
Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupParent
$BackupRoot = Join-Path $BackupParent (
    "hunyuan21-paint-8gb-" +
    (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-" +
    [Guid]::NewGuid().ToString("N").Substring(0, 8))
[IO.Directory]::CreateDirectory($BackupRoot) | Out-Null
Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupRoot
Assert-NormalDirectory $BackupRoot "مجلد النسخة الاحتياطية"
$BackupWorker = Join-Path $BackupRoot "hunyuan21_worker.py"

# الفحص الأول مباشرة قبل إنشاء النسخة الاحتياطية.
Assert-MujassamProcessesStopped $InstallRoot
$OriginalWorkerSha256 = Get-Sha256 $InstalledWorker
[IO.File]::Copy($InstalledWorker, $BackupWorker, $false)
Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupWorker
Assert-NormalFile $BackupWorker "نسخة worker الاحتياطية"
if ((Get-Sha256 $BackupWorker) -cne $OriginalWorkerSha256) {
    throw "فشل فحص النسخة الاحتياطية؛ لم يتغير التثبيت."
}

$UpdatedWorkerSha256 = Get-Sha256 $UpdatedWorker
$UpdatedWorkerText = [IO.File]::ReadAllText($UpdatedWorker)
if (-not $UpdatedWorkerText.Contains("def _install_low_vram_paint_runtime(") -or
    -not $UpdatedWorkerText.Contains("def _install_streaming_pbr_bake(")) {
    throw "المستودع لا يحتوي إصلاح Paint 8GB المطلوب؛ حدّث المستودع ثم أعد المحاولة."
}
$SupportsSelfTest = $UpdatedWorkerText.Contains('"--self-test"') -or `
    $UpdatedWorkerText.Contains("'--self-test'")

try {
    Set-FileAtomically `
        $UpdatedWorker $InstalledWorker $UpdatedWorkerSha256 $InstallRoot

    Write-Host "تشغيل فحص CUDA السريع من worker المثبت..." `
        -ForegroundColor Cyan
    & $PortablePython -I -X utf8 $InstalledWorker `
        --rasterizer-self-test --texture-mode native_2k
    if ($LASTEXITCODE -ne 0) {
        throw "فشل فحص CUDA بعد التحديث."
    }

    if ($SupportsSelfTest) {
        Write-Host "تشغيل الفحص الذاتي للـworker المثبت..." `
            -ForegroundColor Cyan
        & $PortablePython -I -X utf8 $InstalledWorker --self-test
        if ($LASTEXITCODE -ne 0) {
            throw "فشل الفحص الذاتي للـworker بعد التحديث."
        }
    }

    if ((Get-Sha256 $InstalledWorker) -cne $UpdatedWorkerSha256) {
        throw "تغيّرت بصمة worker المثبت أثناء الفحص."
    }
} catch {
    $InstallFailure = $_
    Write-Warning "فشل التحديث أو الفحص؛ استرجاع worker السابق الآن."
    try {
        Set-FileAtomically `
            $BackupWorker $InstalledWorker $OriginalWorkerSha256 $InstallRoot
        if ((Get-Sha256 $InstalledWorker) -cne $OriginalWorkerSha256) {
            throw "بصمة worker المسترجع لا تطابق النسخة الاحتياطية."
        }
    } catch {
        throw (
            "$($InstallFailure.Exception.Message) | فشل الاسترجاع: " +
            "$($_.Exception.Message) | النسخة الاحتياطية: $BackupRoot"
        )
    }
    throw $InstallFailure
}

Write-Host ""
Write-Host "تم تثبيت إصلاح Paint لجهاز 8GB واختباره بنجاح." `
    -ForegroundColor Green
Write-Host "لم تُنزّل أي ملفات ولم تُمس النماذج ولم يُعد بناء البرنامج."
Write-Host "تم تحديث worker واحد فقط. النسخة الاحتياطية: $BackupRoot"
Write-Host "افتح Mujassam AI وأعد نفس المحاولة؛ سيستخدم Shape المحفوظ."
