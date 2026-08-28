[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = "High")]
param(
    [Parameter()]
    [ValidateNotNullOrEmpty()]
    [string]$InstallRoot = "$env:USERPROFILE\Documents\MujassamAI-Portable",

    # Restoring the original Mini executable/worker is reversible and remains
    # the default operation.  Large/add-on cleanup is intentionally opt-in so
    # an accidental invocation cannot discard the H21 rollback material.
    [Parameter()]
    [switch]$DeepCleanup,

    # These roots make the destructive-path contract testable without touching
    # a runner account.  Every deletion is still restricted to the exact child
    # names/prefixes enforced below.
    [Parameter(DontShow = $true)]
    [string]$LocalApplicationDataRoot,

    [Parameter(DontShow = $true)]
    [string]$UserProfileRoot,

    [Parameter(DontShow = $true)]
    [string]$TemporaryRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$BaseBackupPattern = '^hunyuan21-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$'
$AnyH21BackupPattern = `
    '^hunyuan21(?:-rasterizer|-paint-8gb)?-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$'
$PbrUpdateArtifactPattern = `
    '^MujassamAI-Hunyuan21-PBR-Update-v1(?:-[0-9]{8}-[0-9]{6}(?:-[0-9a-f]{8})?)?\.zip(?:\.sha256)?$'
$TemporaryDirectoryPattern = `
    '^MujassamAI-hy21-(?:[0-9a-f]{32}|install-[0-9a-f]{32}|rasterizer-[0-9a-f]{32})$'
$LegacyStagingPattern = '^\.MujassamAI-hy21-staging-[0-9a-f]{8,32}$'
$SetupArtifactDirectoryPattern = `
    '^(?:(?:MAI|MHU)-[0-9a-f]{8}|MujassamAI-HunyuanBackup-[0-9a-f]{8})$'
$QuarantineDirectoryPattern = `
    '^\.MujassamAI-hy21-quarantine-[0-9a-f]{32}$'
$QuarantineItemPattern = '^item-[0-9]{6}$'
$QuarantineTemporaryMarkerPattern = '^transaction-[0-9a-f]{32}\.tmp$'
$H21WorkerRelative = "app/engines/hunyuan21/hunyuan21_worker.py"
$MiniWorkerRelative = "app/worker.py"
$MiniExecutableRelative = "MujassamAI.exe"
$MaximumReceiptFiles = 150000

function Get-NormalizedFullPath([string]$Path, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.Contains([char]0)) {
        throw "$Label غير صالح أو فارغ."
    }
    try {
        $Full = [IO.Path]::GetFullPath($Path)
        $VolumeRoot = [IO.Path]::GetPathRoot($Full)
    } catch {
        throw "$Label غير صالح: $Path. $($_.Exception.Message)"
    }
    if ([string]::IsNullOrWhiteSpace($VolumeRoot)) {
        throw "$Label لا يملك جذر volume معروفًا: $Path"
    }
    # Preserve C:\ (and UNC share roots) exactly; trimming it to C: changes
    # GetFullPath semantics and can turn a safety comparison into a CWD lookup.
    if ($Full.Length -gt $VolumeRoot.Length) {
        $Full = $Full.TrimEnd([char[]]@('\', '/'))
    }
    return $Full
}

function Test-PathInsideOrEqual([string]$Path, [string]$Root) {
    $PathFull = Get-NormalizedFullPath $Path "المسار"
    $RootFull = Get-NormalizedFullPath $Root "الجذر"
    if ([string]::Equals(
        $PathFull, $RootFull, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $RootWithSeparator = $RootFull
    if (-not $RootWithSeparator.EndsWith('\')) {
        $RootWithSeparator += '\'
    }
    return $PathFull.StartsWith(
        $RootWithSeparator, [StringComparison]::OrdinalIgnoreCase)
}

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
        if ([string]::IsNullOrWhiteSpace($Part) -or
            $Part -in @('.', '..') -or
            $Part.EndsWith(' ') -or $Part.EndsWith('.') -or
            $Part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') {
            return $false
        }
    }
    return $true
}

function Get-ContainedPath([string]$Root, [string]$Relative) {
    if (-not (Test-SafeRelativePath $Relative)) {
        throw "مسار غير آمن: $Relative"
    }
    $RootNormalized = Get-NormalizedFullPath $Root "جذر المسار المحتوى"
    $RootWithSeparator = $RootNormalized
    if (-not $RootWithSeparator.EndsWith('\')) {
        $RootWithSeparator += '\'
    }
    $Candidate = [IO.Path]::GetFullPath((Join-Path $RootWithSeparator `
        $Relative.Replace('/', '\')))
    if (-not $Candidate.StartsWith(
        $RootWithSeparator, [StringComparison]::OrdinalIgnoreCase)) {
        throw "مسار خارج الجذر المسموح: $Relative"
    }
    return $Candidate
}

function Test-H21EngineRelativePath([string]$Path) {
    $Normalized = $Path.Replace('\', '/').TrimStart('/')
    return $Normalized.StartsWith(
        "app/engines/hunyuan21/", [StringComparison]::OrdinalIgnoreCase)
}

function Test-ProtectedMiniRelativePath([string]$Path) {
    $Normalized = $Path.Replace('\', '/').TrimStart('/')
    foreach ($ProtectedRoot in @(
        "app/engines/hunyuan2", "rt", "models", "app/vendor",
        "exports", "export", "output", "outputs",
        "app/exports", "app/export", "app/output", "app/outputs"
    )) {
        if ([string]::Equals(
            $Normalized, $ProtectedRoot,
            [StringComparison]::OrdinalIgnoreCase) -or
            $Normalized.StartsWith(
                $ProtectedRoot + "/",
                [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Test-AllowedH21ReceiptRelativePath([string]$Path) {
    if (-not (Test-SafeRelativePath $Path)) {
        return $false
    }
    $Normalized = $Path.Replace('\', '/').TrimStart('/')
    foreach ($AllowedFile in @(
        "MujassamAI.exe", "NOTICE_THIRD_PARTY.md", "app/worker.py"
    )) {
        if ([string]::Equals(
            $Normalized, $AllowedFile,
            [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    foreach ($AllowedTree in @(
        "app/quality", "licenses", "app/engines/hunyuan21"
    )) {
        if ($Normalized.StartsWith(
            $AllowedTree + "/", [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Assert-PathNotProtected(
    [string]$Path,
    [string[]]$ProtectedRoots,
    [string]$Label
) {
    $PathFull = Get-NormalizedFullPath $Path $Label
    foreach ($ProtectedRoot in $ProtectedRoots) {
        if (-not [string]::IsNullOrWhiteSpace($ProtectedRoot) -and
            ((Test-PathInsideOrEqual $PathFull $ProtectedRoot) -or
             (Test-PathInsideOrEqual $ProtectedRoot $PathFull))) {
            throw (
                "رُفض المساس بـ$Label بسبب تداخل مع مسار محمي: " +
                "$PathFull <-> $ProtectedRoot"
            )
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

function Assert-NormalFile([string]$Path, [string]$Label) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label غير موجود: $Path"
    }
    $Item = Get-Item -LiteralPath $Path -Force
    if ($Item.PSIsContainer -or
        ($Item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "$Label يجب أن يكون ملفًا عاديًا وليس reparse point: $Path"
    }
}

function Assert-NoReparsePointInExistingPath([string]$Root, [string]$Candidate) {
    $RootFull = Get-NormalizedFullPath $Root "جذر فحص reparse point"
    $CandidateFull = Get-NormalizedFullPath $Candidate "مسار فحص reparse point"
    if (-not (Test-PathInsideOrEqual $CandidateFull $RootFull)) {
        throw "المسار ليس داخل الجذر المحدد: $CandidateFull"
    }
    $Current = $RootFull
    if (Test-Path -LiteralPath $Current) {
        $RootItem = Get-Item -LiteralPath $Current -Force
        if ($RootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "رفض reparse point داخل المسار: $Current"
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
            throw "رفض reparse point داخل المسار: $Current"
        }
    }
}

function Assert-NormalTree([string]$Path, [string]$Label) {
    Assert-NormalDirectory $Path $Label
    $Pending = [Collections.Generic.Stack[string]]::new()
    $Pending.Push([IO.Path]::GetFullPath($Path))
    while ($Pending.Count -ne 0) {
        $Current = $Pending.Pop()
        foreach ($Child in [IO.Directory]::EnumerateFileSystemEntries($Current)) {
            $Attributes = [IO.File]::GetAttributes($Child)
            if ($Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "$Label يحتوي reparse point؛ رُفض الحذف: $Child"
            }
            if ($Attributes -band [IO.FileAttributes]::Directory) {
                $Pending.Push($Child)
            }
        }
    }
}

function Test-TreeContainsUserOutput([string]$Path, [string]$Label) {
    Assert-NormalTree $Path $Label
    $Pending = [Collections.Generic.Stack[string]]::new()
    $Pending.Push((Get-NormalizedFullPath $Path $Label))
    while ($Pending.Count -ne 0) {
        $Current = $Pending.Pop()
        foreach ($Child in [IO.Directory]::EnumerateFileSystemEntries(
            $Current)) {
            $Attributes = [IO.File]::GetAttributes($Child)
            if ($Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "$Label يحتوي reparse point؛ رُفض الفحص: $Child"
            }
            if ([IO.Path]::GetFileName($Child) -imatch `
                '^(?:export|exports|output|outputs|MujassamAI-Exports|\.git)$') {
                return $true
            }
            if ($Attributes -band [IO.FileAttributes]::Directory) {
                $Pending.Push($Child)
            }
        }
    }
    return $false
}

function Assert-DirectChildDirectory(
    [string]$Path,
    [string]$Parent,
    [string]$NamePattern,
    [string]$Label
) {
    $PathFull = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $ParentFull = [IO.Path]::GetFullPath($Parent).TrimEnd('\')
    if (-not [string]::Equals(
        [IO.Path]::GetDirectoryName($PathFull), $ParentFull,
        [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetFileName($PathFull) -cnotmatch $NamePattern) {
        throw "رُفض مسار تنظيف غير متوقع: $PathFull"
    }
    Assert-NormalTree $PathFull $Label
}

function Assert-MujassamProcessesStopped([string]$Root) {
    $RootWithSeparator = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $RunningApplication = @(Get-Process -Name "MujassamAI" `
        -ErrorAction SilentlyContinue)
    $RunningPortablePython = @(
        foreach ($ProcessName in @("python", "pythonw")) {
            Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
                Where-Object {
                    try {
                        $_.Path.StartsWith(
                            $RootWithSeparator,
                            [StringComparison]::OrdinalIgnoreCase)
                    } catch {
                        $false
                    }
                }
        }
    )
    if ($RunningApplication.Count -ne 0 -or
        $RunningPortablePython.Count -ne 0) {
        throw "أغلق Mujassam AI وانتظر توقفه بالكامل، ثم أعد أمر الإزالة."
    }
}

function Select-InactiveH21CleanupDirectories([string[]]$Paths) {
    try {
        $CommandLines = @(
            Get-CimInstance -ClassName Win32_Process -ErrorAction Stop |
                ForEach-Object { [string]$_.CommandLine } |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
    } catch {
        return [pscustomobject]@{
            Paths = @()
            Warning = (
                "تعذر التأكد من عدم وجود build/install آخر؛ " +
                "تُركت مجلدات TEMP/staging دون حذف."
            )
        }
    }
    foreach ($CommandLine in $CommandLines) {
        foreach ($ActiveOperationMarker in @(
            "build-hunyuan21-local.ps1",
            "build-hunyuan21-update.ps1",
            "resume-hunyuan21-local.ps1",
            "install-hunyuan21-local.ps1",
            "hotfix-hunyuan21-rasterizer.ps1",
            "hotfix-hunyuan21-paint-8gb.ps1"
        )) {
            if ($CommandLine.IndexOf(
                $ActiveOperationMarker,
                [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return [pscustomobject]@{
                    Paths = @()
                    Warning = (
                        "يوجد build/install خاص بـH21 يعمل الآن؛ " +
                        "تُركت مجلدات TEMP/staging لحمايته."
                    )
                }
            }
        }
    }
    $Safe = [Collections.Generic.List[string]]::new()
    foreach ($Path in $Paths) {
        $InUse = $false
        foreach ($CommandLine in $CommandLines) {
            if ($CommandLine.IndexOf(
                [IO.Path]::GetFullPath($Path),
                [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $InUse = $true
                break
            }
        }
        if (-not $InUse) {
            $Safe.Add($Path)
        }
    }
    $Warning = if ($Safe.Count -ne $Paths.Count) {
        "تُرك مجلد TEMP/staging مستخدم بواسطة عملية قائمة."
    } else {
        $null
    }
    return [pscustomobject]@{ Paths = @($Safe); Warning = $Warning }
}

function Assert-DeepCleanupPhaseReady([string]$InstallRoot) {
    Assert-MujassamProcessesStopped $InstallRoot
    $OperationCheck = Select-InactiveH21CleanupDirectories @()
    if (-not [string]::IsNullOrWhiteSpace([string]$OperationCheck.Warning)) {
        throw [string]$OperationCheck.Warning
    }
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

function Get-ReceiptPathMap([object[]]$Entries, [string]$Label) {
    $Map = [Collections.Generic.Dictionary[string, object]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    foreach ($Entry in $Entries) {
        $Relative = ([string]$Entry.path).Replace('\', '/')
        if (-not (Test-SafeRelativePath $Relative) -or
            $Map.ContainsKey($Relative)) {
            throw "$Label يحتوي مسارًا مكررًا أو غير آمن: $Relative"
        }
        if (-not (Test-AllowedH21ReceiptRelativePath $Relative)) {
            throw "$Label يحتوي مسارًا خارج allowlist تحديث H21: $Relative"
        }
        if (Test-ProtectedMiniRelativePath $Relative) {
            throw "$Label يحاول المساس بمسار Mini محمي: $Relative"
        }
        $Map.Add($Relative, $Entry)
    }
    return ,$Map
}

function Get-TrustedMiniBaseline([string]$BackupPath, [string]$ExpectedInstallRoot) {
    try {
        Assert-NormalTree $BackupPath "نسخة Mini الاحتياطية المرشحة"
        $ReceiptPath = Join-Path $BackupPath "install-receipt.json"
        Assert-NoReparsePointInExistingPath $BackupPath $ReceiptPath
        Assert-NormalFile $ReceiptPath "إيصال التثبيت"
        $Receipt = Get-Content -LiteralPath $ReceiptPath -Raw | ConvertFrom-Json
        if ([int]$Receipt.schema_version -ne 1 -or
            -not [string]::Equals(
                (Get-NormalizedFullPath ([string]$Receipt.install_root) `
                    "install_root داخل الإيصال"),
                $ExpectedInstallRoot,
                [StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
        $Overwritten = @($Receipt.overwritten_files)
        $Created = @($Receipt.created_files)
        if ($Overwritten.Count + $Created.Count -eq 0 -or
            $Overwritten.Count + $Created.Count -gt $MaximumReceiptFiles) {
            return $null
        }
        $OverwrittenMap = Get-ReceiptPathMap $Overwritten "إيصال overwritten_files"
        $CreatedMap = Get-ReceiptPathMap $Created "إيصال created_files"
        foreach ($Path in $OverwrittenMap.Keys) {
            if ($CreatedMap.ContainsKey($Path)) {
                return $null
            }
        }
        if (-not $OverwrittenMap.ContainsKey($MiniExecutableRelative) -or
            -not $OverwrittenMap.ContainsKey($MiniWorkerRelative) -or
            $OverwrittenMap.ContainsKey($H21WorkerRelative) -or
            -not $CreatedMap.ContainsKey($H21WorkerRelative)) {
            return $null
        }
        foreach ($RequiredRelative in @(
            $MiniExecutableRelative, $MiniWorkerRelative
        )) {
            $Entry = $OverwrittenMap[$RequiredRelative]
            $ExpectedHash = [string]$Entry.sha256
            $InstalledHash = [string]$Entry.installed_sha256
            $BackupFile = Get-ContainedPath $BackupPath $RequiredRelative
            Assert-NoReparsePointInExistingPath $BackupPath $BackupFile
            Assert-NormalFile $BackupFile "ملف Mini الاحتياطي"
            if ($ExpectedHash -notmatch '^[0-9a-f]{64}$' -or
                $InstalledHash -notmatch '^[0-9a-f]{64}$' -or
                [Int64]$Entry.bytes -lt 0 -or
                (Get-Item -LiteralPath $BackupFile).Length -ne [Int64]$Entry.bytes -or
                (Get-Sha256 $BackupFile) -cne $ExpectedHash) {
                return $null
            }
        }
        $BackupWorker = Get-ContainedPath $BackupPath $MiniWorkerRelative
        $BackupExecutable = Get-ContainedPath `
            $BackupPath $MiniExecutableRelative
        if (-not (Test-MarkerFreeMiniExecutable $BackupExecutable)) {
            return $null
        }
        if ((Get-Item -LiteralPath $BackupWorker).Length -gt 10MB) {
            return $null
        }
        $MiniWorkerText = [IO.File]::ReadAllText($BackupWorker)
        foreach ($UltimateMarker in @(
            "hunyuan3d_2_1_pbr", "hunyuan21_worker.py"
        )) {
            if ($MiniWorkerText.IndexOf(
                $UltimateMarker, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $null
            }
        }
        return [pscustomobject]@{
            Path = $BackupPath
            Receipt = $Receipt
            Overwritten = $Overwritten
            Created = $Created
            OverwrittenMap = $OverwrittenMap
            CreatedMap = $CreatedMap
        }
    } catch {
        Write-Verbose "استبعاد نسخة احتياطية غير موثوقة $BackupPath`: $($_.Exception.Message)"
        return $null
    }
}

function New-QuarantineTransaction(
    [string[]]$ProtectedRoots,
    [string]$Purpose,
    [string]$InstallRoot
) {
    if ($Purpose -cnotin @("restore", "deep-cleanup")) {
        throw "غرض quarantine غير صالح: $Purpose"
    }
    return [pscustomobject]@{
        Id = [Guid]::NewGuid().ToString("N")
        Purpose = $Purpose
        State = "active"
        InstallRoot = (Get-NormalizedFullPath `
            $InstallRoot "install_root للمعاملة")
        ProtectedRoots = @($ProtectedRoots)
        Roots = [Collections.Generic.Dictionary[string, object]]::new(
            [StringComparer]::OrdinalIgnoreCase)
        Moves = [Collections.Generic.List[object]]::new()
    }
}

function Write-TransactionJournalAtomic(
    [object]$Transaction,
    [object]$RootRecord
) {
    Assert-DirectChildDirectory $RootRecord.Path $RootRecord.Anchor `
        $QuarantineDirectoryPattern "جذر journal"
    $MarkerPath = Join-Path $RootRecord.Path "transaction.json"
    if (Test-Path -LiteralPath $MarkerPath) {
        Assert-NormalFile $MarkerPath "journal المعاملة"
    }
    $Entries = @(
        foreach ($Entry in @($RootRecord.Entries)) {
            [ordered]@{
                item_name = [string]$Entry.ItemName
                quarantine_item = [string]$Entry.ItemName
                original_path = [string]$Entry.OriginalPath
                containment_root = [string]$Entry.ContainmentRoot
                kind = [string]$Entry.Kind
                original_sha256 = $Entry.OriginalSha256
                replacement_sha256 = $Entry.ReplacementSha256
                state = [string]$Entry.State
            }
        }
    )
    $Document = [ordered]@{
        schema_version = 2
        transaction_id = [string]$Transaction.Id
        purpose = [string]$Transaction.Purpose
        state = [string]$Transaction.State
        install_root = [string]$Transaction.InstallRoot
        entries = $Entries
    }
    $TemporaryMarker = Join-Path $RootRecord.Path (
        "transaction-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::WriteAllText(
            $TemporaryMarker, ($Document | ConvertTo-Json -Depth 6),
            [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $MarkerPath) {
            [IO.File]::Move($TemporaryMarker, $MarkerPath, $true)
        } else {
            [IO.File]::Move($TemporaryMarker, $MarkerPath, $false)
        }
    } finally {
        Remove-TemporaryFileBestEffort $TemporaryMarker
    }
}

function Get-TransactionQuarantineRoot(
    [object]$Transaction,
    [string]$TargetPath,
    [string]$PreferredAnchor
) {
    $TargetFull = Get-NormalizedFullPath $TargetPath "هدف quarantine"
    $TargetVolume = Get-NormalizedFullPath `
        ([IO.Path]::GetPathRoot($TargetFull)) "volume الهدف"
    $VolumeKey = $TargetVolume.ToUpperInvariant()
    if ($Transaction.Roots.ContainsKey($VolumeKey)) {
        return [string]$Transaction.Roots[$VolumeKey].Path
    }
    $Anchor = Get-NormalizedFullPath $PreferredAnchor "جذر quarantine"
    $AnchorVolume = Get-NormalizedFullPath `
        ([IO.Path]::GetPathRoot($Anchor)) "volume quarantine"
    if (-not [string]::Equals(
        $TargetVolume, $AnchorVolume,
        [StringComparison]::OrdinalIgnoreCase)) {
        throw "quarantine ليس على volume الهدف نفسه: $TargetFull"
    }
    Assert-NormalDirectory $Anchor "جذر quarantine"
    Assert-NoReparsePointInExistingPath $Anchor $Anchor
    $Quarantine = Join-Path $Anchor (
        ".MujassamAI-hy21-quarantine-" + [string]$Transaction.Id)
    Assert-NoReparsePointInExistingPath $Anchor $Quarantine
    if (Test-Path -LiteralPath $Quarantine) {
        throw "مسار quarantine موجود مسبقًا: $Quarantine"
    }
    [IO.Directory]::CreateDirectory($Quarantine) | Out-Null
    Assert-DirectChildDirectory $Quarantine $Anchor `
        $QuarantineDirectoryPattern "quarantine إزالة H21"
    $Record = [pscustomobject]@{
        Path = $Quarantine
        Anchor = $Anchor
        Entries = [Collections.Generic.List[object]]::new()
    }
    $Transaction.Roots.Add($VolumeKey, $Record)
    try {
        Write-TransactionJournalAtomic $Transaction $Record
    } catch {
        $Transaction.Roots.Remove($VolumeKey) | Out-Null
        if (Test-Path -LiteralPath $Quarantine) {
            foreach ($Temporary in @(
                [IO.Directory]::EnumerateFiles($Quarantine) |
                    Where-Object {
                        [IO.Path]::GetFileName($_) -cmatch `
                            $QuarantineTemporaryMarkerPattern
                    }
            )) {
                [IO.File]::Delete($Temporary)
            }
            if (-not [IO.Directory]::EnumerateFileSystemEntries(
                $Quarantine).GetEnumerator().MoveNext()) {
                [IO.Directory]::Delete($Quarantine, $false)
            }
        }
        throw
    }
    return $Quarantine
}

function Move-ToTransactionQuarantine {
    param(
        [object]$Transaction,
        [string]$Path,
        [string]$ContainmentRoot,
        [string]$PreferredAnchor,
        [string]$Label,
        [string]$ReplacementSha256 = $null
    )
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    $PathFull = Get-NormalizedFullPath $Path $Label
    $ContainmentFull = Get-NormalizedFullPath $ContainmentRoot "جذر $Label"
    if (-not (Test-PathInsideOrEqual $PathFull $ContainmentFull) -or
        [string]::Equals(
            $PathFull, $ContainmentFull,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "رُفض هدف quarantine خارج الجذر أو مساويًا له: $PathFull"
    }
    Assert-PathNotProtected $PathFull @($Transaction.ProtectedRoots) $Label
    Assert-NoReparsePointInExistingPath $ContainmentFull $PathFull
    $IsDirectory = Test-Path -LiteralPath $PathFull -PathType Container
    if ($IsDirectory) {
        Assert-NormalTree $PathFull $Label
    } else {
        Assert-NormalFile $PathFull $Label
    }
    $QuarantineRoot = Get-TransactionQuarantineRoot `
        $Transaction $PathFull $PreferredAnchor
    $RootRecord = @($Transaction.Roots.Values | Where-Object {
        [string]::Equals(
            [string]$_.Path, $QuarantineRoot,
            [StringComparison]::OrdinalIgnoreCase)
    })[0]
    $ItemName = "item-" + $Transaction.Moves.Count.ToString("D6")
    $QuarantinedPath = Join-Path $QuarantineRoot $ItemName
    if (Test-Path -LiteralPath $QuarantinedPath) {
        throw "عنصر quarantine موجود مسبقًا: $QuarantinedPath"
    }
    $MoveRecord = [pscustomobject]@{
        ItemName = $ItemName
        OriginalPath = $PathFull
        ContainmentRoot = $ContainmentFull
        QuarantinedPath = $QuarantinedPath
        IsDirectory = [bool]$IsDirectory
        Kind = $(if ($IsDirectory) { "directory" } else { "file" })
        OriginalSha256 = $(if ($IsDirectory) {
            $null
        } else {
            Get-Sha256 $PathFull
        })
        ReplacementSha256 = $ReplacementSha256
        State = "planned"
        RootRecord = $RootRecord
        Label = $Label
    }
    if (-not [string]::IsNullOrWhiteSpace($ReplacementSha256) -and
        $ReplacementSha256 -notmatch '^[0-9a-f]{64}$') {
        throw "بصمة replacement غير صالحة: $Label"
    }
    # Journal the intent before the destructive rename.  Recovery therefore
    # handles a crash both before and immediately after Move returns.
    $RootRecord.Entries.Add($MoveRecord)
    $Transaction.Moves.Add($MoveRecord)
    Write-TransactionJournalAtomic $Transaction $RootRecord
    if ($IsDirectory) {
        [IO.Directory]::Move($PathFull, $QuarantinedPath)
    } else {
        [IO.File]::Move($PathFull, $QuarantinedPath, $false)
    }
    $MoveRecord.State = "moved"
    Write-TransactionJournalAtomic $Transaction $RootRecord
    if ((Test-Path -LiteralPath $PathFull) -or
        -not (Test-Path -LiteralPath $QuarantinedPath)) {
        throw "فشل نقل $Label إلى quarantine."
    }
    return $MoveRecord
}

function Undo-TransactionEntry(
    [object]$Transaction,
    [object]$RootRecord,
    [object]$Entry
) {
    if ([string]$Entry.ItemName -cnotmatch $QuarantineItemPattern) {
        throw "اسم item غير صالح داخل journal: $($Entry.ItemName)"
    }
    $QuarantinedPath = Join-Path $RootRecord.Path ([string]$Entry.ItemName)
    Assert-NoReparsePointInExistingPath $RootRecord.Path $QuarantinedPath
    $OriginalPath = Get-NormalizedFullPath `
        ([string]$Entry.OriginalPath) "original_path داخل journal"
    $ContainmentRoot = Get-NormalizedFullPath `
        ([string]$Entry.ContainmentRoot) "containment_root داخل journal"
    if (-not (Test-PathInsideOrEqual $OriginalPath $ContainmentRoot) -or
        [string]::Equals(
            $OriginalPath, $ContainmentRoot,
            [StringComparison]::OrdinalIgnoreCase)) {
        throw "original_path خارج containment_root داخل journal: $OriginalPath"
    }
    Assert-NoReparsePointInExistingPath $ContainmentRoot $OriginalPath
    $QuarantinedExists = Test-Path -LiteralPath $QuarantinedPath
    $OriginalExists = Test-Path -LiteralPath $OriginalPath
    $IsDirectory = [string]$Entry.Kind -ceq "directory"

    if ($QuarantinedExists) {
        if ($IsDirectory) {
            Assert-NormalTree $QuarantinedPath "directory داخل quarantine"
        } else {
            Assert-NormalFile $QuarantinedPath "file داخل quarantine"
            if ([string]$Entry.OriginalSha256 -notmatch '^[0-9a-f]{64}$' -or
                (Get-Sha256 $QuarantinedPath) -cne
                    [string]$Entry.OriginalSha256) {
                throw "فشلت بصمة original داخل quarantine: $OriginalPath"
            }
        }
        if ($OriginalExists) {
            $ReplacementHash = [string]$Entry.ReplacementSha256
            if ($IsDirectory -or
                $ReplacementHash -notmatch '^[0-9a-f]{64}$') {
                throw "تعارض fail-closed عند rollback: $OriginalPath"
            }
            Assert-NormalFile $OriginalPath "replacement قبل rollback"
            if ((Get-Sha256 $OriginalPath) -cne $ReplacementHash) {
                throw "replacement تغيّر قبل rollback: $OriginalPath"
            }
            [IO.File]::Delete($OriginalPath)
        }
        $OriginalParent = [IO.Path]::GetDirectoryName($OriginalPath)
        [IO.Directory]::CreateDirectory($OriginalParent) | Out-Null
        Assert-NoReparsePointInExistingPath $ContainmentRoot $OriginalPath
        if ($IsDirectory) {
            [IO.Directory]::Move($QuarantinedPath, $OriginalPath)
        } else {
            [IO.File]::Move($QuarantinedPath, $OriginalPath, $false)
        }
    } elseif (-not $OriginalExists) {
        throw "فُقد original وquarantine معًا: $OriginalPath"
    }

    # q missing + original present means either Move never happened or this
    # exact entry was already rolled back before a process interruption.
    if ($IsDirectory) {
        Assert-NormalTree $OriginalPath "directory بعد rollback"
    } else {
        Assert-NormalFile $OriginalPath "file بعد rollback"
        if ([string]$Entry.OriginalSha256 -notmatch '^[0-9a-f]{64}$' -or
            (Get-Sha256 $OriginalPath) -cne [string]$Entry.OriginalSha256) {
            throw "original لا يطابق journal بعد rollback: $OriginalPath"
        }
    }
    $Entry.State = "rolled-back"
    Write-TransactionJournalAtomic $Transaction $RootRecord
}

function Remove-RolledBackTransactionRoot(
    [object]$Transaction,
    [object]$RootRecord
) {
    foreach ($Temporary in @(
        [IO.Directory]::EnumerateFiles($RootRecord.Path) |
            Where-Object {
                [IO.Path]::GetFileName($_) -cmatch `
                    $QuarantineTemporaryMarkerPattern
            }
    )) {
        Assert-NormalFile $Temporary "ملف journal مؤقت"
        [IO.File]::Delete($Temporary)
    }
    $Unexpected = @(
        [IO.Directory]::EnumerateFileSystemEntries($RootRecord.Path) |
            Where-Object { [IO.Path]::GetFileName($_) -cne "transaction.json" }
    )
    if ($Unexpected.Count -ne 0) {
        throw "quarantine غير فارغ بعد rollback: $($Unexpected -join ', ')"
    }
    $MarkerPath = Join-Path $RootRecord.Path "transaction.json"
    Assert-NormalFile $MarkerPath "journal rollback"
    [IO.File]::Delete($MarkerPath)
    [IO.Directory]::Delete($RootRecord.Path, $false)
}

function Undo-QuarantineTransaction([object]$Transaction) {
    $Errors = [Collections.Generic.List[string]]::new()
    for ($Index = $Transaction.Moves.Count - 1; $Index -ge 0; $Index--) {
        $Move = $Transaction.Moves[$Index]
        try {
            Undo-TransactionEntry $Transaction $Move.RootRecord $Move
        } catch {
            $Errors.Add("$($Move.Label): $($_.Exception.Message)")
        }
    }
    if ($Errors.Count -eq 0) {
        foreach ($RootRecord in @($Transaction.Roots.Values)) {
            try {
                Remove-RolledBackTransactionRoot $Transaction $RootRecord
            } catch {
                $Errors.Add("quarantine rollback: $($_.Exception.Message)")
            }
        }
    }
    if ($Errors.Count -ne 0) {
        throw "فشل rollback: $($Errors -join ' | ')"
    }
}

function Set-QuarantineTransactionCommitted([object]$Transaction) {
    $Transaction.State = "committed"
    foreach ($RootRecord in @($Transaction.Roots.Values)) {
        Write-TransactionJournalAtomic $Transaction $RootRecord
    }
}

function Get-RecoveryContainmentRoot(
    [string]$Path,
    [string]$Kind,
    [hashtable]$Context
) {
    $PathFull = Get-NormalizedFullPath $Path "original_path recovery"
    if (Test-PathInsideOrEqual $PathFull $Context.InstallRoot) {
        if ([string]::Equals(
            $PathFull, $Context.InstallRoot,
            [StringComparison]::OrdinalIgnoreCase)) {
            return $null
        }
        $Relative = $PathFull.Substring(
            $Context.InstallRoot.Length).TrimStart('\').Replace('\', '/')
        if ($Kind -ceq "directory" -and
            [string]::Equals(
                $Relative, "app/engines/hunyuan21",
                [StringComparison]::OrdinalIgnoreCase)) {
            return $Context.InstallRoot
        }
        if ($Kind -ceq "file" -and
            (Test-AllowedH21ReceiptRelativePath $Relative) -and
            -not (Test-ProtectedMiniRelativePath $Relative)) {
            return $Context.InstallRoot
        }
        return $null
    }
    if ($Kind -ceq "directory" -and
        [string]::Equals(
            $PathFull, $Context.EngineState,
            [StringComparison]::OrdinalIgnoreCase)) {
        return $Context.LocalRoot
    }
    if ($Kind -ceq "file" -and
        [string]::Equals(
            $PathFull, $Context.AcceptanceFile,
            [StringComparison]::OrdinalIgnoreCase)) {
        return $Context.LocalRoot
    }
    $Parent = [IO.Path]::GetDirectoryName($PathFull)
    $Name = [IO.Path]::GetFileName($PathFull)
    if ($Kind -ceq "file" -and
        [string]::Equals(
            $Parent, $Context.DownloadsRoot,
            [StringComparison]::OrdinalIgnoreCase) -and
        $Name -cmatch $PbrUpdateArtifactPattern) {
        return $Context.DownloadsRoot
    }
    if ($Kind -ceq "directory" -and
        [string]::Equals(
            $Parent, $Context.TempRoot,
            [StringComparison]::OrdinalIgnoreCase) -and
        $Name -cmatch $TemporaryDirectoryPattern) {
        return $Context.TempRoot
    }
    if ($Kind -ceq "directory" -and
        [string]::Equals(
            $Parent, $Context.DocumentsRoot,
            [StringComparison]::OrdinalIgnoreCase) -and
        $Name -cmatch $LegacyStagingPattern) {
        return $Context.DocumentsRoot
    }
    if ($Kind -ceq "directory" -and
        [string]::Equals(
            $Parent, $Context.BackupParent,
            [StringComparison]::OrdinalIgnoreCase) -and
        $Name -cmatch $AnyH21BackupPattern) {
        return $Context.BackupParent
    }
    if ($Kind -ceq "directory" -and
        [string]::Equals(
            $Parent, $Context.MujassamLocalRoot,
            [StringComparison]::OrdinalIgnoreCase) -and
        $Name -cin @(
            "HunyuanUpdateCache", "UpdaterLogs", "InstallerCache",
            "InstallerLogs"
        )) {
        return $Context.MujassamLocalRoot
    }
    if ($Kind -ceq "directory" -and
        [string]::Equals(
            $Parent, $Context.InstallParent,
            [StringComparison]::OrdinalIgnoreCase) -and
        $Name -cmatch $SetupArtifactDirectoryPattern) {
        # A setup-looking direct child must never be the current installation
        # or one of its ancestors.  Apply the same symmetric overlap rule used
        # during live selection so a forged/replayed journal cannot bypass it.
        if (Test-PathInsideOrEqual $Context.InstallRoot $PathFull) {
            return $null
        }
        try {
            Assert-PathNotProtected $PathFull `
                @($Context.ProtectedRoots) "setup artifact recovery"
        } catch {
            return $null
        }
        return $Context.InstallParent
    }
    return $null
}

function Read-TransactionJournal(
    [string]$Directory,
    [string]$Anchor,
    [hashtable]$Context
) {
    Assert-DirectChildDirectory $Directory $Anchor `
        $QuarantineDirectoryPattern "quarantine recovery"
    $MarkerPath = Join-Path $Directory "transaction.json"
    Assert-NormalFile $MarkerPath "journal recovery"
    $Journal = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
    $ExpectedId = [IO.Path]::GetFileName($Directory).Substring(
        ".MujassamAI-hy21-quarantine-".Length)
    $JournalInstallRoot = Get-NormalizedFullPath `
        ([string]$Journal.install_root) "install_root داخل journal"
    if (-not [string]::Equals(
        $JournalInstallRoot, $Context.InstallRoot,
        [StringComparison]::OrdinalIgnoreCase)) {
        return $null
    }
    $JournalPurpose = [string]$Journal.purpose
    $JournalState = [string]$Journal.state
    if ([int]$Journal.schema_version -ne 2 -or
        [string]$Journal.transaction_id -cne $ExpectedId -or
        $JournalPurpose -cnotin @("restore", "deep-cleanup") -or
        $JournalState -cnotin @("active", "committed")) {
        throw "journal recovery غير صالح: $MarkerPath"
    }
    $QuarantineVolume = Get-NormalizedFullPath `
        ([IO.Path]::GetPathRoot($Directory)) "volume quarantine recovery"
    $RawEntries = @($Journal.entries)
    if ($RawEntries.Count -gt $MaximumReceiptFiles) {
        throw "journal recovery أكبر من الحد الآمن: $MarkerPath"
    }
    $Entries = [Collections.Generic.List[object]]::new()
    $ItemNames = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::Ordinal)
    $OriginalPaths = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    foreach ($RawEntry in $RawEntries) {
        $ItemName = [string]$RawEntry.item_name
        $QuarantineItem = [string]$RawEntry.quarantine_item
        $Kind = [string]$RawEntry.kind
        $OriginalPath = Get-NormalizedFullPath `
            ([string]$RawEntry.original_path) "original_path recovery"
        $ContainmentRoot = Get-NormalizedFullPath `
            ([string]$RawEntry.containment_root) `
            "containment_root recovery"
        $ExpectedContainmentRoot = Get-RecoveryContainmentRoot `
            $OriginalPath $Kind $Context
        $OriginalVolume = Get-NormalizedFullPath `
            ([IO.Path]::GetPathRoot($OriginalPath)) `
            "volume original_path recovery"
        $OriginalHash = [string]$RawEntry.original_sha256
        $ReplacementHash = [string]$RawEntry.replacement_sha256
        $EntryState = [string]$RawEntry.state
        if ($ItemName -cnotmatch $QuarantineItemPattern -or
            $QuarantineItem -cne $ItemName -or
            -not $ItemNames.Add($ItemName) -or
            -not $OriginalPaths.Add($OriginalPath) -or
            [string]::IsNullOrWhiteSpace($ExpectedContainmentRoot) -or
            -not [string]::Equals(
                $ContainmentRoot, $ExpectedContainmentRoot,
                [StringComparison]::OrdinalIgnoreCase) -or
            -not (Test-PathInsideOrEqual $OriginalPath $ContainmentRoot) -or
            [string]::Equals(
                $OriginalPath, $ContainmentRoot,
                [StringComparison]::OrdinalIgnoreCase) -or
            -not [string]::Equals(
                $OriginalVolume, $QuarantineVolume,
                [StringComparison]::OrdinalIgnoreCase) -or
            $Kind -cnotin @("file", "directory") -or
            $EntryState -cnotin @(
                "planned", "moved", "replacement-written",
                "rolled-back", "purged"
            )) {
            throw "entry غير آمن داخل journal: $MarkerPath"
        }
        if ($JournalState -ceq "committed" -and
            $EntryState -cnotin @("moved", "replacement-written", "purged")) {
            throw "entry committed بحالة غير صالحة: $MarkerPath"
        }
        if ($Kind -ceq "file") {
            if ($OriginalHash -notmatch '^[0-9a-f]{64}$' -or
                (-not [string]::IsNullOrWhiteSpace($ReplacementHash) -and
                 $ReplacementHash -notmatch '^[0-9a-f]{64}$')) {
                throw "بصمة file غير صالحة داخل journal: $MarkerPath"
            }
        } elseif (-not [string]::IsNullOrWhiteSpace($OriginalHash) -or
            -not [string]::IsNullOrWhiteSpace($ReplacementHash)) {
            throw "directory يحمل بصمة غير صالحة داخل journal: $MarkerPath"
        }
        if ($JournalPurpose -ceq "restore") {
            if ($Kind -cne "file" -or
                $ReplacementHash -notmatch '^[0-9a-f]{64}$' -or
                ($JournalState -ceq "committed" -and
                 $EntryState -cnotin @("replacement-written", "purged"))) {
                throw "entry restore غير متوافق مع journal: $MarkerPath"
            }
        } elseif (-not [string]::IsNullOrWhiteSpace($ReplacementHash) -or
            $EntryState -ceq "replacement-written" -or
            ($JournalState -ceq "committed" -and
             $EntryState -cnotin @("moved", "purged"))) {
            throw "entry deep-cleanup غير متوافق مع journal: $MarkerPath"
        }
        $QuarantinedPath = Join-Path $Directory $ItemName
        if ($Kind -ceq "directory" -and
            [string]::Equals(
                [IO.Path]::GetDirectoryName($OriginalPath),
                $Context.InstallParent,
                [StringComparison]::OrdinalIgnoreCase) -and
            [IO.Path]::GetFileName($OriginalPath) -cmatch `
                $SetupArtifactDirectoryPattern) {
            # Setup leftovers are never allowed to carry user exports/output.
            # Inspect whichever side of the atomic move currently exists and
            # refuse reparse points through Test-TreeContainsUserOutput.
            $SetupTreeToInspect = $null
            if (Test-Path -LiteralPath $QuarantinedPath) {
                $SetupTreeToInspect = $QuarantinedPath
            } elseif (Test-Path -LiteralPath $OriginalPath) {
                $SetupTreeToInspect = $OriginalPath
            }
            if ($null -ne $SetupTreeToInspect -and
                (Test-TreeContainsUserOutput `
                    $SetupTreeToInspect "setup artifact recovery")) {
                throw (
                    "setup artifact recovery يحتوي export/output أو .git: " +
                    $OriginalPath
                )
            }
        }
        $Entries.Add([pscustomobject]@{
            ItemName = $ItemName
            OriginalPath = $OriginalPath
            ContainmentRoot = $ContainmentRoot
            QuarantinedPath = $QuarantinedPath
            IsDirectory = ($Kind -ceq "directory")
            Kind = $Kind
            OriginalSha256 = $(if ($Kind -ceq "file") {
                $OriginalHash
            } else { $null })
            ReplacementSha256 = $(if (
                [string]::IsNullOrWhiteSpace($ReplacementHash)) {
                $null
            } else { $ReplacementHash })
            State = $EntryState
            RootRecord = $null
            Label = "startup recovery"
        })
    }
    $RootRecord = [pscustomobject]@{
        Path = (Get-NormalizedFullPath $Directory "quarantine recovery")
        Anchor = (Get-NormalizedFullPath $Anchor "anchor recovery")
        Entries = $Entries
    }
    foreach ($Entry in $Entries) {
        $Entry.RootRecord = $RootRecord
    }
    return [pscustomobject]@{
        Id = $ExpectedId
        Purpose = $JournalPurpose
        State = $JournalState
        InstallRoot = $JournalInstallRoot
        RootRecord = $RootRecord
    }
}

function Assert-HarmlessQuarantineWithoutJournal(
    [string]$Directory,
    [string]$Anchor
) {
    Assert-DirectChildDirectory $Directory $Anchor `
        $QuarantineDirectoryPattern "quarantine بلا journal"
    foreach ($Entry in @(
        [IO.Directory]::EnumerateFileSystemEntries($Directory)
    )) {
        $Name = [IO.Path]::GetFileName($Entry)
        if ($Name -cnotmatch $QuarantineTemporaryMarkerPattern -or
            -not (Test-Path -LiteralPath $Entry -PathType Leaf)) {
            throw "quarantine بلا journal يحتوي عنصرًا غير آمن: $Entry"
        }
        Assert-NormalFile $Entry "ملف journal مؤقت بلا journal"
    }
    # Do not delete a markerless root: another remover may be between creating
    # the directory and atomically publishing its initial journal.  Empty or
    # temp-only roots contain no moved user data and are safe to leave behind.
    Write-Verbose "ترك quarantine فارغ/مؤقت بلا journal: $Directory"
}

function Recover-PendingQuarantineTransactions(
    [string[]]$Anchors,
    [hashtable]$Context
) {
    $Found = [Collections.Generic.List[object]]::new()
    $SeenAnchors = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    foreach ($AnchorCandidate in $Anchors) {
        if ([string]::IsNullOrWhiteSpace($AnchorCandidate)) {
            continue
        }
        $Anchor = Get-NormalizedFullPath $AnchorCandidate `
            "anchor startup recovery"
        if (-not $SeenAnchors.Add($Anchor) -or
            -not (Test-Path -LiteralPath $Anchor -PathType Container)) {
            continue
        }
        Assert-NormalDirectory $Anchor "anchor startup recovery"
        foreach ($Directory in @(
            [IO.Directory]::EnumerateDirectories($Anchor) |
                Where-Object {
                    [IO.Path]::GetFileName($_) -cmatch `
                        $QuarantineDirectoryPattern
                }
        )) {
            $JournalPath = Join-Path $Directory "transaction.json"
            if (-not (Test-Path -LiteralPath $JournalPath -PathType Leaf)) {
                Assert-HarmlessQuarantineWithoutJournal $Directory $Anchor
                continue
            }
            $Parsed = Read-TransactionJournal $Directory $Anchor $Context
            if ($null -ne $Parsed) {
                $Found.Add($Parsed)
            }
        }
    }
    foreach ($Group in @($Found | Group-Object -Property Id)) {
        $Parts = @($Group.Group)
        $Purpose = [string]$Parts[0].Purpose
        foreach ($Part in $Parts) {
            if ([string]$Part.Purpose -cne $Purpose -or
                -not [string]::Equals(
                    [string]$Part.InstallRoot, $Context.InstallRoot,
                    [StringComparison]::OrdinalIgnoreCase)) {
                throw "أجزاء transaction غير متوافقة: $($Group.Name)"
            }
        }
        $AnyActive = @($Parts | Where-Object {
            [string]$_.State -ceq "active"
        }).Count -ne 0
        $Transaction = [pscustomobject]@{
            Id = [string]$Group.Name
            Purpose = $Purpose
            State = $(if ($AnyActive) { "active" } else { "committed" })
            InstallRoot = $Context.InstallRoot
            ProtectedRoots = @($Context.ProtectedRoots)
            Roots = [Collections.Generic.Dictionary[string, object]]::new(
                [StringComparer]::OrdinalIgnoreCase)
            Moves = [Collections.Generic.List[object]]::new()
        }
        $RecoveredItemNames = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::Ordinal)
        $RecoveredOriginalPaths = [Collections.Generic.HashSet[string]]::new(
            [StringComparer]::OrdinalIgnoreCase)
        foreach ($Part in $Parts) {
            $RootRecord = $Part.RootRecord
            $VolumeKey = (Get-NormalizedFullPath `
                ([IO.Path]::GetPathRoot($RootRecord.Path)) `
                "volume recovery").ToUpperInvariant()
            if ($Transaction.Roots.ContainsKey($VolumeKey)) {
                throw "transaction يملك quarantine مكررًا على volume واحد."
            }
            $Transaction.Roots.Add($VolumeKey, $RootRecord)
            foreach ($Entry in @($RootRecord.Entries)) {
                if (-not $RecoveredItemNames.Add([string]$Entry.ItemName) -or
                    -not $RecoveredOriginalPaths.Add(
                        [string]$Entry.OriginalPath)) {
                    throw "transaction يحتوي item/original مكررًا: $($Group.Name)"
                }
                $Transaction.Moves.Add($Entry)
            }
        }
        Assert-DeepCleanupPhaseReady $Context.InstallRoot
        if ($AnyActive) {
            if (@($Transaction.Moves | Where-Object {
                [string]$_.State -ceq "purged"
            }).Count -ne 0) {
                throw "transaction active يحتوي item purged؛ أُوقف recovery."
            }
            Write-Host "استرجاع transaction انقطع سابقًا: $($Group.Name)" `
                -ForegroundColor Yellow
            Undo-QuarantineTransaction $Transaction
        } else {
            Write-Host "إكمال purge لمعاملة committed سابقة: $($Group.Name)" `
                -ForegroundColor Yellow
            Complete-QuarantineTransaction $Transaction
        }
    }
}

function Complete-QuarantineTransaction([object]$Transaction) {
    if ([string]$Transaction.State -cne "committed") {
        throw "رُفض purge لمعاملة غير committed: $($Transaction.Id)"
    }
    foreach ($RootRecord in @($Transaction.Roots.Values)) {
        if (-not (Test-Path -LiteralPath $RootRecord.Path)) {
            continue
        }
        Assert-DirectChildDirectory $RootRecord.Path $RootRecord.Anchor `
            $QuarantineDirectoryPattern "quarantine commit"
        $MarkerPath = Join-Path $RootRecord.Path "transaction.json"
        Assert-NormalFile $MarkerPath "journal committed"
        foreach ($Entry in @($RootRecord.Entries)) {
            if ([string]$Entry.ItemName -cnotmatch $QuarantineItemPattern) {
                throw "اسم item غير صالح عند purge: $($Entry.ItemName)"
            }
            $ItemPath = Join-Path $RootRecord.Path ([string]$Entry.ItemName)
            Assert-NoReparsePointInExistingPath $RootRecord.Path $ItemPath
            if ([string]$Entry.State -ceq "purged") {
                if (Test-Path -LiteralPath $ItemPath) {
                    throw "item purged عاد للظهور؛ أُوقف purge: $ItemPath"
                }
                continue
            }
            if (Test-Path -LiteralPath $ItemPath) {
                if ([string]$Entry.Kind -ceq "directory") {
                    Assert-NormalTree $ItemPath "directory committed"
                    [IO.Directory]::Delete($ItemPath, $true)
                } elseif ([string]$Entry.Kind -ceq "file") {
                    Assert-NormalFile $ItemPath "file committed"
                    if ([string]$Entry.OriginalSha256 -notmatch `
                            '^[0-9a-f]{64}$' -or
                        (Get-Sha256 $ItemPath) -cne
                            [string]$Entry.OriginalSha256) {
                        throw "بصمة item committed غير صحيحة: $ItemPath"
                    }
                    [IO.File]::Delete($ItemPath)
                } else {
                    throw "نوع item غير صالح عند purge: $($Entry.Kind)"
                }
            }
            $Entry.State = "purged"
            # Keep transaction.json durable until this and every other item is
            # gone; a crash simply resumes the remaining entries next run.
            Write-TransactionJournalAtomic $Transaction $RootRecord
        }
        foreach ($Temporary in @(
            [IO.Directory]::EnumerateFiles($RootRecord.Path) |
                Where-Object {
                    [IO.Path]::GetFileName($_) -cmatch `
                        $QuarantineTemporaryMarkerPattern
                }
        )) {
            Assert-NormalFile $Temporary "ملف journal مؤقت committed"
            [IO.File]::Delete($Temporary)
        }
        $Unexpected = @(
            [IO.Directory]::EnumerateFileSystemEntries($RootRecord.Path) |
                Where-Object {
                    [IO.Path]::GetFileName($_) -cne "transaction.json"
                }
        )
        if ($Unexpected.Count -ne 0) {
            throw "عنصر غير موثق داخل quarantine committed: $($Unexpected -join ', ')"
        }
        [IO.File]::Delete($MarkerPath)
        [IO.Directory]::Delete($RootRecord.Path, $false)
    }
}

function Restore-OriginalFileTransactional(
    [string]$BackupRoot,
    [string]$InstallRoot,
    [object]$Entry,
    [object]$Transaction
) {
    $Relative = ([string]$Entry.path).Replace('\', '/')
    $BackupFile = Get-ContainedPath $BackupRoot $Relative
    $Destination = Get-ContainedPath $InstallRoot $Relative
    $ExpectedHash = [string]$Entry.sha256
    $CurrentHash = Get-Sha256 $Destination
    if ($CurrentHash -ceq $ExpectedHash) {
        return
    }
    $AllowedInstalledHash = [string]$Entry.installed_sha256
    $Parent = [IO.Path]::GetDirectoryName($Destination)
    $TemporaryDestination = Join-Path $Parent (
        "MujassamAI-remove-hy21-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::Copy($BackupFile, $TemporaryDestination, $false)
        if ((Get-Sha256 $TemporaryDestination) -cne $ExpectedHash) {
            throw "فشل فحص ملف Mini المؤقت: $Relative"
        }
        Assert-DeepCleanupPhaseReady $InstallRoot
        Assert-NoReparsePointInExistingPath $InstallRoot $Destination
        $LatestHash = Get-Sha256 $Destination
        if ($LatestHash -cne $AllowedInstalledHash -and
            $LatestHash -cne $ExpectedHash -and
            -not (Test-H21EngineRelativePath $Relative)) {
            throw "تغيّر الملف قبل الاسترجاع مباشرة: $Relative"
        }
        if ($LatestHash -ceq $ExpectedHash) {
            return
        }
        $Move = Move-ToTransactionQuarantine `
            $Transaction $Destination $InstallRoot $InstallRoot `
            "ملف H21 مستبدل" $ExpectedHash
        [IO.File]::Move($TemporaryDestination, $Destination, $false)
        if ((Get-Sha256 $Destination) -cne $ExpectedHash) {
            throw "فشل فحص ملف Mini المسترجع: $Relative"
        }
        $Move.State = "replacement-written"
        Write-TransactionJournalAtomic $Transaction $Move.RootRecord
    } finally {
        Remove-TemporaryFileBestEffort $TemporaryDestination
    }
}

function Test-MarkerFreeMiniWorker([string]$WorkerPath) {
    Assert-NormalFile $WorkerPath "Mini worker"
    if ((Get-Item -LiteralPath $WorkerPath).Length -gt 10MB) {
        return $false
    }
    $Source = [IO.File]::ReadAllText($WorkerPath)
    foreach ($UltimateMarker in @(
        "hunyuan3d_2_1_pbr", "hunyuan21_worker.py"
    )) {
        if ($Source.IndexOf(
            $UltimateMarker, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $false
        }
    }
    return $true
}

function Test-MarkerFreeMiniExecutable([string]$ExecutablePath) {
    try {
        Assert-NormalFile $ExecutablePath "ملف Mini التنفيذي"
        $File = Get-Item -LiteralPath $ExecutablePath
        if ($File.Length -lt 2 -or $File.Length -gt 64MB) {
            return $false
        }
        $Bytes = [IO.File]::ReadAllBytes($ExecutablePath)
        if ($Bytes[0] -ne 0x4d -or $Bytes[1] -ne 0x5a) {
            return $false
        }
        $Utf8Text = [Text.Encoding]::UTF8.GetString($Bytes)
        # Scan UTF-16LE at both possible byte alignments inside a PE.  PE
        # string data is commonly aligned, but marker detection must not rely
        # on that layout detail.
        $Utf16EvenText = [Text.Encoding]::Unicode.GetString($Bytes)
        $Utf16OddText = $(if ($Bytes.Length -gt 1) {
            [Text.Encoding]::Unicode.GetString(
                $Bytes, 1, $Bytes.Length - 1)
        } else {
            ""
        })
        foreach ($UltimateMarker in @(
            "hunyuan3d_2_1_pbr",
            "Hunyuan3D 2.1 Ultimate",
            "hunyuan21_worker.py"
        )) {
            if ($Utf8Text.IndexOf(
                    $UltimateMarker,
                    [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                $Utf16EvenText.IndexOf(
                    $UltimateMarker,
                    [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
                $Utf16OddText.IndexOf(
                    $UltimateMarker,
                    [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                return $false
            }
        }
        return $true
    } catch {
        Write-Verbose "رفض ملف Mini التنفيذي: $($_.Exception.Message)"
        return $false
    }
}

function Test-MiniCompletionMarker(
    [string]$MarkerPath,
    [string]$MarkerRoot,
    [string]$ExpectedInstallRoot,
    [hashtable]$ExpectedHashes
) {
    try {
        Assert-NoReparsePointInExistingPath $MarkerRoot $MarkerPath
        Assert-NormalFile $MarkerPath "علامة اكتمال Mini"
        $Marker = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
        if ([int]$Marker.schema_version -ne 1 -or
            -not [string]::Equals(
                (Get-NormalizedFullPath ([string]$Marker.install_root) `
                    "install_root داخل العلامة"),
                $ExpectedInstallRoot,
                [StringComparison]::OrdinalIgnoreCase)) {
            return $false
        }
        foreach ($Name in $ExpectedHashes.Keys) {
            $Recorded = [string]$Marker.$Name
            if ($Recorded -notmatch '^[0-9a-f]{64}$' -or
                $Recorded -cne [string]$ExpectedHashes[$Name]) {
                return $false
            }
        }
        return $true
    } catch {
        Write-Verbose "علامة Mini غير موثوقة: $($_.Exception.Message)"
        return $false
    }
}

function Write-MiniCompletionMarker(
    [string]$MarkerPath,
    [string]$MarkerRoot,
    [string]$InstallRoot,
    [hashtable]$Hashes
) {
    Assert-NoReparsePointInExistingPath $MarkerRoot $MarkerPath
    if (Test-Path -LiteralPath $MarkerPath) {
        Assert-NormalFile $MarkerPath "علامة اكتمال Mini القديمة"
    }
    $MarkerParent = [IO.Path]::GetDirectoryName($MarkerPath)
    [IO.Directory]::CreateDirectory($MarkerParent) | Out-Null
    Assert-NoReparsePointInExistingPath $MarkerRoot $MarkerParent
    $TemporaryMarker = Join-Path $MarkerParent (
        "mini-only-restored-v1-" + [Guid]::NewGuid().ToString("N") + ".tmp")
    try {
        $Document = [ordered]@{
            schema_version = 1
            completed_utc = (Get-Date).ToUniversalTime().ToString("o")
            install_root = $InstallRoot
            mujassam_exe_sha256 = [string]$Hashes.mujassam_exe_sha256
            mini_worker_sha256 = [string]$Hashes.mini_worker_sha256
            hunyuan2_worker_sha256 = [string]$Hashes.hunyuan2_worker_sha256
            hunyuan2_manifest_sha256 = [string]$Hashes.hunyuan2_manifest_sha256
        }
        [IO.File]::WriteAllText(
            $TemporaryMarker, ($Document | ConvertTo-Json -Depth 4),
            [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $MarkerPath) {
            [IO.File]::Move($TemporaryMarker, $MarkerPath, $true)
        } else {
            [IO.File]::Move($TemporaryMarker, $MarkerPath, $false)
        }
    } finally {
        Remove-TemporaryFileBestEffort $TemporaryMarker
    }
}

if ($PSVersionTable.PSVersion -lt [Version]"7.4" -or -not $IsWindows -or
    -not [Environment]::Is64BitOperatingSystem -or
    -not [Environment]::Is64BitProcess) {
    throw "إزالة Hunyuan3D 2.1 تتطلب PowerShell 7.4 أو أحدث على Windows x64."
}

$RootPath = Get-NormalizedFullPath $InstallRoot "مجلد التثبيت"
Assert-NormalDirectory $RootPath "مجلد MujassamAI-Portable"
$ForbiddenInstallRoots = @(
    [IO.Path]::GetPathRoot($RootPath),
    (Get-NormalizedFullPath $env:USERPROFILE "مجلد المستخدم"),
    (Get-NormalizedFullPath $env:WINDIR "مجلد Windows")
)
foreach ($ForbiddenRoot in $ForbiddenInstallRoots) {
    if ([string]::Equals(
        $RootPath, $ForbiddenRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw "مسار التثبيت واسع أو حساس أكثر من اللازم: $RootPath"
    }
}

# Resolve only trusted roots, then recover interrupted atomic moves before
# asserting that EXE/worker files exist.  A closed PowerShell window may have
# stopped exactly between destination -> quarantine and replacement -> target.
if ([string]::IsNullOrWhiteSpace($LocalApplicationDataRoot)) {
    $LocalApplicationDataRoot = [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::LocalApplicationData)
}
if ([string]::IsNullOrWhiteSpace($UserProfileRoot)) {
    $UserProfileRoot = $env:USERPROFILE
}
if ([string]::IsNullOrWhiteSpace($TemporaryRoot)) {
    $TemporaryRoot = [IO.Path]::GetTempPath()
}
$LocalRoot = Get-NormalizedFullPath $LocalApplicationDataRoot "LocalAppData"
$ProfileRoot = Get-NormalizedFullPath $UserProfileRoot "مجلد المستخدم"
$TempRoot = Get-NormalizedFullPath $TemporaryRoot "مجلد Windows المؤقت"
Assert-NormalDirectory $LocalRoot "LocalAppData"
Assert-NormalDirectory $ProfileRoot "مجلد المستخدم"
Assert-NormalDirectory $TempRoot "مجلد Windows المؤقت"

$MujassamLocalRoot = Join-Path $LocalRoot "MujassamAI"
$BackupParent = Join-Path $MujassamLocalRoot "Backups"
$InstalledH21Engine = Get-ContainedPath $RootPath "app/engines/hunyuan21"
$EngineState = Join-Path $MujassamLocalRoot "Engines\Hunyuan3D-2.1"
$MiniEngineState = Join-Path $MujassamLocalRoot "Engines\Hunyuan3D-2"
$AcceptanceFile = Join-Path $MujassamLocalRoot `
    "Licenses\acceptance-v2-1.txt"
$DocumentsRoot = Join-Path $ProfileRoot "Documents"
$DownloadsRoot = Join-Path $ProfileRoot "Downloads"
$InstallParent = [IO.Path]::GetDirectoryName($RootPath)
$ProtectedRoots = @(
    (Get-ContainedPath $RootPath "app/engines/hunyuan2"),
    (Get-ContainedPath $RootPath "rt"),
    (Get-ContainedPath $RootPath "models"),
    (Get-ContainedPath $RootPath "app/vendor"),
    (Get-ContainedPath $RootPath "exports"),
    (Get-ContainedPath $RootPath "export"),
    (Get-ContainedPath $RootPath "output"),
    (Get-ContainedPath $RootPath "outputs"),
    (Get-ContainedPath $RootPath "app/exports"),
    (Get-ContainedPath $RootPath "app/export"),
    (Get-ContainedPath $RootPath "app/output"),
    (Get-ContainedPath $RootPath "app/outputs"),
    $MiniEngineState,
    (Join-Path $DownloadsRoot "MujassamAI-Exports"),
    (Join-Path $DocumentsRoot "MujassamAI-Exports")
)
$RecoveryContext = @{
    InstallRoot = $RootPath
    LocalRoot = $LocalRoot
    MujassamLocalRoot = $MujassamLocalRoot
    BackupParent = $BackupParent
    EngineState = $EngineState
    AcceptanceFile = $AcceptanceFile
    DocumentsRoot = $DocumentsRoot
    DownloadsRoot = $DownloadsRoot
    TempRoot = $TempRoot
    InstallParent = $InstallParent
    ProtectedRoots = $ProtectedRoots
}
Assert-DeepCleanupPhaseReady $RootPath
Recover-PendingQuarantineTransactions `
    @($RootPath, $LocalRoot, $DownloadsRoot, $TempRoot,
      $InstallParent, $DocumentsRoot) $RecoveryContext

foreach ($RequiredRelative in @(
    $MiniExecutableRelative,
    $MiniWorkerRelative,
    "rt/python.exe",
    "app/engines/hunyuan2/hunyuan2_worker.py",
    "app/engines/hunyuan2/ENGINE-MANIFEST.json"
)) {
    $RequiredPath = Get-ContainedPath $RootPath $RequiredRelative
    Assert-NoReparsePointInExistingPath $RootPath $RequiredPath
    Assert-NormalFile $RequiredPath "ملف النسخة المحمولة المطلوب"
}
$Hunyuan2Worker = Get-ContainedPath $RootPath `
    "app/engines/hunyuan2/hunyuan2_worker.py"
$Hunyuan2Manifest = Get-ContainedPath $RootPath `
    "app/engines/hunyuan2/ENGINE-MANIFEST.json"
$Hunyuan2WorkerSha256 = Get-Sha256 $Hunyuan2Worker
$Hunyuan2ManifestSha256 = Get-Sha256 $Hunyuan2Manifest
$PortablePython = Get-ContainedPath $RootPath "rt/python.exe"
Assert-DeepCleanupPhaseReady $RootPath

Assert-NoReparsePointInExistingPath $LocalRoot $MujassamLocalRoot
if (-not (Test-Path -LiteralPath $MujassamLocalRoot)) {
    [IO.Directory]::CreateDirectory($MujassamLocalRoot) | Out-Null
}
Assert-NormalDirectory $MujassamLocalRoot "مجلد MujassamAI المحلي"
Assert-NoReparsePointInExistingPath $MujassamLocalRoot $BackupParent
if (Test-Path -LiteralPath $BackupParent) {
    Assert-NormalDirectory $BackupParent "مجلد نسخ Mujassam AI الاحتياطية"
}

$MiniExecutable = Get-ContainedPath $RootPath $MiniExecutableRelative
$MiniWorker = Get-ContainedPath $RootPath $MiniWorkerRelative
$CurrentMiniHashes = @{
    mujassam_exe_sha256 = (Get-Sha256 $MiniExecutable)
    mini_worker_sha256 = (Get-Sha256 $MiniWorker)
    hunyuan2_worker_sha256 = $Hunyuan2WorkerSha256
    hunyuan2_manifest_sha256 = $Hunyuan2ManifestSha256
}
$CompletionMarker = Join-Path $MujassamLocalRoot "mini-only-restored-v1.json"
$HasValidCompletionMarker = $false
if (Test-Path -LiteralPath $CompletionMarker) {
    $HasValidCompletionMarker = Test-MiniCompletionMarker `
        $CompletionMarker $MujassamLocalRoot $RootPath $CurrentMiniHashes
}
$CurrentWorkerIsMarkerFree = Test-MarkerFreeMiniWorker $MiniWorker
$CurrentExecutableIsMarkerFree = Test-MarkerFreeMiniExecutable $MiniExecutable

Write-Host "فحص النسخ الاحتياطية واختيار أحدث نسخة Mini موثوقة ومتوافقة..." `
    -ForegroundColor Cyan
$BaseBackupDirectories = @()
if (Test-Path -LiteralPath $BackupParent -PathType Container) {
    $BaseBackupDirectories = @(
        [IO.Directory]::EnumerateDirectories($BackupParent) |
            Where-Object {
                [IO.Path]::GetFileName($_) -cmatch $BaseBackupPattern
            } |
            Sort-Object { [IO.Path]::GetFileName($_) } -Descending
    )
}
$TrustedBaselines = [Collections.Generic.List[object]]::new()
foreach ($Candidate in $BaseBackupDirectories) {
    # A matching reparse point is an unsafe cleanup target, not a candidate to
    # silently ignore.
    Assert-DirectChildDirectory $Candidate $BackupParent `
        $BaseBackupPattern "نسخة Hunyuan3D 2.1 الاحتياطية"
    $Trusted = Get-TrustedMiniBaseline $Candidate $RootPath
    if ($null -ne $Trusted) {
        # A marker-free receipt is not enough: every current non-H21 path must
        # still match either the receipt's installed hash or its Mini hash.
        $Compatible = $true
        foreach ($Entry in @($Trusted.Overwritten)) {
            $Relative = ([string]$Entry.path).Replace('\', '/')
            if (Test-H21EngineRelativePath $Relative) {
                continue
            }
            $Destination = Get-ContainedPath $RootPath $Relative
            if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
                $Compatible = $false
                break
            }
            $CurrentHash = Get-Sha256 $Destination
            if ($CurrentHash -cne [string]$Entry.installed_sha256 -and
                $CurrentHash -cne [string]$Entry.sha256) {
                $Compatible = $false
                break
            }
        }
        if ($Compatible) {
            foreach ($Entry in @($Trusted.Created)) {
                $Relative = ([string]$Entry.path).Replace('\', '/')
                if (Test-H21EngineRelativePath $Relative) {
                    continue
                }
                $Destination = Get-ContainedPath $RootPath $Relative
                if (Test-Path -LiteralPath $Destination) {
                    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
                        (Get-Sha256 $Destination) -cne
                            [string]$Entry.installed_sha256) {
                        $Compatible = $false
                        break
                    }
                }
            }
        }
        if ($Compatible) {
            $TrustedBaselines.Add($Trusted)
        }
    }
}
$Baseline = $null
$BackupRoot = $null
$Overwritten = @()
$Created = @()
$MiniExecutableOriginal = $null
$MiniWorkerOriginal = $null
if ($TrustedBaselines.Count -ne 0) {
    # REMOVE_HY21_LATEST_COMPATIBLE_MINI_BASELINE: PBR-tainted workers are
    # rejected by Get-TrustedMiniBaseline before this newest-first selection.
    $Baseline = $TrustedBaselines[0]
    $BackupRoot = [string]$Baseline.Path
    Write-Host "تم اعتماد نسخة Mini الأصلية: $BackupRoot" -ForegroundColor Green
    $Overwritten = @($Baseline.Overwritten)
    $Created = @($Baseline.Created)
    $MiniExecutableOriginal = $Baseline.OverwrittenMap[$MiniExecutableRelative]
    $MiniWorkerOriginal = $Baseline.OverwrittenMap[$MiniWorkerRelative]
} elseif (-not $CurrentWorkerIsMarkerFree -or
    -not $CurrentExecutableIsMarkerFree -or
    -not $HasValidCompletionMarker) {
    throw (
        "لا توجد نسخة Mini موثوقة ولا علامة اكتمال تطابق بصمات EXE/worker/Hunyuan2. " +
        "لم يُحذف أو يُستبدل أي ملف."
    )
} else {
    Write-Host "نسخة Mini الحالية مثبتة بعلامة اكتمال موثوقة؛ لا تحتاج rollback." `
        -ForegroundColor Green
}
$AllPaths = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase)

# Validate the complete selected receipt and every current destination before
# the first mutation.  Only files inside the receipt-owned H21 engine may have
# hotfix hashes that differ from the originally installed update.
Write-Host "فحص بصمات ملفات Mini وUltimate الحالية قبل أي تغيير..." `
    -ForegroundColor Cyan
foreach ($Entry in $Overwritten) {
    $Relative = ([string]$Entry.path).Replace('\', '/')
    if (-not (Test-SafeRelativePath $Relative) -or -not $AllPaths.Add($Relative)) {
        throw "مسار overwritten مكرر أو غير آمن: $Relative"
    }
    if (-not (Test-AllowedH21ReceiptRelativePath $Relative)) {
        throw "مسار overwritten خارج allowlist تحديث H21: $Relative"
    }
    if (Test-ProtectedMiniRelativePath $Relative) {
        throw "إيصال التثبيت يحاول المساس بمسار Mini محمي: $Relative"
    }
    $ExpectedHash = [string]$Entry.sha256
    $InstalledHash = [string]$Entry.installed_sha256
    $BackupFile = Get-ContainedPath $BackupRoot $Relative
    Assert-NoReparsePointInExistingPath $BackupRoot $BackupFile
    Assert-NormalFile $BackupFile "ملف Mini الاحتياطي"
    if ($ExpectedHash -notmatch '^[0-9a-f]{64}$' -or
        $InstalledHash -notmatch '^[0-9a-f]{64}$' -or
        [Int64]$Entry.bytes -lt 0 -or
        (Get-Item -LiteralPath $BackupFile).Length -ne [Int64]$Entry.bytes -or
        (Get-Sha256 $BackupFile) -cne $ExpectedHash) {
        throw "فشل تحقق ملف Mini الاحتياطي: $Relative"
    }
    $Destination = Get-ContainedPath $RootPath $Relative
    Assert-NoReparsePointInExistingPath $RootPath $Destination
    Assert-NormalFile $Destination "ملف التثبيت الحالي"
    $CurrentHash = Get-Sha256 $Destination
    if ($CurrentHash -cne $InstalledHash -and
        $CurrentHash -cne $ExpectedHash -and
        -not (Test-H21EngineRelativePath $Relative)) {
        throw "تغيّر ملف خارج Hunyuan3D 2.1؛ رُفضت الإزالة: $Relative"
    }
}
foreach ($Entry in $Created) {
    $Relative = ([string]$Entry.path).Replace('\', '/')
    if (-not (Test-SafeRelativePath $Relative) -or -not $AllPaths.Add($Relative)) {
        throw "مسار created مكرر أو غير آمن: $Relative"
    }
    if (-not (Test-AllowedH21ReceiptRelativePath $Relative)) {
        throw "مسار created خارج allowlist تحديث H21: $Relative"
    }
    if (Test-ProtectedMiniRelativePath $Relative) {
        throw "إيصال التثبيت يحاول حذف مسار Mini محمي: $Relative"
    }
    $InstalledHash = [string]$Entry.installed_sha256
    if ($InstalledHash -notmatch '^[0-9a-f]{64}$') {
        throw "بصمة created غير صالحة: $Relative"
    }
    if (Test-H21EngineRelativePath $Relative) {
        # REMOVE_HY21_DEFER_RECEIPT_TREE: do not stat/hash thousands of add-on
        # files; Assert-NormalTree validates the exact root once below.
        continue
    }
    $Destination = Get-ContainedPath $RootPath $Relative
    Assert-NoReparsePointInExistingPath $RootPath $Destination
    if (Test-Path -LiteralPath $Destination) {
        Assert-NormalFile $Destination "ملف أضافه تحديث Hunyuan3D 2.1"
        if ((Get-Sha256 $Destination) -cne $InstalledHash) {
            throw "تغيّر ملف أضافه التحديث خارج مجلد H21؛ رُفض حذفه: $Relative"
        }
    }
}

# Preflight every exact cleanup target before restoring Mini.  Exports/output,
# the base runtime, Mini models, vendor, and Hunyuan2 are explicit deny roots.
Write-Host "فحص أمان مجلدات Ultimate والكاش قبل التنظيف..." `
    -ForegroundColor Cyan
foreach ($Pair in @(
    @($InstalledH21Engine, $RootPath),
    @($EngineState, $LocalRoot),
    @($AcceptanceFile, $LocalRoot),
    @($DocumentsRoot, $ProfileRoot),
    @($DownloadsRoot, $ProfileRoot)
)) {
    Assert-NoReparsePointInExistingPath $Pair[1] $Pair[0]
}

if (Test-Path -LiteralPath $InstalledH21Engine) {
    Assert-PathNotProtected $InstalledH21Engine $ProtectedRoots `
        "مجلد Ultimate/PBR المثبت"
    Assert-NormalTree $InstalledH21Engine "مجلد Ultimate/PBR المثبت"
}
if (Test-Path -LiteralPath $EngineState) {
    Assert-PathNotProtected $EngineState $ProtectedRoots `
        "حالة Hunyuan3D 2.1 المحلية"
    Assert-NormalTree $EngineState "حالة Hunyuan3D 2.1 المحلية"
}
if (Test-Path -LiteralPath $AcceptanceFile) {
    Assert-PathNotProtected $AcceptanceFile $ProtectedRoots `
        "موافقة Hunyuan3D 2.1"
    Assert-NormalFile $AcceptanceFile "موافقة Hunyuan3D 2.1"
}

$TempDirectories = @(
    [IO.Directory]::EnumerateDirectories($TempRoot) |
        Where-Object {
            [IO.Path]::GetFileName($_) -cmatch $TemporaryDirectoryPattern
        }
)
$LegacyStagingDirectories = @()
if (Test-Path -LiteralPath $DocumentsRoot -PathType Container) {
    Assert-NormalDirectory $DocumentsRoot "مجلد Documents"
    $LegacyStagingDirectories = @(
        [IO.Directory]::EnumerateDirectories($DocumentsRoot) |
            Where-Object {
                [IO.Path]::GetFileName($_) -cmatch $LegacyStagingPattern
            }
    )
}
$GlobalCleanupSelection = Select-InactiveH21CleanupDirectories `
    (@($TempDirectories) + @($LegacyStagingDirectories))
$SelectedCleanupPaths = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase)
foreach ($SelectedPath in @($GlobalCleanupSelection.Paths)) {
    $SelectedCleanupPaths.Add(
        (Get-NormalizedFullPath $SelectedPath "هدف cleanup")) | Out-Null
}
$TempDirectories = @($TempDirectories | Where-Object {
    $SelectedCleanupPaths.Contains(
        (Get-NormalizedFullPath $_ "مجلد H21 المؤقت"))
})
$LegacyStagingDirectories = @($LegacyStagingDirectories | Where-Object {
    $SelectedCleanupPaths.Contains(
        (Get-NormalizedFullPath $_ "مجلد H21 staging"))
})
foreach ($Directory in $TempDirectories) {
    Assert-DirectChildDirectory $Directory $TempRoot `
        $TemporaryDirectoryPattern "مجلد H21 المؤقت"
}
foreach ($Directory in $LegacyStagingDirectories) {
    Assert-DirectChildDirectory $Directory $DocumentsRoot `
        $LegacyStagingPattern "مجلد H21 staging القديم"
}
$DeferredGlobalCleanupWarning = [string]$GlobalCleanupSelection.Warning

$DownloadArtifacts = @()
if (Test-Path -LiteralPath $DownloadsRoot -PathType Container) {
    Assert-NormalDirectory $DownloadsRoot "مجلد Downloads"
    $DownloadArtifacts = @(
        [IO.Directory]::EnumerateFiles($DownloadsRoot) |
            Where-Object {
                [IO.Path]::GetFileName($_) -cmatch $PbrUpdateArtifactPattern
            }
    )
    foreach ($Artifact in $DownloadArtifacts) {
        Assert-NoReparsePointInExistingPath $DownloadsRoot $Artifact
        Assert-PathNotProtected $Artifact $ProtectedRoots `
            "ملف تحديث H21 المحمّل"
        Assert-NormalFile $Artifact "ملف تحديث H21 المحمّل"
    }
}

$H21BackupDirectories = @()
if ($DeepCleanup -and
    (Test-Path -LiteralPath $BackupParent -PathType Container)) {
    $H21BackupDirectories = @(
        [IO.Directory]::EnumerateDirectories($BackupParent) |
            Where-Object {
                [IO.Path]::GetFileName($_) -cmatch $AnyH21BackupPattern
            }
    )
    foreach ($Directory in $H21BackupDirectories) {
        Assert-DirectChildDirectory $Directory $BackupParent `
            $AnyH21BackupPattern "نسخة H21 احتياطية"
        Assert-PathNotProtected $Directory $ProtectedRoots `
            "نسخة H21 احتياطية"
    }
}

$LocalUpdaterArtifacts = [Collections.Generic.List[string]]::new()
if ($DeepCleanup) {
    foreach ($Name in @(
        "HunyuanUpdateCache", "UpdaterLogs", "InstallerCache", "InstallerLogs"
    )) {
        $Candidate = Join-Path $MujassamLocalRoot $Name
        Assert-NoReparsePointInExistingPath $MujassamLocalRoot $Candidate
        if (Test-Path -LiteralPath $Candidate) {
            Assert-DirectChildDirectory $Candidate $MujassamLocalRoot `
                ("^" + [regex]::Escape($Name) + "$") `
                "بقايا updater الخاصة بـMujassamAI"
            Assert-PathNotProtected $Candidate $ProtectedRoots `
                "بقايا updater الخاصة بـMujassamAI"
            $LocalUpdaterArtifacts.Add($Candidate)
        }
    }
}

$SetupArtifactDirectories = @()
if ($DeepCleanup -and
    (Test-Path -LiteralPath $InstallParent -PathType Container)) {
    Assert-NormalDirectory $InstallParent "مجلد التثبيت الأب"
    $SetupArtifactDirectories = @(
        [IO.Directory]::EnumerateDirectories($InstallParent) |
            Where-Object {
                [IO.Path]::GetFileName($_) -cmatch `
                    $SetupArtifactDirectoryPattern
            }
    )
    $SafeSetupArtifactDirectories = [Collections.Generic.List[string]]::new()
    foreach ($Directory in $SetupArtifactDirectories) {
        $DirectoryFull = Get-NormalizedFullPath `
            $Directory "بقايا تثبيت MujassamAI"
        if (Test-PathInsideOrEqual $RootPath $DirectoryFull) {
            Write-Warning (
                "تُرك مجلد setup لأنه يساوي/يحتوي التثبيت الحالي: " +
                $DirectoryFull
            )
            continue
        }
        Assert-DirectChildDirectory $DirectoryFull $InstallParent `
            $SetupArtifactDirectoryPattern "بقايا تثبيت MujassamAI"
        Assert-PathNotProtected $DirectoryFull $ProtectedRoots `
            "بقايا تثبيت MujassamAI"
        if (Test-TreeContainsUserOutput `
            $DirectoryFull "بقايا تثبيت MujassamAI") {
            Write-Warning (
                "تُرك مجلد setup لأنه يحتوي export/output أو .git: " +
                $DirectoryFull
            )
            continue
        }
        $SafeSetupArtifactDirectories.Add($DirectoryFull)
    }
    $SetupArtifactDirectories = @($SafeSetupArtifactDirectories)
}

# REMOVE_HY21_BASELINE_PREFLIGHT: prove that the selected rollback worker is a
# functional Mini worker before the first installed byte is replaced/deleted.
$PreflightMiniExecutable = $MiniExecutable
$PreflightMiniWorker = $MiniWorker
if ($null -ne $Baseline) {
    $PreflightMiniExecutable = Get-ContainedPath `
        $BackupRoot $MiniExecutableRelative
    $PreflightMiniWorker = Get-ContainedPath $BackupRoot $MiniWorkerRelative
}
if (-not (Test-MarkerFreeMiniExecutable $PreflightMiniExecutable)) {
    throw "نسخة MujassamAI.exe المرشحة ليست Mini marker-free موثوقة."
}
Write-Host "تشغيل فحص Mini worker المرشح قبل أي تغيير..." `
    -ForegroundColor Cyan
& $PortablePython -I -X utf8 $PreflightMiniWorker --self-test
if ($LASTEXITCODE -ne 0) {
    throw "فشل فحص Mini worker المرشح؛ لم يتغير أو يُحذف أي ملف."
}
& $PortablePython -I -X utf8 $Hunyuan2Worker --self-test
if ($LASTEXITCODE -ne 0) {
    throw "فشل فحص Hunyuan2 Mini الحالي؛ لم يتغير أو يُحذف أي ملف."
}
Assert-DeepCleanupPhaseReady $RootPath

if (-not $PSCmdlet.ShouldProcess(
    $RootPath,
    $(if ($DeepCleanup) {
        "استرجاع Mini ثم إزالة Hunyuan3D 2.1 Ultimate/PBR وبقاياه بالكامل"
    } else {
        "استرجاع ملفات Mini الأصلية فقط مع إبقاء بقايا H21"
    }))) {
    return
}

$RestoreTransaction = New-QuarantineTransaction `
    $ProtectedRoots "restore" $RootPath
Assert-DeepCleanupPhaseReady $RootPath
try {
    Write-Host "استرجاع ملفات Mini الأصلية والتحقق من بصماتها..." `
        -ForegroundColor Cyan
    foreach ($Entry in $Overwritten) {
        Restore-OriginalFileTransactional `
            $BackupRoot $RootPath $Entry $RestoreTransaction
    }
    foreach ($Entry in $Overwritten) {
        $Destination = Get-ContainedPath $RootPath ([string]$Entry.path)
        if (-not (Test-Path -LiteralPath $Destination -PathType Leaf) -or
            (Get-Sha256 $Destination) -cne [string]$Entry.sha256) {
            throw "فشل تحقق ملف Mini بعد الاسترجاع: $($Entry.path)"
        }
    }

    if ($null -ne $Baseline) {
        if ((Get-Sha256 $MiniExecutable) -cne
                [string]$MiniExecutableOriginal.sha256 -or
            (Get-Sha256 $MiniWorker) -cne
                [string]$MiniWorkerOriginal.sha256) {
            throw "لم تتطابق بصمة Mini worker/exe مع النسخة الأصلية."
        }
    } elseif (-not (Test-MiniCompletionMarker `
        $CompletionMarker $MujassamLocalRoot $RootPath $CurrentMiniHashes)) {
        throw "تغيّرت بصمات Mini عن علامة الاكتمال الموثوقة."
    }
    $ExecutableHeader = [IO.File]::ReadAllBytes($MiniExecutable)
    if ($ExecutableHeader.Length -lt 2 -or
        $ExecutableHeader[0] -ne 0x4d -or $ExecutableHeader[1] -ne 0x5a) {
        throw "ملف MujassamAI.exe الأصلي لا يحتوي ترويسة Windows PE."
    }
    Write-Host "تشغيل فحص واجهة Mini الأصلية..." -ForegroundColor Cyan
    $GuiSelfTestReport = Join-Path $TempRoot (
        "MujassamAI-mini-restore-selftest-" +
        [Guid]::NewGuid().ToString("N") + ".txt")
    $GuiSelfTestProcess = $null
    try {
        # MujassamAI.exe is linked as /target:winexe.  Direct invocation may
        # return control before a GUI-subsystem process exits, so explicitly
        # wait for the process and use its own ExitCode before trusting the
        # report.
        $GuiSelfTestArguments = '--self-test "' + $GuiSelfTestReport + '"'
        $GuiSelfTestProcess = Start-Process `
            -FilePath $MiniExecutable `
            -ArgumentList $GuiSelfTestArguments `
            -Wait -PassThru -ErrorAction Stop
        $GuiSelfTestExitCode = $GuiSelfTestProcess.ExitCode
        if ($GuiSelfTestExitCode -ne 0 -or
            -not (Test-Path -LiteralPath $GuiSelfTestReport -PathType Leaf)) {
            throw (
                "فشل فحص MujassamAI.exe الأصلي؛ exit code: " +
                $GuiSelfTestExitCode
            )
        }
        Assert-NoReparsePointInExistingPath $TempRoot $GuiSelfTestReport
        Assert-NormalFile $GuiSelfTestReport "تقرير فحص واجهة Mini"
        $GuiSelfTestText = [IO.File]::ReadAllText($GuiSelfTestReport)
        foreach ($ExpectedGuiResult in @(
            "PortableLayout=OK", "JobSchema=OK", "Is64BitProcess=True"
        )) {
            if (-not $GuiSelfTestText.Contains($ExpectedGuiResult)) {
                throw "تقرير واجهة Mini يفتقد: $ExpectedGuiResult"
            }
        }
    } finally {
        if ($null -ne $GuiSelfTestProcess) {
            $GuiSelfTestProcess.Dispose()
        }
        Remove-TemporaryFileBestEffort $GuiSelfTestReport
    }
    Write-Host "تشغيل فحص Mini worker ومحرك Hunyuan2 Mini..." `
        -ForegroundColor Cyan
    & $PortablePython -I -X utf8 $MiniWorker --self-test
    if ($LASTEXITCODE -ne 0) {
        throw "فشل الفحص الذاتي للـMini worker بعد الاسترجاع."
    }
    if ((Get-Sha256 $Hunyuan2Worker) -cne $Hunyuan2WorkerSha256 -or
        (Get-Sha256 $Hunyuan2Manifest) -cne $Hunyuan2ManifestSha256) {
        throw "تغيّر Hunyuan2 Mini worker/manifest أثناء إزالة Ultimate."
    }
    & $PortablePython -I -X utf8 $Hunyuan2Worker --self-test
    if ($LASTEXITCODE -ne 0) {
        throw "فشل الفحص الذاتي لمحرك Hunyuan2 Mini بعد الاسترجاع."
    }
    if (-not (Test-MarkerFreeMiniWorker $MiniWorker)) {
        throw "Mini worker ما زال يحتوي marker خاصًا بـUltimate/PBR."
    }
    if (-not (Test-MarkerFreeMiniExecutable $MiniExecutable)) {
        throw "ملف Mini التنفيذي ما زال يحتوي marker خاصًا بـUltimate/PBR."
    }
    Set-QuarantineTransactionCommitted $RestoreTransaction
} catch {
    $RestoreFailure = $_.Exception.Message
    try {
        $RestoreTransaction.State = "active"
        Assert-DeepCleanupPhaseReady $RootPath
        Undo-QuarantineTransaction $RestoreTransaction
    } catch {
        throw (
            "فشلت استعادة Mini وفشل rollback التلقائي. لا تشغّل البرنامج. " +
            "السبب الأصلي: $RestoreFailure. rollback: $($_.Exception.Message)"
        )
    }
    throw "فشلت استعادة Mini وتم rollback تلقائيًا بلا حذف H21: $RestoreFailure"
}

# Commit only after EXE, worker, and Hunyuan2 hashes/self-tests all succeeded.
Assert-DeepCleanupPhaseReady $RootPath
Complete-QuarantineTransaction $RestoreTransaction
$VerifiedMiniHashes = @{
    mujassam_exe_sha256 = (Get-Sha256 $MiniExecutable)
    mini_worker_sha256 = (Get-Sha256 $MiniWorker)
    hunyuan2_worker_sha256 = (Get-Sha256 $Hunyuan2Worker)
    hunyuan2_manifest_sha256 = (Get-Sha256 $Hunyuan2Manifest)
}
Write-MiniCompletionMarker `
    $CompletionMarker $MujassamLocalRoot $RootPath $VerifiedMiniHashes

# REMOVE_HY21_DEEP_CLEANUP_GATE: nothing beyond the shared Mini restore is
# discarded unless the caller explicitly supplies -DeepCleanup.
if (-not $DeepCleanup) {
    Write-Host "تم استرجاع Mini والتحقق منه. لم يُحذف كاش/backup H21." `
        -ForegroundColor Green
    Write-Host "للتنظيف الكامل أعد الأمر نفسه مع -DeepCleanup."
    Write-Host "لم تُمس الصادرات أو Hunyuan2/Mini أو كاشاته أو مجلد المستودع."
    return
}

$DeepCleanupWarnings = [Collections.Generic.List[string]]::new()
if (-not [string]::IsNullOrWhiteSpace($DeferredGlobalCleanupWarning)) {
    $DeepCleanupWarnings.Add($DeferredGlobalCleanupWarning)
    Write-Warning $DeferredGlobalCleanupWarning
}

$CleanupTransaction = New-QuarantineTransaction `
    $ProtectedRoots "deep-cleanup" $RootPath
try {
    # Every phase repeats the process check immediately before its first move.
    # Exact files created by the H21 receipt are moved only when their current
    # hash still matches that receipt.

    Assert-DeepCleanupPhaseReady $RootPath
    foreach ($CreatedEntry in $Created) {
        $Relative = ([string]$CreatedEntry.path).Replace('\', '/')
        if (Test-H21EngineRelativePath $Relative) {
            continue
        }
        $Destination = Get-ContainedPath $RootPath $Relative
        Assert-NoReparsePointInExistingPath $RootPath $Destination
        if (-not (Test-Path -LiteralPath $Destination)) {
            continue
        }
        if ((Get-Sha256 $Destination) -cne
            [string]$CreatedEntry.installed_sha256) {
            throw "تغيّر ملف أضافه H21 قبل cleanup: $Relative"
        }
        Move-ToTransactionQuarantine `
            $CleanupTransaction $Destination $RootPath $RootPath `
            "ملف أضافه تحديث H21" | Out-Null
    }

    Assert-DeepCleanupPhaseReady $RootPath
    if (Test-Path -LiteralPath $InstalledH21Engine) {
        Write-Host "نقل محرك Ultimate/PBR إلى quarantine..." `
            -ForegroundColor Cyan
        # REMOVE_HY21_EXACT_ENGINE_TREE: the root is additive, exact, and is
        # validated again immediately before the atomic same-volume move.
        Assert-NoReparsePointInExistingPath $RootPath $InstalledH21Engine
        Assert-NormalTree $InstalledH21Engine "مجلد Ultimate/PBR المثبت"
        Move-ToTransactionQuarantine `
            $CleanupTransaction $InstalledH21Engine $RootPath $RootPath `
            "مجلد Ultimate/PBR المثبت" | Out-Null
    }

    Assert-DeepCleanupPhaseReady $RootPath
    if (Test-Path -LiteralPath $EngineState) {
        Write-Host (
            "نقل نماذج وكاش Hunyuan3D 2.1 الكبيرة إلى quarantine؛ " +
            "قد يستغرق إفراغ عشرات GB عدة دقائق..."
        ) -ForegroundColor Cyan
        Assert-DirectChildDirectory $EngineState `
            ([IO.Path]::GetDirectoryName($EngineState)) `
            '^Hunyuan3D-2\.1$' "حالة Hunyuan3D 2.1 المحلية"
        Move-ToTransactionQuarantine `
            $CleanupTransaction $EngineState $LocalRoot $LocalRoot `
            "حالة Hunyuan3D 2.1 المحلية" | Out-Null
    }

    Assert-DeepCleanupPhaseReady $RootPath
    if (Test-Path -LiteralPath $AcceptanceFile) {
        Move-ToTransactionQuarantine `
            $CleanupTransaction $AcceptanceFile $LocalRoot $LocalRoot `
            "موافقة Hunyuan3D 2.1" | Out-Null
    }

    Assert-DeepCleanupPhaseReady $RootPath
    foreach ($Artifact in $DownloadArtifacts) {
        if (Test-Path -LiteralPath $Artifact) {
            if ([IO.Path]::GetFileName($Artifact) -cnotmatch `
                $PbrUpdateArtifactPattern) {
                throw "اسم ZIP/SHA غير متوقع قبل cleanup: $Artifact"
            }
            Move-ToTransactionQuarantine `
                $CleanupTransaction $Artifact $DownloadsRoot $DownloadsRoot `
                "ملف تحديث H21 المحمّل" | Out-Null
        }
    }

    Assert-DeepCleanupPhaseReady $RootPath
    foreach ($Directory in @($LocalUpdaterArtifacts)) {
        if (Test-Path -LiteralPath $Directory) {
            $Name = [IO.Path]::GetFileName($Directory)
            Assert-DirectChildDirectory $Directory $MujassamLocalRoot `
                ("^" + [regex]::Escape($Name) + "$") `
                "بقايا updater الخاصة بـMujassamAI"
            Move-ToTransactionQuarantine `
                $CleanupTransaction $Directory $MujassamLocalRoot $LocalRoot `
                "بقايا updater الخاصة بـMujassamAI" | Out-Null
        }
    }

    Assert-DeepCleanupPhaseReady $RootPath
    foreach ($Directory in $SetupArtifactDirectories) {
        if (Test-Path -LiteralPath $Directory) {
            $DirectoryFull = Get-NormalizedFullPath `
                $Directory "بقايا تثبيت MujassamAI"
            if (Test-PathInsideOrEqual $RootPath $DirectoryFull) {
                throw (
                    "تداخل مجلد setup مع التثبيت الحالي قبل cleanup: " +
                    $DirectoryFull
                )
            }
            Assert-DirectChildDirectory $DirectoryFull $InstallParent `
                $SetupArtifactDirectoryPattern "بقايا تثبيت MujassamAI"
            Assert-PathNotProtected $DirectoryFull $ProtectedRoots `
                "بقايا تثبيت MujassamAI"
            if (Test-TreeContainsUserOutput `
                $DirectoryFull "بقايا تثبيت MujassamAI") {
                throw (
                    "ظهر export/output أو .git داخل مجلد setup قبل cleanup: " +
                    $DirectoryFull
                )
            }
            Move-ToTransactionQuarantine `
                $CleanupTransaction $DirectoryFull $InstallParent `
                $InstallParent `
                "بقايا تثبيت MujassamAI" | Out-Null
        }
    }

    Assert-DeepCleanupPhaseReady $RootPath
    $FinalGlobalCleanupSelection = Select-InactiveH21CleanupDirectories `
        (@($TempDirectories) + @($LegacyStagingDirectories))
    if (-not [string]::IsNullOrWhiteSpace(
        [string]$FinalGlobalCleanupSelection.Warning)) {
        $FinalCleanupWarning = [string]$FinalGlobalCleanupSelection.Warning
        if (-not $DeepCleanupWarnings.Contains($FinalCleanupWarning)) {
            $DeepCleanupWarnings.Add($FinalCleanupWarning)
        }
        Write-Warning $FinalCleanupWarning
    }
    $FinalCleanupPaths = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    foreach ($SelectedPath in @($FinalGlobalCleanupSelection.Paths)) {
        $FinalCleanupPaths.Add(
            (Get-NormalizedFullPath $SelectedPath "هدف cleanup النهائي")) |
            Out-Null
    }
    foreach ($Directory in $TempDirectories) {
        $DirectoryFull = Get-NormalizedFullPath $Directory "مجلد H21 المؤقت"
        if ($FinalCleanupPaths.Contains($DirectoryFull) -and
            (Test-Path -LiteralPath $DirectoryFull)) {
            Assert-DirectChildDirectory $DirectoryFull $TempRoot `
                $TemporaryDirectoryPattern "مجلد H21 المؤقت"
            Move-ToTransactionQuarantine `
                $CleanupTransaction $DirectoryFull $TempRoot $TempRoot `
                "مجلد H21 المؤقت" | Out-Null
        }
    }
    foreach ($Directory in $LegacyStagingDirectories) {
        $DirectoryFull = Get-NormalizedFullPath $Directory `
            "مجلد H21 staging القديم"
        if ($FinalCleanupPaths.Contains($DirectoryFull) -and
            (Test-Path -LiteralPath $DirectoryFull)) {
            Assert-DirectChildDirectory $DirectoryFull $DocumentsRoot `
                $LegacyStagingPattern "مجلد H21 staging القديم"
            Move-ToTransactionQuarantine `
                $CleanupTransaction $DirectoryFull $DocumentsRoot `
                $DocumentsRoot "مجلد H21 staging القديم" | Out-Null
        }
    }

    # Backups are last: by this point the completion marker exists and every
    # Mini component has passed both hash and executable self-tests.
    Assert-DeepCleanupPhaseReady $RootPath
    foreach ($Directory in $H21BackupDirectories) {
        if (Test-Path -LiteralPath $Directory) {
            Assert-DirectChildDirectory $Directory $BackupParent `
                $AnyH21BackupPattern "نسخة H21 احتياطية"
            Move-ToTransactionQuarantine `
                $CleanupTransaction $Directory $BackupParent $LocalRoot `
                "نسخة H21 احتياطية" | Out-Null
        }
    }
    Set-QuarantineTransactionCommitted $CleanupTransaction
} catch {
    $CleanupFailure = $_.Exception.Message
    try {
        $CleanupTransaction.State = "active"
        Assert-DeepCleanupPhaseReady $RootPath
        Undo-QuarantineTransaction $CleanupTransaction
    } catch {
        throw (
            "فشل DeepCleanup وفشل rollback لعناصر cleanup. " +
            "Mini نفسه سليم. السبب: $CleanupFailure. rollback: " +
            $($_.Exception.Message)
        )
    }
    throw "فشل DeepCleanup وتم rollback لكل عناصر cleanup: $CleanupFailure"
}

# All targets have moved successfully; purge only the controlled per-volume
# quarantine roots.  No repository clone, export, or generic HF/pip cache is
# ever enumerated by this script.
try {
    Assert-DeepCleanupPhaseReady $RootPath
    Complete-QuarantineTransaction $CleanupTransaction
} catch {
    $CommittedRoots = @($CleanupTransaction.Roots.Values | ForEach-Object {
        [string]$_.Path
    }) -join " | "
    throw (
        "اكتمل النقل الآمن لكن Windows/الحماية منع إفراغ quarantine. " +
        "أعد نفس أمر -DeepCleanup؛ لن يحذف إلا transaction committed الموثق. " +
        "المسارات: $CommittedRoots. السبب: $($_.Exception.Message)"
    )
}

Write-Host ""
if ($DeepCleanupWarnings.Count -eq 0) {
    Write-Host "تم حذف Hunyuan3D 2.1 Ultimate/PBR وبقاياه وإرجاع Mini بنجاح." `
        -ForegroundColor Green
} else {
    Write-Host "تم إرجاع Mini وحذف H21، لكن تُركت مجلدات build نشطة." `
        -ForegroundColor Yellow
    Write-Warning (
        "أعد نفس أمر -DeepCleanup بعد توقف build/install: " +
        ($DeepCleanupWarnings -join " | ")
    )
}
Write-Host "لم تُمس الصادرات أو Hunyuan2/Mini أو كاشاته أو مجلد المستودع."
Write-Host "لم تُمس كاشات Hugging Face أو pip العامة."
