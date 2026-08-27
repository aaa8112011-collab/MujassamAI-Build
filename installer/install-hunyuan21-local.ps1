[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$UpdateZip,

    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedSha256,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$InstallRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ExpectedProduct = "Mujassam AI Hunyuan3D-2.1 Shape + PBR engine"
$ExpectedUpstreamCommit = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
$MaximumFiles = 150000
$MaximumExpandedBytes = 20GB
$MaximumManifestBytes = 5MB

function Test-SafeRelativePath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.Contains([char]0)) {
        return $false
    }
    $Normalized = $Path.Replace('\', '/')
    if ($Normalized.StartsWith('/') -or $Normalized.Contains(':') -or
        $Normalized.Contains('//')) {
        return $false
    }
    $Parts = @($Normalized.Split('/') | Where-Object { $_ -ne '' })
    if ($Parts.Count -eq 0) {
        return $false
    }
    foreach ($Part in $Parts) {
        if ($Part -in @('.', '..') -or
            $Part.EndsWith(' ') -or $Part.EndsWith('.') -or
            $Part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') {
            return $false
        }
    }
    return $true
}

function Get-ContainedPath([string]$Root, [string]$Relative) {
    if (-not (Test-SafeRelativePath $Relative)) {
        throw "مسار غير آمن داخل التحديث: $Relative"
    }
    $RootWithSeparator = [IO.Path]::GetFullPath($Root).TrimEnd(
        [IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    $NativeRelative = $Relative.Replace('/', [IO.Path]::DirectorySeparatorChar)
    $Candidate = [IO.Path]::GetFullPath((Join-Path $RootWithSeparator $NativeRelative))
    if (-not $Candidate.StartsWith(
        $RootWithSeparator, [StringComparison]::OrdinalIgnoreCase)) {
        throw "محاولة خروج من مجلد التثبيت: $Relative"
    }
    return $Candidate
}

function Assert-NoReparsePointInExistingPath([string]$Root, [string]$Candidate) {
    $RootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')
    $CandidateFull = [IO.Path]::GetFullPath($Candidate)
    if (-not $CandidateFull.StartsWith(
        $RootFull + '\', [StringComparison]::OrdinalIgnoreCase) -and
        $CandidateFull -cne $RootFull) {
        throw "مسار التثبيت ليس داخل الجذر المحدد."
    }
    $Current = $RootFull
    if (Test-Path -LiteralPath $Current) {
        $Item = Get-Item -LiteralPath $Current -Force
        if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "رفض reparse point داخل مسار التثبيت: $Current"
        }
    }
    $Relative = $CandidateFull.Substring($RootFull.Length).TrimStart('\')
    foreach ($Part in @($Relative.Split('\') | Where-Object { $_ -ne '' })) {
        $Current = Join-Path $Current $Part
        if (-not (Test-Path -LiteralPath $Current)) {
            break
        }
        $Item = Get-Item -LiteralPath $Current -Force
        if ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "رفض reparse point داخل مسار التثبيت: $Current"
        }
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-NormalDirectory([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label غير موجود: $Path"
    }
    $Item = Get-Item -LiteralPath $Path -Force
    if (-not $Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Label يجب أن يكون مجلدًا عاديًا وليس reparse point: $Path"
    }
}

function Assert-WritableDirectory([string]$Path, [string]$Label) {
    Assert-NormalDirectory $Path $Label
    $ProbePath = Join-Path $Path (
        "MujassamAI-write-probe-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    $ProbeStream = $null
    try {
        $ProbeStream = [IO.File]::Open(
            $ProbePath, [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write, [IO.FileShare]::None)
        $ProbeStream.WriteByte(0)
    } catch {
        throw "$Label غير قابل للكتابة: $Path. $($_.Exception.Message)"
    } finally {
        if ($null -ne $ProbeStream) {
            $ProbeStream.Dispose()
        }
        if (Test-Path -LiteralPath $ProbePath -PathType Leaf) {
            [IO.File]::Delete($ProbePath)
        }
    }
}

function Remove-SafeStagingDirectory([string]$Path, [string]$TemporaryRoot) {
    $TemporaryRootFull = [IO.Path]::GetFullPath($TemporaryRoot).TrimEnd('\')
    $PathFull = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not [string]::Equals(
        [IO.Path]::GetDirectoryName($PathFull), $TemporaryRootFull,
        [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($PathFull) -notmatch
            '^MujassamAI-hy21-install-[0-9a-f]{32}$') {
        throw "رُفض تنظيف مسار تجهيز غير متوقع: $PathFull"
    }
    if (-not (Test-Path -LiteralPath $PathFull)) {
        return
    }
    Assert-NormalDirectory $PathFull "مجلد تجهيز المثبّت"
    $UnsafeItems = @(Get-ChildItem -LiteralPath $PathFull -Recurse -Force |
        Where-Object {
            $_.Attributes -band [IO.FileAttributes]::ReparsePoint
        })
    if ($UnsafeItems.Count -ne 0) {
        throw "رُفض تنظيف مجلد تجهيز يحتوي reparse point: $($UnsafeItems[0].FullName)"
    }
    [IO.Directory]::Delete($PathFull, $true)
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

if ($PSVersionTable.PSVersion.Major -lt 7 -or -not $IsWindows -or
    -not [Environment]::Is64BitOperatingSystem -or
    -not [Environment]::Is64BitProcess) {
    throw "المثبّت المحلي يتطلب PowerShell 7 على Windows x64."
}

$ZipPath = [IO.Path]::GetFullPath($UpdateZip)
$RootPath = [IO.Path]::GetFullPath($InstallRoot).TrimEnd(
    [IO.Path]::DirectorySeparatorChar)
if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
    throw "ملف ZIP غير موجود: $ZipPath"
}
if (-not (Test-Path -LiteralPath $RootPath -PathType Container)) {
    throw "مجلد MujassamAI-Portable غير موجود: $RootPath"
}
$RootItem = Get-Item -LiteralPath $RootPath -Force
if ($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "مجلد التثبيت لا يجوز أن يكون reparse point."
}
$ForbiddenRoots = @(
    [IO.Path]::GetPathRoot($RootPath),
    [IO.Path]::GetFullPath($env:USERPROFILE).TrimEnd('\'),
    [IO.Path]::GetFullPath($env:WINDIR).TrimEnd('\')
)
foreach ($Forbidden in $ForbiddenRoots) {
    if ($RootPath -eq $Forbidden) {
        throw "مسار التثبيت واسع أو حساس أكثر من اللازم: $RootPath"
    }
}

$RequiredBaseFiles = @(
    "MujassamAI.exe", "app/worker.py", "rt/python.exe"
)
foreach ($Relative in $RequiredBaseFiles) {
    $Path = Get-ContainedPath $RootPath $Relative
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "هذه ليست نسخة MujassamAI-Portable كاملة؛ الملف مفقود: $Relative"
    }
}

$RunningMujassam = @(Get-Process -Name "MujassamAI" -ErrorAction SilentlyContinue)
if ($RunningMujassam.Count -ne 0) {
    throw "أغلق MujassamAI.exe بالكامل ثم أعد التثبيت."
}
Assert-WritableDirectory $RootPath "مجلد MujassamAI-Portable"

$BasePython = Get-ContainedPath $RootPath "rt/python.exe"
$AbiProbeText = & $BasePython -I -X utf8 -c `
    'import json,platform,sys,torch,torchvision; print(json.dumps({"python": platform.python_version(), "bits": 64 if sys.maxsize > 2**32 else 32, "torch": torch.__version__, "torchvision": torchvision.__version__, "machine": platform.machine()}))'
if ($LASTEXITCODE -ne 0) {
    throw "تعذر فحص Python/PyTorch في النسخة المحمولة الأساسية."
}
$Abi = $AbiProbeText | ConvertFrom-Json
if ([string]$Abi.python -cne "3.11.9" -or [int]$Abi.bits -ne 64 -or
    [string]$Abi.torch -cne "2.5.1+cu124" -or
    [string]$Abi.torchvision -cne "0.20.1+cu124" -or
    [string]$Abi.machine -notin @("AMD64", "x86_64")) {
    throw (
        "ABI النسخة الأساسية غير مطابق. المطلوب Python 3.11.9 x64، " +
        "torch 2.5.1+cu124، torchvision 0.20.1+cu124."
    )
}
Write-Host "ABI النسخة الأساسية: OK" -ForegroundColor Green

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$ArchiveSha256 = Get-Sha256 $ZipPath
if ($ArchiveSha256 -cne $ExpectedSha256.ToLowerInvariant()) {
    throw "بصمة ZIP لا تطابق SHA-256 الموثوق الذي مرره المشغّل."
}
$InstallerTemporaryRoot = [IO.Path]::GetFullPath(
    [IO.Path]::GetTempPath()).TrimEnd('\')
Assert-WritableDirectory $InstallerTemporaryRoot "مجلد Windows المؤقت"
$StagingRoot = Join-Path $InstallerTemporaryRoot (
    "MujassamAI-hy21-install-" + [Guid]::NewGuid().ToString("N"))
if (-not [string]::Equals(
    [IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($StagingRoot)),
    $InstallerTemporaryRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "مسار تجهيز المثبّت ليس داخل مجلد Windows المؤقت."
}
if (Test-Path -LiteralPath $StagingRoot) {
    throw "مسار تجهيز المثبّت موجود مسبقًا بصورة غير متوقعة: $StagingRoot"
}
$Archive = [IO.Compression.ZipFile]::OpenRead($ZipPath)
$StagingCreated = $false
$ExtractionSucceeded = $false
try {
    if ($Archive.Entries.Count -eq 0 -or $Archive.Entries.Count -gt $MaximumFiles) {
        throw "عدد الملفات داخل ZIP غير منطقي أو يتجاوز الحد الآمن."
    }
    $Seen = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    [Int64]$ExpandedBytes = 0
    $ManifestEntry = $null
    foreach ($Entry in $Archive.Entries) {
        $EntryPath = $Entry.FullName.Replace('\', '/').TrimEnd('/')
        if ([string]::IsNullOrWhiteSpace($EntryPath)) {
            continue
        }
        if (-not (Test-SafeRelativePath $EntryPath)) {
            throw "مسار ZIP غير آمن: $($Entry.FullName)"
        }
        if (-not $Seen.Add($EntryPath)) {
            throw "مسار مكرر داخل ZIP: $EntryPath"
        }
        $UnixMode = (($Entry.ExternalAttributes -shr 16) -band 0xF000)
        if ($UnixMode -eq 0xA000) {
            throw "الروابط الرمزية غير مسموحة داخل ZIP: $EntryPath"
        }
        $ExpandedBytes += [Int64]$Entry.Length
        if ($ExpandedBytes -gt $MaximumExpandedBytes) {
            throw "حجم ZIP بعد الفك يتجاوز الحد الآمن."
        }
        if ($Entry.Length -gt 100MB -and $Entry.CompressedLength -gt 0 -and
            ($Entry.Length / $Entry.CompressedLength) -gt 5000) {
            throw "نسبة ضغط غير آمنة داخل ZIP: $EntryPath"
        }
        if ($EntryPath -ceq "update-manifest.json") {
            if ($Entry.Length -gt $MaximumManifestBytes) {
                throw "update-manifest.json أكبر من الحد الآمن."
            }
            $ManifestEntry = $Entry
        }
    }
    if ($null -eq $ManifestEntry) {
        throw "ZIP لا يحتوي update-manifest.json في الجذر."
    }

    [IO.Directory]::CreateDirectory($StagingRoot) | Out-Null
    $StagingCreated = $true
    Assert-NoReparsePointInExistingPath $InstallerTemporaryRoot $StagingRoot
    Assert-WritableDirectory $StagingRoot "مجلد تجهيز المثبّت"
    foreach ($Entry in $Archive.Entries) {
        $EntryPath = $Entry.FullName.Replace('\', '/').TrimEnd('/')
        if ([string]::IsNullOrWhiteSpace($EntryPath)) {
            continue
        }
        $Destination = Get-ContainedPath $StagingRoot $EntryPath
        if ([string]::IsNullOrEmpty($Entry.Name)) {
            Assert-NoReparsePointInExistingPath $StagingRoot $Destination
            [IO.Directory]::CreateDirectory($Destination) | Out-Null
            Assert-NoReparsePointInExistingPath $StagingRoot $Destination
            continue
        }
        $Parent = [IO.Path]::GetDirectoryName($Destination)
        Assert-NoReparsePointInExistingPath $StagingRoot $Parent
        [IO.Directory]::CreateDirectory($Parent) | Out-Null
        Assert-NoReparsePointInExistingPath $StagingRoot $Parent
        $Input = $Entry.Open()
        try {
            $Output = [IO.File]::Open(
                $Destination, [IO.FileMode]::CreateNew,
                [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $Input.CopyTo($Output)
            } finally {
                $Output.Dispose()
            }
        } finally {
            $Input.Dispose()
        }
    }
    $UnsafeStagingItems = @(Get-ChildItem -LiteralPath $StagingRoot -Recurse -Force |
        Where-Object {
            $_.Attributes -band [IO.FileAttributes]::ReparsePoint
        })
    if ($UnsafeStagingItems.Count -ne 0) {
        throw "مجلد التجهيز يحتوي reparse point غير مسموح."
    }
    $ExtractionSucceeded = $true
} finally {
    $Archive.Dispose()
    if (-not $ExtractionSucceeded -and $StagingCreated -and
        (Test-Path -LiteralPath $StagingRoot)) {
        try {
            Remove-SafeStagingDirectory $StagingRoot $InstallerTemporaryRoot
        } catch {
            Write-Warning "تعذر تنظيف مجلد التجهيز: $($_.Exception.Message)"
        }
    }
}

try {
    $ManifestPath = Join-Path $StagingRoot "update-manifest.json"
    $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    if ([int]$Manifest.schema_version -ne 1 -or
        [string]$Manifest.product -cne $ExpectedProduct -or
        [string]$Manifest.upstream_commit -cne $ExpectedUpstreamCommit) {
        throw "بيانات update-manifest.json لا تخص إصدار Hunyuan3D 2.1 المتوقع."
    }
    $ManifestPropertyNames = @($Manifest.PSObject.Properties.Name)
    foreach ($RequiredProperty in @(
        "usage_scope", "distribution_authorized", "provider_legal_name"
    )) {
        if ($RequiredProperty -cnotin $ManifestPropertyNames) {
            throw "manifest المحلي يفتقد حقل الأمان: $RequiredProperty"
        }
    }
    $ProviderLegalName = $Manifest.provider_legal_name
    if ([string]$Manifest.usage_scope -cne "personal_local_only" -or
        $Manifest.distribution_authorized -isnot [bool] -or
        [bool]$Manifest.distribution_authorized -ne $false -or
        ($null -ne $ProviderLegalName -and
            [string]$ProviderLegalName -cne "")) {
        throw (
            "رفض ZIP غير مخصص للاستخدام الشخصي المحلي: يجب أن يكون " +
            "usage_scope=personal_local_only وdistribution_authorized=false " +
            "وprovider_legal_name فارغًا."
        )
    }
    $ManifestFiles = @($Manifest.files)
    if ($ManifestFiles.Count -eq 0 -or $ManifestFiles.Count -gt $MaximumFiles) {
        throw "قائمة files في manifest فارغة أو كبيرة أكثر من اللازم."
    }
    $ManifestPaths = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    foreach ($Entry in $ManifestFiles) {
        $Relative = [string]$Entry.path
        if (-not (Test-SafeRelativePath $Relative) -or
            -not $ManifestPaths.Add($Relative.Replace('\', '/'))) {
            throw "مسار manifest غير آمن أو مكرر: $Relative"
        }
        $ExpectedHash = [string]$Entry.sha256
        if ($ExpectedHash -notmatch '^[0-9a-f]{64}$' -or [Int64]$Entry.bytes -lt 0) {
            throw "حجم/بصمة غير صالحة في manifest: $Relative"
        }
        $StagedFile = Get-ContainedPath $StagingRoot $Relative
        if (-not (Test-Path -LiteralPath $StagedFile -PathType Leaf)) {
            throw "ملف manifest مفقود من ZIP: $Relative"
        }
        $File = Get-Item -LiteralPath $StagedFile
        if ($File.Length -ne [Int64]$Entry.bytes -or
            (Get-Sha256 $StagedFile) -cne $ExpectedHash) {
            throw "فشل SHA-256/الحجم للملف: $Relative"
        }
    }
    $ActualPayloadPaths = @(Get-ChildItem -LiteralPath $StagingRoot -Recurse -File |
        ForEach-Object {
            $_.FullName.Substring($StagingRoot.Length).TrimStart('\').Replace('\', '/')
        } | Where-Object { $_ -cne "update-manifest.json" })
    if ($ActualPayloadPaths.Count -ne $ManifestPaths.Count) {
        throw "ZIP يحتوي ملفًا زائدًا أو manifest لا يسرد كل الملفات."
    }
    foreach ($Relative in $ActualPayloadPaths) {
        if (-not $ManifestPaths.Contains($Relative)) {
            throw "ملف غير مصرح به داخل ZIP: $Relative"
        }
    }

    foreach ($Required in @(
        "MujassamAI.exe",
        "app/worker.py",
        "app/engines/hunyuan21/hunyuan21_worker.py",
        "app/engines/hunyuan21/ENGINE-MANIFEST.json",
        "app/engines/hunyuan21/LICENSE-HUNYUAN3D-2.1.txt",
        "app/engines/hunyuan21/NOTICE-HUNYUAN3D-2.1.txt",
        "app/engines/hunyuan21/NOTICE.txt"
    )) {
        if (-not $ManifestPaths.Contains($Required)) {
            throw "التحديث الموثق يفتقد ملفًا مطلوبًا: $Required"
        }
    }
    $EngineManifestPath = Get-ContainedPath $StagingRoot `
        "app/engines/hunyuan21/ENGINE-MANIFEST.json"
    $EngineManifest = Get-Content -LiteralPath $EngineManifestPath -Raw | ConvertFrom-Json
    if ([string]$EngineManifest.source.commit -cne $ExpectedUpstreamCommit -or
        [string]$EngineManifest.runtime_abi.python -cne "3.11.9" -or
        [string]$EngineManifest.runtime_abi.pytorch -cne "2.5.1+cu124" -or
        [string]$EngineManifest.runtime_abi.torchvision -cne "0.20.1+cu124" -or
        [string]$EngineManifest.runtime_abi.cuda_runtime -cne "12.4" -or
        [string]$EngineManifest.runtime_abi.platform -cne "win_amd64") {
        throw "ENGINE-MANIFEST داخل التحديث لا يطابق ABI/source المثبت."
    }

    $LocalApplicationData = [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrWhiteSpace($LocalApplicationData)) {
        throw "تعذر تحديد مجلد LocalAppData للنسخ الاحتياطية."
    }
    $LocalApplicationData = [IO.Path]::GetFullPath($LocalApplicationData).TrimEnd('\')
    Assert-NormalDirectory $LocalApplicationData "مجلد LocalAppData"
    $BackupParent = Join-Path $LocalApplicationData "MujassamAI\Backups"
    Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupParent
    [IO.Directory]::CreateDirectory($BackupParent) | Out-Null
    Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupParent
    Assert-WritableDirectory $BackupParent "مجلد النسخ الاحتياطية"
    $BackupRoot = Join-Path $BackupParent (
        "hunyuan21-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") +
        "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    if (Test-Path -LiteralPath $BackupRoot) {
        throw "مسار النسخة الاحتياطية موجود مسبقًا: $BackupRoot"
    }
    Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupRoot
    [IO.Directory]::CreateDirectory($BackupRoot) | Out-Null
    Assert-NoReparsePointInExistingPath $LocalApplicationData $BackupRoot

    $Overwritten = [Collections.Generic.List[object]]::new()
    $Created = [Collections.Generic.List[object]]::new()
    foreach ($Entry in $ManifestFiles) {
        $Relative = ([string]$Entry.path).Replace('\', '/')
        $Destination = Get-ContainedPath $RootPath $Relative
        Assert-NoReparsePointInExistingPath $RootPath $Destination
        if (Test-Path -LiteralPath $Destination) {
            if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
                throw "مسار الهدف موجود لكنه ليس ملفًا: $Relative"
            }
            $BackupFile = Get-ContainedPath $BackupRoot $Relative
            $BackupFileParent = [IO.Path]::GetDirectoryName($BackupFile)
            Assert-NoReparsePointInExistingPath $BackupRoot $BackupFileParent
            [IO.Directory]::CreateDirectory($BackupFileParent) | Out-Null
            Assert-NoReparsePointInExistingPath $BackupRoot $BackupFileParent
            [IO.File]::Copy($Destination, $BackupFile, $false)
            $Original = Get-Item -LiteralPath $Destination
            $OriginalHash = Get-Sha256 $Destination
            if ((Get-Sha256 $BackupFile) -cne $OriginalHash) {
                throw "فشل التحقق من النسخة الاحتياطية: $Relative"
            }
            $Overwritten.Add([ordered]@{
                path = $Relative
                bytes = [Int64]$Original.Length
                sha256 = $OriginalHash
                installed_sha256 = [string]$Entry.sha256
            })
        } else {
            $Created.Add([ordered]@{
                path = $Relative
                installed_sha256 = [string]$Entry.sha256
            })
        }
    }

    $Receipt = [ordered]@{
        schema_version = 1
        created_utc = (Get-Date).ToUniversalTime().ToString("o")
        install_root = $RootPath
        update_zip = $ZipPath
        update_zip_sha256 = $ArchiveSha256
        overwritten_files = @($Overwritten)
        created_files = @($Created)
    }
    $ReceiptPath = Join-Path $BackupRoot "install-receipt.json"
    [IO.File]::WriteAllText(
        $ReceiptPath, ($Receipt | ConvertTo-Json -Depth 6),
        [Text.UTF8Encoding]::new($false))

    $OriginalByPath = [Collections.Generic.Dictionary[string, object]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    foreach ($OriginalEntry in $Overwritten) {
        $OriginalByPath.Add([string]$OriginalEntry.path, $OriginalEntry)
    }
    $Applied = [Collections.Generic.List[object]]::new()
    try {
        foreach ($Entry in $ManifestFiles) {
            $Relative = ([string]$Entry.path).Replace('\', '/')
            $Source = Get-ContainedPath $StagingRoot $Relative
            $Destination = Get-ContainedPath $RootPath $Relative
            Assert-NoReparsePointInExistingPath $RootPath $Destination
            $DestinationParent = [IO.Path]::GetDirectoryName($Destination)
            [IO.Directory]::CreateDirectory($DestinationParent) | Out-Null
            Assert-NoReparsePointInExistingPath $RootPath $DestinationParent
            $TemporaryDestination = Join-Path $DestinationParent (
                "MujassamAI-new-" + [Guid]::NewGuid().ToString("N") + ".tmp")
            [IO.File]::Copy($Source, $TemporaryDestination, $false)
            if ((Get-Sha256 $TemporaryDestination) -cne [string]$Entry.sha256) {
                [IO.File]::Delete($TemporaryDestination)
                throw "فشل تحقق النسخة المؤقتة قبل الاستبدال: $Relative"
            }
            $Existed = $OriginalByPath.ContainsKey($Relative)
            if ($Existed) {
                $OriginalEntry = $OriginalByPath[$Relative]
                if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
                    (Get-Sha256 $Destination) -cne [string]$OriginalEntry.sha256) {
                    Remove-TemporaryFileBestEffort $TemporaryDestination
                    throw "تغيّر ملف الهدف قبل التثبيت مباشرة: $Relative"
                }
            } elseif (Test-Path -LiteralPath $Destination) {
                Remove-TemporaryFileBestEffort $TemporaryDestination
                throw "ظهر مسار هدف جديد قبل التثبيت مباشرة: $Relative"
            }
            $ReplacementBackup = $null
            try {
                if ($Existed) {
                    $ReplacementBackup = Join-Path $DestinationParent (
                        "MujassamAI-replaced-" +
                        [Guid]::NewGuid().ToString("N") + ".tmp")
                    [IO.File]::Replace(
                        $TemporaryDestination, $Destination,
                        $ReplacementBackup, $true)
                } else {
                    [IO.File]::Move($TemporaryDestination, $Destination)
                }
            } catch {
                Remove-TemporaryFileBestEffort $TemporaryDestination
                Remove-TemporaryFileBestEffort $ReplacementBackup
                throw
            }
            $Applied.Add([ordered]@{
                path = $Relative
                existed = $Existed
                installed_sha256 = [string]$Entry.sha256
            })
            Remove-TemporaryFileBestEffort $ReplacementBackup
            if ((Get-Sha256 $Destination) -cne [string]$Entry.sha256) {
                throw "فشل تحقق الملف مباشرة بعد التثبيت: $Relative"
            }
        }
        foreach ($Entry in $ManifestFiles) {
            $Destination = Get-ContainedPath $RootPath ([string]$Entry.path)
            if ((Get-Sha256 $Destination) -cne [string]$Entry.sha256) {
                throw "فشل فحص ما بعد التثبيت: $($Entry.path)"
            }
        }
    } catch {
        $InstallError = $_
        Write-Warning "فشل التثبيت؛ بدء الاسترجاع من النسخة الاحتياطية."
        $RollbackErrors = [Collections.Generic.List[string]]::new()
        for ($Index = $Applied.Count - 1; $Index -ge 0; $Index--) {
            $AppliedEntry = $Applied[$Index]
            $Relative = [string]$AppliedEntry.path
            $Destination = Get-ContainedPath $RootPath $Relative
            try {
                Assert-NoReparsePointInExistingPath $RootPath $Destination
                if ([bool]$AppliedEntry.existed) {
                    $BackupFile = Get-ContainedPath $BackupRoot $Relative
                    $OriginalEntry = $OriginalByPath[$Relative]
                    if ((Get-Sha256 $BackupFile) -cne [string]$OriginalEntry.sha256) {
                        throw "بصمة النسخة الاحتياطية تغيّرت"
                    }
                    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
                        $CurrentHash = Get-Sha256 $Destination
                        if ($CurrentHash -ceq [string]$OriginalEntry.sha256) {
                            continue
                        }
                        if ($CurrentHash -cne [string]$AppliedEntry.installed_sha256) {
                            throw "تغيّر الملف بعد فشل التثبيت؛ لم تتم الكتابة فوقه"
                        }
                    } elseif (Test-Path -LiteralPath $Destination) {
                        throw "مسار الهدف بعد فشل التثبيت ليس ملفًا"
                    }
                    $Parent = [IO.Path]::GetDirectoryName($Destination)
                    $RollbackTemporary = Join-Path $Parent (
                        "MujassamAI-rollback-" +
                        [Guid]::NewGuid().ToString("N") + ".tmp")
                    [IO.File]::Copy($BackupFile, $RollbackTemporary, $false)
                    $RollbackReplacementBackup = $null
                    try {
                        if ((Get-Sha256 $RollbackTemporary) -cne
                            [string]$OriginalEntry.sha256) {
                            throw "فشل فحص ملف الاسترجاع المؤقت"
                        }
                        Assert-NoReparsePointInExistingPath $RootPath $Destination
                        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
                            $RollbackReplacementBackup = Join-Path $Parent (
                                "MujassamAI-rollback-replaced-" +
                                [Guid]::NewGuid().ToString("N") + ".tmp")
                            [IO.File]::Replace(
                                $RollbackTemporary, $Destination,
                                $RollbackReplacementBackup, $true)
                        } else {
                            [IO.File]::Move($RollbackTemporary, $Destination)
                        }
                    } finally {
                        Remove-TemporaryFileBestEffort $RollbackTemporary
                        Remove-TemporaryFileBestEffort $RollbackReplacementBackup
                    }
                    if ((Get-Sha256 $Destination) -cne
                        [string]$OriginalEntry.sha256) {
                        throw "فشل فحص الملف بعد الاسترجاع"
                    }
                } elseif (Test-Path -LiteralPath $Destination -PathType Leaf) {
                    if ((Get-Sha256 $Destination) -cne
                        [string]$AppliedEntry.installed_sha256) {
                        throw "تغيّر الملف الجديد قبل الاسترجاع؛ لم يُحذف"
                    }
                    Assert-NoReparsePointInExistingPath $RootPath $Destination
                    [IO.File]::Delete($Destination)
                    if (Test-Path -LiteralPath $Destination) {
                        throw "تعذر حذف الملف الذي أضافه التحديث"
                    }
                }
            } catch {
                $RollbackErrors.Add("$Relative`: $($_.Exception.Message)")
            }
        }
        if ($RollbackErrors.Count -ne 0) {
            throw (
                "فشل التثبيت وفشل استرجاع بعض الملفات. النسخة الاحتياطية: " +
                "$BackupRoot. التفاصيل: $($RollbackErrors -join '; '). " +
                "الخطأ الأصلي: $($InstallError.Exception.Message)"
            )
        }
        foreach ($OriginalEntry in $Overwritten) {
            $Destination = Get-ContainedPath $RootPath ([string]$OriginalEntry.path)
            if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
                (Get-Sha256 $Destination) -cne [string]$OriginalEntry.sha256) {
                throw (
                    "فشل التثبيت ولم ينجح التحقق النهائي من الاسترجاع. " +
                    "لا تشغّل البرنامج. النسخة الاحتياطية: $BackupRoot"
                )
            }
        }
        foreach ($CreatedEntry in $Created) {
            $Destination = Get-ContainedPath $RootPath ([string]$CreatedEntry.path)
            if (Test-Path -LiteralPath $Destination) {
                throw (
                    "فشل التثبيت وبقي ملف جديد بعد الاسترجاع. لا تشغّل البرنامج. " +
                    "النسخة الاحتياطية: $BackupRoot"
                )
            }
        }
        throw "فشل التثبيت وتم استرجاع الملفات السابقة. النسخة الاحتياطية: $BackupRoot. $($InstallError.Exception.Message)"
    }
    Write-Host "تم تثبيت Hunyuan3D 2.1 محليًا بنجاح." -ForegroundColor Green
    Write-Host "ZIP SHA-256: $ArchiveSha256"
    Write-Host "نسخة احتياطية قابلة للاسترجاع: $BackupRoot"
    Write-Host "لا يستخدم هذا المثبّت GitHub Release ولا ينزّل أي ملف."
} finally {
    if ($StagingCreated -and (Test-Path -LiteralPath $StagingRoot)) {
        try {
            Remove-SafeStagingDirectory $StagingRoot $InstallerTemporaryRoot
        } catch {
            Write-Warning "تعذر تنظيف مجلد التجهيز: $($_.Exception.Message)"
        }
    }
}
