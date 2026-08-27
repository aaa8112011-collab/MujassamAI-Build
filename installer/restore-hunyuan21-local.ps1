[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$BackupRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-SafeRelativePath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.Contains([char]0)) {
        return $false
    }
    $Normalized = $Path.Replace('\', '/')
    if ($Normalized.StartsWith('/') -or $Normalized.Contains(':') -or
        $Normalized.Contains('//')) {
        return $false
    }
    $Parts = @($Normalized.Split('/'))
    foreach ($Part in $Parts) {
        if ([string]::IsNullOrWhiteSpace($Part) -or $Part -in @('.', '..') -or
            $Part.EndsWith(' ') -or $Part.EndsWith('.') -or
            $Part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') {
            return $false
        }
    }
    return $true
}

function Get-ContainedPath([string]$Root, [string]$Relative) {
    if (-not (Test-SafeRelativePath $Relative)) {
        throw "مسار غير آمن في إيصال النسخة الاحتياطية: $Relative"
    }
    $RootWithSeparator = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $Candidate = [IO.Path]::GetFullPath((Join-Path $RootWithSeparator `
        $Relative.Replace('/', '\')))
    if (-not $Candidate.StartsWith(
        $RootWithSeparator, [StringComparison]::OrdinalIgnoreCase)) {
        throw "مسار خارج الجذر: $Relative"
    }
    return $Candidate
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Remove-TemporaryFileBestEffort([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }
    try {
        if ([IO.File]::Exists($Path)) {
            [IO.File]::Delete($Path)
        }
    } catch {
        Write-Warning "تعذر حذف الملف المؤقت $Path`: $($_.Exception.Message)"
    }
}

function Assert-NoReparsePointInExistingPath([string]$Root, [string]$Candidate) {
    $RootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $CandidateFull = [IO.Path]::GetFullPath($Candidate)
    if (-not $CandidateFull.StartsWith(
        $RootFull + '\', [StringComparison]::OrdinalIgnoreCase) -and
        $CandidateFull -cne $RootFull) {
        throw "مسار الاسترجاع ليس داخل الجذر المحدد."
    }
    $Current = $RootFull
    $Relative = $CandidateFull.Substring($RootFull.Length).TrimStart('\')
    foreach ($Part in @($Relative.Split('\') | Where-Object { $_ -ne '' })) {
        $Current = Join-Path $Current $Part
        if (-not (Test-Path -LiteralPath $Current)) {
            break
        }
        $Item = Get-Item -LiteralPath $Current -Force
        if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "رفض reparse point داخل مسار الاسترجاع: $Current"
        }
    }
}

if ($PSVersionTable.PSVersion.Major -lt 7 -or -not $IsWindows) {
    throw "الاسترجاع يتطلب PowerShell 7 على Windows."
}
$BackupPath = [IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
if (-not (Test-Path -LiteralPath $BackupPath -PathType Container)) {
    throw "مجلد النسخة الاحتياطية غير موجود: $BackupPath"
}
$BackupItem = Get-Item -LiteralPath $BackupPath -Force
if ($BackupItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "مجلد النسخة الاحتياطية لا يجوز أن يكون reparse point."
}
$ReceiptPath = Join-Path $BackupPath "install-receipt.json"
if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
    throw "install-receipt.json غير موجود داخل النسخة الاحتياطية."
}
$Receipt = Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json
if ([int]$Receipt.schema_version -ne 1) {
    throw "إصدار إيصال النسخة الاحتياطية غير مدعوم."
}
$InstallRoot = [IO.Path]::GetFullPath([string]$Receipt.install_root).TrimEnd('\')
if (-not (Test-Path -LiteralPath $InstallRoot -PathType Container)) {
    throw "مجلد التثبيت الأصلي غير موجود: $InstallRoot"
}
$ForbiddenRoots = @(
    [IO.Path]::GetPathRoot($InstallRoot),
    [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\'),
    [IO.Path]::GetFullPath($env:WINDIR).TrimEnd('\')
)
foreach ($Forbidden in $ForbiddenRoots) {
    if ($InstallRoot -eq $Forbidden) {
        throw "مسار الاسترجاع واسع أو حساس أكثر من اللازم: $InstallRoot"
    }
}
$InstallItem = Get-Item -LiteralPath $InstallRoot -Force
if ($InstallItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "مجلد التثبيت لا يجوز أن يكون reparse point."
}
foreach ($RequiredBaseFile in @("MujassamAI.exe", "rt/python.exe")) {
    $RequiredPath = Get-ContainedPath $InstallRoot $RequiredBaseFile
    Assert-NoReparsePointInExistingPath $InstallRoot $RequiredPath
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        throw "هذه ليست نسخة MujassamAI-Portable قابلة للاسترجاع: $RequiredBaseFile"
    }
}
if (@(Get-Process -Name "MujassamAI" -ErrorAction SilentlyContinue).Count -ne 0) {
    throw "أغلق MujassamAI.exe بالكامل قبل الاسترجاع."
}

$Overwritten = @($Receipt.overwritten_files)
$Created = @($Receipt.created_files)
if ($Overwritten.Count + $Created.Count -gt 150000) {
    throw "إيصال النسخة الاحتياطية يحتوي ملفات أكثر من الحد الآمن."
}
$AllPaths = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase)
foreach ($Entry in $Overwritten) {
    $Relative = [string]$Entry.path
    if (-not (Test-SafeRelativePath $Relative) -or -not $AllPaths.Add($Relative)) {
        throw "مسار مكرر أو غير آمن في الإيصال: $Relative"
    }
    $ExpectedHash = [string]$Entry.sha256
    $InstalledHash = [string]$Entry.installed_sha256
    $BackupFile = Get-ContainedPath $BackupPath $Relative
    if ($ExpectedHash -notmatch '^[0-9a-f]{64}$' -or
        $InstalledHash -notmatch '^[0-9a-f]{64}$' -or
        -not (Test-Path -LiteralPath $BackupFile -PathType Leaf)) {
        throw "ملف احتياطي مفقود أو بصمته غير صالحة: $Relative"
    }
    $File = Get-Item -LiteralPath $BackupFile
    if ($File.Length -ne [Int64]$Entry.bytes -or
        (Get-Sha256 $BackupFile) -cne $ExpectedHash) {
        throw "فشل تحقق النسخة الاحتياطية: $Relative"
    }
    $Destination = Get-ContainedPath $InstallRoot $Relative
    Assert-NoReparsePointInExistingPath $InstallRoot $Destination
    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        throw "ملف النسخة المحمولة مفقود قبل الاسترجاع: $Relative"
    }
    $CurrentHash = Get-Sha256 $Destination
    if ($CurrentHash -cne $InstalledHash -and $CurrentHash -cne $ExpectedHash) {
        throw "تغيّر الملف بعد التثبيت؛ لن تتم الكتابة فوق تعديلك: $Relative"
    }
}
foreach ($Entry in $Created) {
    $Relative = [string]$Entry.path
    $InstalledHash = [string]$Entry.installed_sha256
    if (-not (Test-SafeRelativePath $Relative) -or -not $AllPaths.Add($Relative)) {
        throw "مسار مكرر أو غير آمن في الإيصال: $Relative"
    }
    if ($InstalledHash -notmatch '^[0-9a-f]{64}$') {
        throw "بصمة الملف المضاف غير صالحة في الإيصال: $Relative"
    }
    $Destination = Get-ContainedPath $InstallRoot $Relative
    Assert-NoReparsePointInExistingPath $InstallRoot $Destination
    if (Test-Path -LiteralPath $Destination) {
        if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
            (Get-Sha256 $Destination) -cne $InstalledHash) {
            throw "تغيّر الملف المضاف بعد التثبيت؛ لن يتم حذفه: $Relative"
        }
    }
}

if (-not $PSCmdlet.ShouldProcess(
    $InstallRoot,
    "استرجاع $($Overwritten.Count) ملفًا وحذف $($Created.Count) ملفًا أضافه التحديث")) {
    return
}

try {
    foreach ($Entry in $Overwritten) {
        $Relative = [string]$Entry.path
        $BackupFile = Get-ContainedPath $BackupPath $Relative
        $Destination = Get-ContainedPath $InstallRoot $Relative
        Assert-NoReparsePointInExistingPath $InstallRoot $Destination
        $CurrentHash = Get-Sha256 $Destination
        if ($CurrentHash -ceq [string]$Entry.sha256) {
            continue
        }
        if ($CurrentHash -cne [string]$Entry.installed_sha256) {
            throw "تغيّر الملف أثناء الاسترجاع؛ لم تتم الكتابة فوقه: $Relative"
        }
        $Parent = [IO.Path]::GetDirectoryName($Destination)
        [IO.Directory]::CreateDirectory($Parent) | Out-Null
        Assert-NoReparsePointInExistingPath $InstallRoot $Parent
        $TemporaryDestination = Join-Path $Parent (
            "MujassamAI-restore-" + [Guid]::NewGuid().ToString("N") + ".tmp")
        [IO.File]::Copy($BackupFile, $TemporaryDestination, $false)
        $RestoreReplacementBackup = $null
        $RestoreSucceeded = $false
        try {
            if ((Get-Sha256 $TemporaryDestination) -cne [string]$Entry.sha256) {
                throw "فشل تحقق ملف الاسترجاع المؤقت: $Relative"
            }
            Assert-NoReparsePointInExistingPath $InstallRoot $Destination
            if ((Get-Sha256 $Destination) -cne [string]$Entry.installed_sha256) {
                throw "تغيّر الملف قبل الاستبدال مباشرة: $Relative"
            }
            $RestoreReplacementBackup = Join-Path $Parent (
                "MujassamAI-restore-replaced-" +
                [Guid]::NewGuid().ToString("N") + ".tmp")
            [IO.File]::Replace(
                $TemporaryDestination, $Destination,
                $RestoreReplacementBackup, $true)
            if ((Get-Sha256 $Destination) -cne [string]$Entry.sha256) {
                throw "فشل فحص الملف مباشرة بعد الاسترجاع: $Relative"
            }
            $RestoreSucceeded = $true
        } finally {
            Remove-TemporaryFileBestEffort $TemporaryDestination
            if ($RestoreSucceeded) {
                Remove-TemporaryFileBestEffort $RestoreReplacementBackup
            }
        }
    }
    foreach ($Entry in $Created) {
        $Destination = Get-ContainedPath $InstallRoot ([string]$Entry.path)
        Assert-NoReparsePointInExistingPath $InstallRoot $Destination
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            if ((Get-Sha256 $Destination) -cne [string]$Entry.installed_sha256) {
                throw "تغيّر الملف قبل الحذف مباشرة: $($Entry.path)"
            }
            [IO.File]::Delete($Destination)
        }
    }
    foreach ($Entry in $Overwritten) {
        $Destination = Get-ContainedPath $InstallRoot ([string]$Entry.path)
        if ((Get-Sha256 $Destination) -cne [string]$Entry.sha256) {
            throw "فشل فحص الملف بعد الاسترجاع: $($Entry.path)"
        }
    }
    foreach ($Entry in $Created) {
        $Destination = Get-ContainedPath $InstallRoot ([string]$Entry.path)
        if (Test-Path -LiteralPath $Destination) {
            throw "فشل حذف الملف الذي أضافه التحديث: $($Entry.path)"
        }
    }
} catch {
    throw (
        "توقف الاسترجاع وقد تكون النسخة في حالة جزئية. لا تشغّل البرنامج. " +
        "أعد تشغيل نفس أمر الاسترجاع؛ العملية قابلة للاستكمال. السبب: " +
        $_.Exception.Message
    )
}
Write-Host "تم استرجاع ملفات النسخة السابقة." -ForegroundColor Green
Write-Host "أُبقي مجلد النسخة الاحتياطية دون حذف: $BackupPath"
