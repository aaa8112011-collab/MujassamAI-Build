# Mujassam AI Portable

نسخة Windows محمولة للاستخدام الشخصي تحوّل صورة واحدة إلى مجسم **GLB** محليًا، مع إعدادات مناسبة لـRoblox Studio وUnreal Engine.

## النتيجة التي يبنيها هذا المستودع

ينشر GitHub Actions إصدارًا في **GitHub Releases** يحتوي على `Setup.exe`
وملفات runtime/model مجزأة، مع بصمات SHA-256 في `release-manifest.json`:

- `MujassamAI.exe` — الواجهة العربية.
- Python 3.11 application-local ومعزول داخل الحزمة.
- SPAR3D بوضع Low-VRAM المخصص تقريبًا لبطاقات 8GB.
- أوزان النموذج وأداة إزالة الخلفية للعمل محليًا بعد انتهاء التثبيت.

المستخدم النهائي ينزّل `Setup.exe` فقط؛ المثبّت ينزّل الأجزاء من نفس Release،
يتحقق من حجم وبصمة كل جزء، ثم يركّب الحزمة في Documents. لا يحتاج تثبيت
Python أو CUDA Toolkit أو Visual Studio. يلزم فقط Windows 10/11 ‏64-bit
وتعريف NVIDIA حديث واتصال إنترنت أثناء التثبيت الأول.

## بناء الحزمة مرة واحدة

هذا المستودع لا يخزن مفتاح Hugging Face أو أوزان النموذج في Git. الـWorkflow
ينزلها وقت البناء من المصدر الرسمي ثم ينشرها كأجزاء يقل كل منها عن 1.9GB؛
ولا يستخدم GitHub Actions Artifact storage.

1. اقبل ترخيص نموذج [Stable Point-Aware 3D](https://huggingface.co/stabilityai/stable-point-aware-3d).
2. أنشئ Hugging Face token بصلاحية **Read**.
3. أضف Repository Secret باسم `HF_TOKEN` من: **Settings → Secrets and variables → Actions**.
4. افتح **Actions → Build Mujassam AI Portable → Run workflow**.
5. بعد نجاحه افتح Release بالوسم
   `portable-<run number>-<run attempt>` ونزّل `Setup.exe`.

لا تضع المفتاح في ملف، ولا تلصقه في Workflow input أو سجل عام.

## الاستخدام

1. شغّل `Setup.exe` من GitHub Release وانتظر اكتمال التنزيل والتحقق.
2. افتح مجلد `Documents\MujassamAI-Portable` وشغّل `MujassamAI.exe`.
3. اختر صورة لجسم واحد، ومجلد النتيجة، والهدف.
4. اضغط إنشاء وانتظر ملف `.glb`.

لأفضل نتيجة: صورة واضحة لجسم كامل، منظور ثلاثة أرباع، خلفية شفافة أو بسيطة، ومن دون قص الأطراف. الصورة الواحدة لا تكشف كل الجهات؛ النموذج يقدّر الأجزاء غير المرئية.

## حدود واضحة

- الناتج Static Mesh، وليس Rig أو Animation.
- وضع Roblox يقلل الهندسة قبل صناعة الخامة حتى يكون الاستيراد أخف.
- وضع Unreal يحتفظ بهندسة أعلى ويحتاج وقتًا وذاكرة أكثر.
- SPAR3D يستهلك قرابة 7GB VRAM في Low-VRAM حسب المشروع الرسمي؛ أغلق الألعاب وBlender أثناء التوليد.
- دعم Windows في المشروع الأصلي تجريبي، لذلك يفحص الـWorkflow الملفات والاستيرادات، لكن اختبار CUDA الفعلي النهائي يجب أن يتم على جهاز NVIDIA.

## المصدر والترخيص

المحرك مبني على [SPAR3D الرسمي](https://github.com/Stability-AI/stable-point-aware-3d) وموزع وفق Stability AI Community License. يجب إبقاء ملفات الترخيص والإشعارات داخل الحزمة.

**3D Powered by Stability AI.** هذا المشروع غير تابع لـStability AI أو Roblox أو Epic Games أو Hugging Face.
