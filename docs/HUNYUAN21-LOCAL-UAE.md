# بناء وتثبيت Hunyuan3D 2.1 محليًا في الإمارات

هذا المسار للاستخدام الشخصي المحلي على جهاز Windows x64 موجود في الإمارات.
لا ينشر مصدر Tencent أو الأوزان في GitHub Release أو Artifact، ولا يرسل ناتج
البناء إلى أي خدمة.

## قبل التشغيل

ثبّت الآتي مرة واحدة:

- PowerShell 7.4 أو أحدث x64 (`pwsh.exe`).
- Git for Windows، واستخدم Git clone نظيفًا للمستودع بعد دمج التحديث.
- Python 3.11.9 x64 بالضبط، واجعله أمر `python.exe` الافتراضي.
- Visual Studio 2022 Build Tools مع C++ x64 وMSVC v142/14.29.
- .NET Framework 4.8 Targeting Pack.

شغّل الملف التالي بالنقر المزدوج:

`build\Build-Hunyuan21-Local.cmd`

سيطلب Windows صلاحية Administrator، ثم يفحص الأدوات قبل أي تنزيل. سيفتح
السكربت نسخة الترخيص المثبتة في المستودع؛ وهي تشمل Acceptable Use Policy في
Exhibit A. اقرأها بنفسك، ثم اكتب عبارات الموافقة والتأكيد الظاهرة حرفيًا. لا
تُكتب الموافقة تلقائيًا نيابة عنك.

بعد نجاح البناء ستجد ZIP مع ملف بصمته `.sha256` في Downloads. يحذف السكربت
مجلد المصدر والبناء المؤقت بعد النجاح، ويحفظ أي مجلد `release` سابق باسم
احتياطي بدل حذفه.

## التثبيت المحلي

في نهاية البناء اكتب `INSTALL` ليُثبّت ZIP فورًا، أو ثبّته لاحقًا من PowerShell 7:

```powershell
$zip = "$env:USERPROFILE\Downloads\MujassamAI-Hunyuan21-PBR-Update-v1.zip"
$sha256 = ((Get-Content "$zip.sha256" -Raw).Trim() -split '\s+')[0]
pwsh -NoProfile -ExecutionPolicy Bypass -File .\installer\install-hunyuan21-local.ps1 `
  -UpdateZip $zip -ExpectedSha256 $sha256 `
  -InstallRoot "$env:USERPROFILE\Documents\MujassamAI-Portable"
```

أغلق `MujassamAI.exe` أولًا. المثبّت لا يتصل بالإنترنت: يفحص مسارات ZIP،
ويتحقق من كل حجم وSHA-256 في `update-manifest.json`، ويتأكد من ABI النسخة
الأساسية، ثم يحفظ الملفات المستبدلة في مجلد `MujassamAI-Backups` بجانب النسخة
المحمولة. لا تحذف هذا المجلد حتى تتأكد أن المحرك يعمل.

لاسترجاع النسخة السابقة، أغلق البرنامج وشغّل الأمر التالي مع مسار النسخة
الاحتياطية الذي طبعه المثبّت:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\installer\restore-hunyuan21-local.ps1 `
  -BackupRoot "$env:USERPROFILE\Documents\MujassamAI-Backups\hunyuan21-..."
```

يعرض PowerShell تأكيدًا قبل الاسترجاع، ويتحقق من بصمات ملفات النسخة
الاحتياطية قبل تغيير النسخة المحمولة.

إذا فشل البناء، اقرأ آخر خطأ فقط. ملفات التشخيص تبقى في المسار المؤقت الذي
يطبعُه السكربت، ولا يُرفع شيء منه تلقائيًا.
