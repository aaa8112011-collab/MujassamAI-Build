[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$FailedBuildRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if ($PSVersionTable.PSVersion -lt [version]"7.4") {
    throw "يلزم PowerShell 7.4 أو أحدث (pwsh.exe)."
}
if (-not $IsWindows -or
    -not [Environment]::Is64BitOperatingSystem -or
    -not [Environment]::Is64BitProcess) {
    throw "استعادة البناء تتطلب Windows x64 وPowerShell 64-bit."
}
$PSNativeCommandUseErrorActionPreference = $true

# This recovery path is intentionally narrow.  It may only finalize the retained
# stage produced from the PR #6 merge whose expensive build/tests already ran.
$FailedRepositoryCommit = "c8c99ed6683d31600edeeb47f883986e77797120"
$ExpectedUpstreamCommit = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
$ExpectedProduct = "Mujassam AI Hunyuan3D-2.1 Shape + PBR engine"
$ArchiveName = "MujassamAI-Hunyuan21-PBR-Update-v1.zip"
$RealEsrganSha256 = "4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1"
$RealEsrganBytes = [Int64]67040989
$MaximumFiles = 150000

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-DownloadsDirectory {
    $Key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
    $ValueName = "{374DE290-123F-4565-9164-39C4925E467B}"
    try {
        $Configured = (Get-ItemProperty -LiteralPath $Key -Name $ValueName).$ValueName
        $Expanded = [Environment]::ExpandEnvironmentVariables([string]$Configured)
        if (-not [string]::IsNullOrWhiteSpace($Expanded)) {
            return [IO.Path]::GetFullPath($Expanded)
        }
    } catch {
        # Fall through to the standard per-user location.
    }
    return [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE "Downloads"))
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
        if ($Part -in @('.', '..') -or $Part.EndsWith(' ') -or
            $Part.EndsWith('.') -or
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
    $RootWithSeparator = [IO.Path]::GetFullPath($Root).TrimEnd('\') + '\'
    $NativeRelative = $Relative.Replace('/', '\')
    $Candidate = [IO.Path]::GetFullPath((Join-Path $RootWithSeparator $NativeRelative))
    if (-not $Candidate.StartsWith(
        $RootWithSeparator, [StringComparison]::OrdinalIgnoreCase)) {
        throw "المسار يخرج من الجذر الموثوق: $Relative"
    }
    return $Candidate
}

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$InstallerScript = Join-Path $RepositoryRoot "installer\install-hunyuan21-local.ps1"
$GitCommand = Get-Command git.exe -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -eq $GitCommand) {
    throw "Git for Windows غير موجود."
}
$Git = $GitCommand.Source

$WorkingTreeChanges = @(& $Git -C $RepositoryRoot status --porcelain --untracked-files=all)
if ($WorkingTreeChanges.Count -ne 0) {
    throw "يوجد تعديل محلي غير محفوظ في المستودع؛ رُفضت الاستعادة."
}
$RepositoryCommit = (& $Git -C $RepositoryRoot rev-parse HEAD).Trim()
$MergeBase = (& $Git -C $RepositoryRoot merge-base `
    $FailedRepositoryCommit $RepositoryCommit).Trim()
if ($MergeBase -cne $FailedRepositoryCommit) {
    throw "نسخة المستودع الحالية ليست امتدادًا للبناء الفاشل الموثق."
}
$ChangedPayloadInputs = @(& $Git -C $RepositoryRoot diff --name-only `
    $FailedRepositoryCommit $RepositoryCommit -- `
    app NOTICE_THIRD_PARTY.md licenses `
    build/patch_hunyuan21_windows.py `
    build/hunyuan21.requirements.lock.txt `
    build/hunyuan21.build.requirements.lock.txt)
if ($ChangedPayloadInputs.Count -ne 0) {
    throw (
        "تغيّرت مدخلات الحزمة بعد البناء الفاشل؛ يلزم بناء كامل. الملفات: " +
        ($ChangedPayloadInputs -join ', ')
    )
}
if (-not (Test-Path -LiteralPath $InstallerScript -PathType Leaf)) {
    throw "المثبّت المحلي غير موجود: $InstallerScript"
}

$TemporaryRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd('\')
$BuildTemporaryRoot = [IO.Path]::GetFullPath($FailedBuildRoot).TrimEnd('\')
if (-not [string]::Equals(
    [IO.Path]::GetDirectoryName($BuildTemporaryRoot), $TemporaryRoot,
    [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetFileName($BuildTemporaryRoot) -notmatch
        '^MujassamAI-hy21-[0-9a-f]{32}$') {
    throw "المسار ليس مجلد بناء MujassamAI مباشرًا وآمنًا داخل TEMP: $BuildTemporaryRoot"
}
Assert-NormalDirectory $BuildTemporaryRoot "مجلد البناء الفاشل"

$BuildRoot = Join-Path $BuildTemporaryRoot "hy3d21-update"
$Stage = Join-Path $BuildRoot "stage"
$GuiTestRoot = Join-Path $BuildRoot "gui-test"
$SourceRoot = Join-Path $BuildTemporaryRoot "hy3d21-src"
foreach ($Directory in @($BuildRoot, $Stage, $GuiTestRoot, $SourceRoot)) {
    Assert-NormalDirectory $Directory "مجلد استعادة مطلوب"
}
$UnsafeStageItems = @(Get-ChildItem -LiteralPath $Stage -Recurse -Force |
    Where-Object {
        $_.Attributes -band [IO.FileAttributes]::ReparsePoint
    })
if ($UnsafeStageItems.Count -ne 0) {
    throw "تحتوي الحزمة المرحلية reparse point غير مسموح: $($UnsafeStageItems[0].FullName)"
}

$UpstreamHead = (& $Git -C $SourceRoot rev-parse HEAD).Trim()
if ($UpstreamHead -cne $ExpectedUpstreamCommit) {
    throw "مصدر Hunyuan3D المرحلي لا يطابق commit المثبت."
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
    $RequiredPath = Get-ContainedPath $Stage $Relative
    if (-not (Test-Path -LiteralPath $RequiredPath -PathType Leaf)) {
        throw "الحزمة المرحلية تفتقد الملف المطلوب: $Relative"
    }
}

# Prove that all directly copied, tracked inputs still match the failed commit.
$TrackedStageInputs = @(
    "NOTICE_THIRD_PARTY.md",
    "app/worker.py",
    "app/engines/hunyuan21/ENGINE-MANIFEST.json",
    "app/engines/hunyuan21/LICENSE-HUNYUAN3D-2.1.txt",
    "app/engines/hunyuan21/MODIFICATIONS.txt",
    "app/engines/hunyuan21/NOTICE-HUNYUAN3D-2.1.txt",
    "app/engines/hunyuan21/hunyuan21_worker.py"
)
$TrackedStageInputs += @(& $Git -C $RepositoryRoot ls-tree -r --name-only `
    $FailedRepositoryCommit -- app/quality licenses)
foreach ($Relative in $TrackedStageInputs) {
    $StagePath = Get-ContainedPath $Stage $Relative
    if (-not (Test-Path -LiteralPath $StagePath -PathType Leaf)) {
        throw "ملف مصدر مرحلي مفقود: $Relative"
    }
    $ExpectedBlob = (& $Git -C $RepositoryRoot rev-parse `
        "$FailedRepositoryCommit`:$Relative").Trim()
    $ActualBlob = (& $Git -C $RepositoryRoot hash-object `
        "--path=$Relative" -- $StagePath).Trim()
    if ($ActualBlob -cne $ExpectedBlob) {
        throw "ملف مرحلي لا يطابق البناء الفاشل الموثق: $Relative"
    }
}

$EngineManifestPath = Get-ContainedPath $Stage `
    "app/engines/hunyuan21/ENGINE-MANIFEST.json"
$EngineManifest = Get-Content -LiteralPath $EngineManifestPath -Raw | ConvertFrom-Json
if ([string]$EngineManifest.source.commit -cne $ExpectedUpstreamCommit -or
    [string]$EngineManifest.runtime_abi.python -cne "3.11.9" -or
    [string]$EngineManifest.runtime_abi.pytorch -cne "2.5.1+cu124" -or
    [string]$EngineManifest.runtime_abi.torchvision -cne "0.20.1+cu124" -or
    [string]$EngineManifest.runtime_abi.cuda_runtime -cne "12.4" -or
    [string]$EngineManifest.runtime_abi.platform -cne "win_amd64") {
    throw "ENGINE-MANIFEST المرحلي لا يطابق المصدر أو ABI المثبت."
}
$DistributionNotice = Get-Content -LiteralPath (Get-ContainedPath $Stage `
    "app/engines/hunyuan21/NOTICE.txt") -Raw
if (-not $DistributionNotice.Contains("Configured usage scope: personal_local_only") -or
    -not $DistributionNotice.Contains(
        "Actual provider of this integration: not applicable (personal local use only)")) {
    throw "NOTICE المرحلي لا يثبت نطاق personal_local_only المتوقع."
}
$RealEsrganPath = Get-ContainedPath $Stage `
    "app/engines/hunyuan21/models/RealESRGAN_x4plus.pth"
$RealEsrgan = Get-Item -LiteralPath $RealEsrganPath
if ($RealEsrgan.Length -ne $RealEsrganBytes -or
    (Get-Sha256 $RealEsrganPath) -cne $RealEsrganSha256) {
    throw "فشل التحقق من نموذج RealESRGAN المرحلي."
}

$GuiReport = Join-Path $GuiTestRoot "self-test.txt"
if (-not (Test-Path -LiteralPath $GuiReport -PathType Leaf)) {
    throw "تقرير اختبار MujassamAI.exe السابق غير موجود."
}
$GuiText = Get-Content -LiteralPath $GuiReport -Raw
if (-not $GuiText.Contains("PortableLayout=OK") -or
    -not $GuiText.Contains("JobSchema=OK") -or
    -not $GuiText.Contains("Is64BitProcess=True")) {
    throw "تقرير الاختبار السابق لا يثبت نجاح فحص الواجهة المحمولة."
}
$GuiExecutable = Join-Path $GuiTestRoot "MujassamAI.exe"
if (-not (Test-Path -LiteralPath $GuiExecutable -PathType Leaf) -or
    (Get-Sha256 $GuiExecutable) -cne (Get-Sha256 (Join-Path $Stage "MujassamAI.exe"))) {
    throw "ملف MujassamAI.exe المرحلي لا يطابق النسخة التي اجتازت الاختبار."
}

$TextExtensions = @(".py", ".json", ".txt", ".md", ".yaml", ".yml", ".manifest")
$MujassamMarkerPattern = '@@MUJASSAM_[A-Z][A-Z0-9_]*@@'
$UnresolvedMarkerDetails = @(Get-ChildItem -LiteralPath $Stage -Recurse -File |
    Where-Object { $_.Extension.ToLowerInvariant() -in $TextExtensions } |
    ForEach-Object {
        $TextFile = $_
        $Text = [IO.File]::ReadAllText($TextFile.FullName)
        $MarkerMatches = @([regex]::Matches(
            $Text, $MujassamMarkerPattern) |
            ForEach-Object { $_.Value } |
            Sort-Object -Unique)
        if ($MarkerMatches.Count -ne 0) {
            "$($TextFile.FullName): $($MarkerMatches -join ', ')"
        }
    })
if ($UnresolvedMarkerDetails.Count -ne 0) {
    throw "توجد علامة بناء غير محلولة: $($UnresolvedMarkerDetails -join '; ')"
}

$ExistingManifestPath = Join-Path $Stage "update-manifest.json"
if (Test-Path -LiteralPath $ExistingManifestPath) {
    $ExistingManifest = Get-Content -LiteralPath $ExistingManifestPath -Raw |
        ConvertFrom-Json
    if ([int]$ExistingManifest.schema_version -ne 1 -or
        [string]$ExistingManifest.product -cne $ExpectedProduct -or
        [string]$ExistingManifest.source_commit -cne $FailedRepositoryCommit -or
        [string]$ExistingManifest.upstream_commit -cne $ExpectedUpstreamCommit) {
        throw "يوجد update-manifest.json غير تابع لهذه الاستعادة."
    }
}

$StageFiles = @(Get-ChildItem -LiteralPath $Stage -Recurse -File |
    Where-Object { $_.FullName -cne $ExistingManifestPath } |
    Sort-Object FullName)
if ($StageFiles.Count -eq 0 -or $StageFiles.Count -gt $MaximumFiles) {
    throw "عدد الملفات المرحلية غير منطقي."
}
$SeenPaths = [Collections.Generic.HashSet[string]]::new(
    [StringComparer]::OrdinalIgnoreCase)
$Entries = @($StageFiles | ForEach-Object {
    $Relative = $_.FullName.Substring($Stage.Length).TrimStart('\').Replace('\', '/')
    if (-not (Test-SafeRelativePath $Relative) -or -not $SeenPaths.Add($Relative)) {
        throw "مسار مرحلي غير آمن أو مكرر: $Relative"
    }
    [ordered]@{
        path = $Relative
        bytes = [Int64]$_.Length
        sha256 = Get-Sha256 $_.FullName
    }
})
$UpdateManifest = [ordered]@{
    schema_version = 1
    product = $ExpectedProduct
    source_commit = $FailedRepositoryCommit
    upstream_commit = $ExpectedUpstreamCommit
    usage_scope = "personal_local_only"
    distribution_authorized = $false
    provider_legal_name = $null
    archive = $ArchiveName
    files = $Entries
}
[IO.File]::WriteAllText(
    $ExistingManifestPath, ($UpdateManifest | ConvertTo-Json -Depth 6),
    [Text.UTF8Encoding]::new($false))

$ReleaseRoot = Join-Path $RepositoryRoot "release"
if (Test-Path -LiteralPath $ReleaseRoot) {
    Assert-NormalDirectory $ReleaseRoot "مجلد release"
} else {
    New-Item -ItemType Directory -Path $ReleaseRoot | Out-Null
}
$Archive = Join-Path $ReleaseRoot $ArchiveName
if (Test-Path -LiteralPath $Archive) {
    throw "ملف ZIP النهائي موجود مسبقًا؛ لن تتم الكتابة فوقه: $Archive"
}
$RecoveryId = [Guid]::NewGuid().ToString("N")
$PartialArchive = Join-Path $ReleaseRoot (
    [IO.Path]::GetFileNameWithoutExtension($ArchiveName) +
    ".resume-$RecoveryId.zip")
$VerifyRoot = Join-Path $BuildRoot "resume-verify-$RecoveryId"
$RecoverySucceeded = $false
try {
    Write-Host "جاري إنشاء ZIP من البناء المكتمل (لن يُعاد التحميل أو التجميع)..."
    Compress-Archive -Path (Join-Path $Stage "*") `
        -DestinationPath $PartialArchive -CompressionLevel Optimal
    Write-Host "جاري التحقق من كل ملف داخل ZIP..."
    Expand-Archive -LiteralPath $PartialArchive -DestinationPath $VerifyRoot
    Assert-NormalDirectory $VerifyRoot "مجلد تحقق ZIP"
    $UnsafeVerifyItems = @(Get-ChildItem -LiteralPath $VerifyRoot -Recurse -Force |
        Where-Object {
            $_.Attributes -band [IO.FileAttributes]::ReparsePoint
        })
    if ($UnsafeVerifyItems.Count -ne 0) {
        throw "ZIP المستخرج يحتوي reparse point غير مسموح."
    }
    $ExtractedManifestPath = Join-Path $VerifyRoot "update-manifest.json"
    $ExtractedManifest = Get-Content -LiteralPath $ExtractedManifestPath -Raw |
        ConvertFrom-Json
    if ([int]$ExtractedManifest.schema_version -ne 1 -or
        [string]$ExtractedManifest.product -cne $ExpectedProduct -or
        [string]$ExtractedManifest.source_commit -cne $FailedRepositoryCommit -or
        [string]$ExtractedManifest.upstream_commit -cne $ExpectedUpstreamCommit -or
        [string]$ExtractedManifest.usage_scope -cne "personal_local_only" -or
        $ExtractedManifest.distribution_authorized -isnot [bool] -or
        [bool]$ExtractedManifest.distribution_authorized -ne $false -or
        $null -ne $ExtractedManifest.provider_legal_name -or
        [string]$ExtractedManifest.archive -cne $ArchiveName) {
        throw "بيانات ZIP المستخرج لا تطابق الاستعادة الشخصية المحلية الموثقة."
    }
    $ManifestFiles = @($ExtractedManifest.files)
    if ($ManifestFiles.Count -ne $Entries.Count) {
        throw "عدد الملفات في manifest لا يطابق الحزمة."
    }
    $ManifestPaths = [Collections.Generic.HashSet[string]]::new(
        [StringComparer]::OrdinalIgnoreCase)
    foreach ($Entry in $ManifestFiles) {
        $Relative = [string]$Entry.path
        if (-not (Test-SafeRelativePath $Relative) -or
            -not $ManifestPaths.Add($Relative)) {
            throw "مسار manifest غير آمن أو مكرر: $Relative"
        }
        $ExpectedHash = [string]$Entry.sha256
        if ($ExpectedHash -notmatch '^[0-9a-f]{64}$' -or
            [Int64]$Entry.bytes -lt 0) {
            throw "حجم أو بصمة غير صالحة في manifest: $Relative"
        }
        $ExtractedFile = Get-ContainedPath $VerifyRoot $Relative
        if (-not (Test-Path -LiteralPath $ExtractedFile -PathType Leaf)) {
            throw "ملف manifest مفقود من ZIP: $Relative"
        }
        $File = Get-Item -LiteralPath $ExtractedFile
        if ($File.Length -ne [Int64]$Entry.bytes -or
            (Get-Sha256 $ExtractedFile) -cne $ExpectedHash) {
            throw "فشل تحقق SHA-256 أو الحجم: $Relative"
        }
    }
    $ActualPayloadPaths = @(Get-ChildItem -LiteralPath $VerifyRoot -Recurse -File |
        ForEach-Object {
            $_.FullName.Substring($VerifyRoot.Length).TrimStart('\').Replace('\', '/')
        } | Where-Object { $_ -cne "update-manifest.json" })
    if ($ActualPayloadPaths.Count -ne $ManifestPaths.Count) {
        throw "ZIP يحتوي ملفًا زائدًا أو لا يسرد كل الملفات."
    }
    foreach ($Relative in $ActualPayloadPaths) {
        if (-not $ManifestPaths.Contains($Relative)) {
            throw "ملف غير مصرح به داخل ZIP: $Relative"
        }
    }
    $ArchiveSha256 = Get-Sha256 $PartialArchive

    $Downloads = Get-DownloadsDirectory
    New-Item -ItemType Directory -Path $Downloads -Force | Out-Null
    $DownloadedArchive = Join-Path $Downloads $ArchiveName
    if (Test-Path -LiteralPath $DownloadedArchive) {
        if ((Get-Sha256 $DownloadedArchive) -cne $ArchiveSha256) {
            $DownloadedArchive = Join-Path $Downloads (
                [IO.Path]::GetFileNameWithoutExtension($ArchiveName) + "-" +
                (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss") + "-" +
                [Guid]::NewGuid().ToString("N").Substring(0, 8) + ".zip")
        }
    }
    if (-not (Test-Path -LiteralPath $DownloadedArchive)) {
        Copy-Item -LiteralPath $PartialArchive -Destination $DownloadedArchive
    }
    if ((Get-Sha256 $DownloadedArchive) -cne $ArchiveSha256) {
        throw "فشل التحقق من نسخة ZIP داخل Downloads."
    }
    [IO.File]::WriteAllText(
        $DownloadedArchive + ".sha256",
        "$ArchiveSha256  $([IO.Path]::GetFileName($DownloadedArchive))`r`n",
        [Text.UTF8Encoding]::new($false))
    [IO.File]::Move($PartialArchive, $Archive)
    if ((Get-Sha256 $Archive) -cne $ArchiveSha256) {
        throw "فشل التحقق النهائي من ZIP داخل release."
    }
    $RecoverySucceeded = $true

    Write-Host ""
    Write-Host "اكتملت استعادة البناء وإنشاء ZIP الموثق:" -ForegroundColor Green
    Write-Host $DownloadedArchive
    Write-Host "SHA-256: $ArchiveSha256"
    Write-Host "لم يُحذف مجلد البناء الفاشل: $BuildTemporaryRoot" -ForegroundColor Yellow

    $InstallAnswer = (Read-Host `
        "للتثبيت الآن فوق النسخة المحمولة اكتب INSTALL، أو اضغط Enter للتخطي").Trim()
    if ($InstallAnswer -ceq "INSTALL") {
        $DefaultInstallRoot = Join-Path `
            ([Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)) `
            "MujassamAI-Portable"
        $ChosenRoot = (Read-Host `
            "مجلد النسخة المحمولة (Enter للمسار الافتراضي: $DefaultInstallRoot)").Trim()
        if ([string]::IsNullOrWhiteSpace($ChosenRoot)) {
            $ChosenRoot = $DefaultInstallRoot
        }
        & $InstallerScript -UpdateZip $DownloadedArchive `
            -ExpectedSha256 $ArchiveSha256 -InstallRoot $ChosenRoot
        if ($LASTEXITCODE -ne 0) {
            throw "فشل المثبّت المحلي برمز $LASTEXITCODE"
        }
    }
} finally {
    if (Test-Path -LiteralPath $PartialArchive) {
        Remove-Item -LiteralPath $PartialArchive -Force
    }
    # Only the verification directory created by this invocation is removed.
    # The retained failed-build root and its staged payload are never deleted.
    if (Test-Path -LiteralPath $VerifyRoot) {
        Remove-Item -LiteralPath $VerifyRoot -Recurse -Force
    }
    if (-not $RecoverySucceeded) {
        Write-Warning "لم تكتمل الاستعادة؛ بقي مجلد البناء الفاشل دون حذف: $BuildTemporaryRoot"
    }
}
