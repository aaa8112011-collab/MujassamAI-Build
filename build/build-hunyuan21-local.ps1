[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$PinnedSourceCommit = "82920d643c0dc2f7bfd7255f45f62d386edfe60c"
$ExpectedLicenseSha256 = "20b7e73b7996a815226ae4c08d18a7891c417749f2de687d1db90b4e36b78789"
$ArchiveName = "MujassamAI-Hunyuan21-PBR-Update-v1.zip"

function Test-IsAdministrator {
    $Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $Principal = [Security.Principal.WindowsPrincipal]::new($Identity)
    return $Principal.IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
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

function Assert-NativeCommand([string]$Name) {
    $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $Command) {
        throw "الأداة المطلوبة غير موجودة: $Name"
    }
    return $Command.Source
}

function Assert-SafeOwnedTemporaryDirectory([string]$Path) {
    $FullPath = [IO.Path]::GetFullPath($Path)
    $TemporaryRoot = [IO.Path]::GetFullPath($env:TEMP).TrimEnd(
        [IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $FullPath.StartsWith(
        $TemporaryRoot, [StringComparison]::OrdinalIgnoreCase) -or
        -not [IO.Path]::GetFileName($FullPath).StartsWith(
            "MujassamAI-hy21-", [StringComparison]::Ordinal)) {
        throw "رفض تنظيف مسار مؤقت غير مملوك لهذا البناء: $FullPath"
    }
}

if ($PSVersionTable.PSVersion -lt [version]"7.4") {
    throw "يلزم PowerShell 7.4 أو أحدث (pwsh.exe)، وليس Windows PowerShell 5.1."
}
if (-not $IsWindows -or
    -not [Environment]::Is64BitOperatingSystem -or
    -not [Environment]::Is64BitProcess) {
    throw "هذا البناء يتطلب Windows x64 وعملية PowerShell 64-bit."
}

if (-not (Test-IsAdministrator)) {
    Write-Host "سيظهر طلب صلاحية Administrator لأن بناء CUDA يحتاجها." -ForegroundColor Yellow
    $PowerShell = (Get-Command pwsh.exe -CommandType Application).Source
    $QuotedScript = '"' + $PSCommandPath.Replace('"', '""') + '"'
    try {
        $Elevated = Start-Process -FilePath $PowerShell -Verb RunAs -Wait -PassThru `
            -ArgumentList @(
                "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", $QuotedScript)
    } catch {
        throw "أُلغي طلب Administrator؛ لم يبدأ البناء."
    }
    exit $Elevated.ExitCode
}
if (-not (Test-IsAdministrator)) {
    throw "تعذر التحقق من صلاحية Administrator."
}

$RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BuildScript = Join-Path $PSScriptRoot "build-hunyuan21-update.ps1"
$InstallerScript = Join-Path $RepositoryRoot "installer\install-hunyuan21-local.ps1"
$LicensePath = Join-Path $RepositoryRoot `
    "app\engines\hunyuan21\LICENSE-HUNYUAN3D-2.1.txt"
$EngineManifestPath = Join-Path $RepositoryRoot `
    "app\engines\hunyuan21\ENGINE-MANIFEST.json"
$Validator = Join-Path $PSScriptRoot "validate_hunyuan21_adapter.py"

foreach ($RequiredFile in @(
    $BuildScript, $InstallerScript, $LicensePath, $EngineManifestPath, $Validator,
    (Join-Path $PSScriptRoot "hunyuan21.requirements.lock.txt"),
    (Join-Path $PSScriptRoot "hunyuan21.build.requirements.lock.txt")
)) {
    if (-not (Test-Path -LiteralPath $RequiredFile -PathType Leaf)) {
        throw "ملف بناء مطلوب غير موجود: $RequiredFile"
    }
}

$Python = Assert-NativeCommand "python.exe"
$Git = Assert-NativeCommand "git.exe"
$PythonProbeText = & $Python -I -X utf8 -c `
    'import json,platform,sys; print(json.dumps({"version": platform.python_version(), "bits": 64 if sys.maxsize > 2**32 else 32, "exe": sys.executable}))'
if ($LASTEXITCODE -ne 0) {
    throw "تعذر تشغيل Python المطلوب."
}
$PythonProbe = $PythonProbeText | ConvertFrom-Json
if ([string]$PythonProbe.version -cne "3.11.9" -or [int]$PythonProbe.bits -ne 64) {
    throw (
        "يلزم Python 3.11.9 x64 بالضبط. الموجود: " +
        "$($PythonProbe.version) ($($PythonProbe.bits)-bit) في $($PythonProbe.exe)"
    )
}
Write-Host "Python: $($PythonProbe.version) x64" -ForegroundColor Green

$GitVersion = (& $Git --version).Trim()
if ($LASTEXITCODE -ne 0 -or $GitVersion -notmatch '^git version ') {
    throw "تعذر تشغيل Git for Windows."
}
Write-Host $GitVersion -ForegroundColor Green

$GitTop = (& $Git -C $RepositoryRoot rev-parse --show-toplevel).Trim()
$GitTopMatchesRepository = [string]::Equals(
    [IO.Path]::GetFullPath($GitTop).TrimEnd('\'),
    $RepositoryRoot.TrimEnd('\'),
    [StringComparison]::OrdinalIgnoreCase)
if ($LASTEXITCODE -ne 0 -or -not $GitTopMatchesRepository) {
    throw "شغّل السكربت من Git clone نظيف للمستودع، وليس من ZIP مفكوك."
}
$WorkingTreeChanges = @(& $Git -C $RepositoryRoot status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0 -or $WorkingTreeChanges.Count -ne 0) {
    throw "يوجد تعديل محلي غير محفوظ في المستودع. احفظه أو اعكسه قبل البناء الموثق."
}
$RepositoryCommit = (& $Git -C $RepositoryRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $RepositoryCommit -notmatch '^[0-9a-f]{40}$') {
    throw "تعذر تحديد commit المستودع الحالي."
}
Write-Host "MujassamAI commit: $RepositoryCommit" -ForegroundColor Green

$ProgramFilesX86 = [Environment]::GetFolderPath(
    [Environment+SpecialFolder]::ProgramFilesX86)
$VsWhere = Join-Path $ProgramFilesX86 `
    "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $VsWhere -PathType Leaf)) {
    throw "Visual Studio Installer/vswhere غير موجود. ثبّت VS 2022 Build Tools."
}
$Msvc142Component = "Microsoft.VisualStudio.Component.VC.14.29.16.11.x86.x64"
$VsInstall = (& $VsWhere -latest -products * `
    -requires $Msvc142Component `
    -property installationPath | Select-Object -First 1)
if ([string]::IsNullOrWhiteSpace($VsInstall)) {
    throw "مكوّن MSVC v142/14.29 x64 في Visual Studio غير موجود."
}
$Msvc142 = @(Get-ChildItem -LiteralPath (Join-Path $VsInstall "VC\Tools\MSVC") `
    -Directory -Filter "14.29.*" -ErrorAction SilentlyContinue | Where-Object {
        Test-Path -LiteralPath (Join-Path $_.FullName "bin\Hostx64\x64\cl.exe") `
            -PathType Leaf
    })
if ($Msvc142.Count -eq 0) {
    throw "ثبّت MSVC v142/14.29 x64 من Visual Studio Installer."
}
$DotNet48 = Join-Path $ProgramFilesX86 `
    "Reference Assemblies\Microsoft\Framework\.NETFramework\v4.8"
if (-not (Test-Path -LiteralPath (Join-Path $DotNet48 "System.dll") -PathType Leaf) -or
    -not (Test-Path -LiteralPath `
        (Join-Path $VsInstall "MSBuild\Current\Bin\Roslyn\csc.exe") -PathType Leaf)) {
    throw "يلزم .NET Framework 4.8 Targeting Pack مع VS 2022 Build Tools."
}
Write-Host "VS Build Tools + MSVC v142 + .NET 4.8: OK" -ForegroundColor Green

& $Python -I -X utf8 $Validator
if ($LASTEXITCODE -ne 0) {
    throw "فشل فحص Adapter أو قوائم الاعتماديات المقفلة."
}

$EngineManifest = Get-Content -LiteralPath $EngineManifestPath -Raw | ConvertFrom-Json
if ([string]$EngineManifest.source.commit -cne $PinnedSourceCommit -or
    [string]$EngineManifest.source.license_file_sha256 -cne $ExpectedLicenseSha256) {
    throw "ENGINE-MANIFEST لا يطابق مصدر/ترخيص Hunyuan3D 2.1 المثبت."
}
$ActualLicenseSha256 = (Get-FileHash -LiteralPath $LicensePath `
    -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ActualLicenseSha256 -cne $ExpectedLicenseSha256) {
    throw "بصمة ملف ترخيص Hunyuan3D 2.1 غير صحيحة."
}

Write-Host ""
Write-Host "موافقة مطلوبة قبل أي تنزيل" -ForegroundColor Cyan
Write-Host "سيُفتح نص الترخيص المثبت، ويشمل Acceptable Use Policy في Exhibit A."
Write-Host "SHA-256: $ActualLicenseSha256"
Start-Process -FilePath "notepad.exe" -ArgumentList `
    ('"{0}"' -f $LicensePath) -Wait | Out-Null

$LicenseAnswer = (Read-Host `
    "بعد قراءة الترخيص وAcceptable Use Policy بالكامل، اكتب I ACCEPT للموافقة").Trim()
if ($LicenseAnswer -cne "I ACCEPT") {
    throw "لم تُسجّل موافقة صريحة؛ لم يبدأ أي تنزيل."
}
$TerritoryAnswer = (Read-Host `
    "أكد أن هذا الكمبيوتر والاستخدام والمخرجات داخل الإمارات؛ اكتب UNITED ARAB EMIRATES").Trim()
if ($TerritoryAnswer -cne "UNITED ARAB EMIRATES") {
    throw "لم يتم تأكيد الإمارات/Territory؛ لم يبدأ أي تنزيل."
}
$PersonalAnswer = (Read-Host `
    "هذا المسار شخصي ومحلي ولا يقدّم المحرك لطرف ثالث؛ اكتب PERSONAL LOCAL USE").Trim()
if ($PersonalAnswer -cne "PERSONAL LOCAL USE") {
    throw "هذا السكربت مخصص للاستخدام الشخصي المحلي فقط."
}

$BuildTemporaryRoot = Join-Path $env:TEMP (
    "MujassamAI-hy21-" + [Guid]::NewGuid().ToString("N"))
Assert-SafeOwnedTemporaryDirectory $BuildTemporaryRoot
New-Item -ItemType Directory -Path $BuildTemporaryRoot | Out-Null
$GitHubEnvironmentFile = Join-Path $BuildTemporaryRoot "github-env.txt"
New-Item -ItemType File -Path $GitHubEnvironmentFile | Out-Null

$ReleaseRoot = Join-Path $RepositoryRoot "release"
$PreviousReleaseBackup = $null
if (Test-Path -LiteralPath $ReleaseRoot) {
    $ReleaseItem = Get-Item -LiteralPath $ReleaseRoot -Force
    if (-not $ReleaseItem.PSIsContainer -or
        ($ReleaseItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw "release موجود لكنه ليس مجلدًا عاديًا؛ رفض البناء لحماية الملفات."
    }
    $PreviousReleaseRoot = Join-Path $RepositoryRoot "build\work"
    New-Item -ItemType Directory -Path $PreviousReleaseRoot -Force | Out-Null
    $PreviousReleaseBackup = Join-Path $PreviousReleaseRoot (
        "release.previous-" + (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") +
        "-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    Move-Item -LiteralPath $ReleaseRoot -Destination $PreviousReleaseBackup
    Write-Host "حُفظ release السابق دون حذف: $PreviousReleaseBackup" -ForegroundColor Yellow
}

$OldEnvironment = @{}
foreach ($Name in @(
    "RUNNER_TEMP", "GITHUB_ENV", "GITHUB_STEP_SUMMARY", "GITHUB_SHA", "ARCHIVE_NAME",
    "MUJASSAM_HY21_LICENSE_ACCEPTED", "MUJASSAM_HY21_TERRITORY_CONFIRMED",
    "MUJASSAM_HY21_LOCAL_PERSONAL_USE", "MUJASSAM_REQUIRE_HASHED_DEPENDENCIES",
    "MUJASSAM_PROVIDER_LEGAL_NAME"
)) {
    $OldEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

$BuildSucceeded = $false
try {
    $env:RUNNER_TEMP = $BuildTemporaryRoot
    $env:GITHUB_ENV = $GitHubEnvironmentFile
    $env:GITHUB_STEP_SUMMARY = Join-Path $BuildTemporaryRoot "build-summary.md"
    $env:GITHUB_SHA = $RepositoryCommit
    $env:ARCHIVE_NAME = $ArchiveName
    $env:MUJASSAM_HY21_LICENSE_ACCEPTED = "1"
    $env:MUJASSAM_HY21_TERRITORY_CONFIRMED = "1"
    $env:MUJASSAM_HY21_LOCAL_PERSONAL_USE = "1"
    $env:MUJASSAM_REQUIRE_HASHED_DEPENDENCIES = "1"
    Remove-Item Env:MUJASSAM_PROVIDER_LEGAL_NAME -ErrorAction SilentlyContinue

    Write-Host "بدء البناء المحلي الموثق. قد يستغرق وقتًا طويلًا." -ForegroundColor Cyan
    & $BuildScript
    if ($LASTEXITCODE -ne 0) {
        throw "فشل build-hunyuan21-update.ps1 برمز $LASTEXITCODE"
    }

    $Archive = Join-Path $ReleaseRoot $ArchiveName
    if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
        throw "اكتمل السكربت دون إنشاء ZIP المتوقع: $Archive"
    }
    $ArchiveSha256 = (Get-FileHash -LiteralPath $Archive `
        -Algorithm SHA256).Hash.ToLowerInvariant()
    $Downloads = Get-DownloadsDirectory
    New-Item -ItemType Directory -Path $Downloads -Force | Out-Null
    $DownloadedArchive = Join-Path $Downloads $ArchiveName
    if (Test-Path -LiteralPath $DownloadedArchive) {
        $ExistingHash = (Get-FileHash -LiteralPath $DownloadedArchive `
            -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($ExistingHash -cne $ArchiveSha256) {
            $DownloadedArchive = Join-Path $Downloads (
                [IO.Path]::GetFileNameWithoutExtension($ArchiveName) + "-" +
                (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss") + ".zip")
        }
    }
    if (-not (Test-Path -LiteralPath $DownloadedArchive)) {
        Copy-Item -LiteralPath $Archive -Destination $DownloadedArchive
    }
    $CopiedHash = (Get-FileHash -LiteralPath $DownloadedArchive `
        -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($CopiedHash -cne $ArchiveSha256) {
        throw "فشل التحقق من نسخة ZIP داخل Downloads."
    }
    $HashFile = $DownloadedArchive + ".sha256"
    [IO.File]::WriteAllText(
        $HashFile,
        "$ArchiveSha256  $([IO.Path]::GetFileName($DownloadedArchive))`r`n",
        [Text.UTF8Encoding]::new($false))
    $BuildSucceeded = $true

    Write-Host ""
    Write-Host "تم إنشاء التحديث والتحقق منه محليًا:" -ForegroundColor Green
    Write-Host $DownloadedArchive
    Write-Host "SHA-256: $ArchiveSha256"
    Write-Host "لم يُرفع أي ملف إلى GitHub أو أي Release." -ForegroundColor Green

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
    foreach ($Name in $OldEnvironment.Keys) {
        if ($null -eq $OldEnvironment[$Name]) {
            Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable(
                $Name, [string]$OldEnvironment[$Name], "Process")
        }
    }
    if ($BuildSucceeded -and (Test-Path -LiteralPath $BuildTemporaryRoot)) {
        Assert-SafeOwnedTemporaryDirectory $BuildTemporaryRoot
        Remove-Item -LiteralPath $BuildTemporaryRoot -Recurse -Force
        Write-Host "حُذفت ملفات المصدر والبناء المؤقتة المحلية بعد النجاح."
    } elseif (Test-Path -LiteralPath $BuildTemporaryRoot) {
        Write-Warning "فشل البناء؛ تُرك مجلد التشخيص المؤقت: $BuildTemporaryRoot"
    }
}
