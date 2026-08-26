# Mujassam AI Portable

نسخة Windows محمولة للاستخدام الشخصي تحوّل صورة واحدة إلى مجسم **GLB** محليًا، مع إعدادات مناسبة لـRoblox Studio وUnreal Engine.

## النتيجة التي يبنيها هذا المستودع

ينشر GitHub Actions إصدارًا في **GitHub Releases** يحتوي على `Setup.exe`
وملفات runtime/model مجزأة، مع بصمات SHA-256 في `release-manifest.json`:

- `MujassamAI.exe` — الواجهة العربية.
- Python 3.11 application-local ومعزول داخل الحزمة.
- SPAR3D بوضع تلقائي يتكيّف مع بطاقات 8GB أو الأجهزة الأقوى.
- Real-ESRGAN x2plus الرسمي لترميم خامة اللون فعليًا بدقة 4K/8K.
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
4. اختر قدرة الجهاز ودقة الخامة والهندسة من **نفس البرنامج**.
5. اضغط إنشاء وانتظر ملف `.glb`.

خيارات الخامة داخل `MujassamAI.exe` الواحد:

- **Native 2K:** الخامة الأصلية PNG بلا JPEG أو شحذ مصطنع.
- **AI Studio 4K:** الخيار الموصى به. يصنع SPAR3D الخامة الأصلية 2K، ثم
  يحرر النموذج من VRAM ويستخدم Real-ESRGAN x2plus على Base Color فقط.
- **AI Studio 8K:** يظهر مع Unreal فقط، ويحتاج بطاقة أقوى بذاكرة مكتشفة
  لا تقل عن 12GB. تبقى خرائط Normal والبيانات عند 4K لأنها لا تستفيد من AI.

يمزج مسار AI النتيجة مع تكبير لوني محافظ لتقليل تغير الألوان والهالات. لا
يُشغّل GAN على Normal أو Roughness/Metallic، ويعيد تطبيع متجهات Normal بعد
تغيير الحجم. الناتج دائمًا ملف واحد باسم `model.glb` مطابق للاختيار الظاهر.

خيارات الجهاز هي **Auto** و**8GB** و**16GB+**. يكتشف Auto مقدار VRAM ويستخدم
وضع الذاكرة المنخفضة وتقسيمات AI صغيرة على 8GB، أو الوضع الكامل وتقسيمات أكبر
على الجهاز الأقوى. لا يوجد EXE منفصل لكل جهاز.

الحزمة الكاملة تبني امتدادات CUDA متعددة المعماريات لـSM 7.5 و8.0 و8.6 و8.9
مع PTX، لذلك لا تكون مقفلة على RTX 4060 Ti؛ يمكن نقل نفس البرنامج إلى جهاز
RTX 20/30/40 أقوى وتشغيل الإعداد المناسب له.

لأفضل نتيجة: صورة واضحة لجسم كامل، منظور ثلاثة أرباع، خلفية شفافة أو بسيطة، ومن دون قص الأطراف. الصورة الواحدة لا تكشف كل الجهات؛ النموذج يقدّر الأجزاء غير المرئية.

## حدود واضحة

- الناتج Static Mesh، وليس Rig أو Animation.
- AI 4K يرمم الحواف والأنسجة أفضل من تكبير Lanczos القديم، لكنه لا يستطيع
  معرفة الجوانب المخفية يقينًا من صورة واحدة.
- Roblox Game-ready يصنع نسخة تقارب 9,500 رأس. Roblox Master يحتفظ بهندسة
  SPAR الأصلية وقد يحتاج Blender قبل الاستيراد.
- Unreal Original يحتفظ بهندسة SPAR الأصلية، و8K يحتاج جهازًا أقوى.
- SPAR3D يستهلك قرابة 7GB VRAM في Low-VRAM حسب المشروع الرسمي؛ أغلق الألعاب وBlender أثناء التوليد.
- دعم Windows في المشروع الأصلي تجريبي، لذلك يفحص الـWorkflow الملفات والاستيرادات، لكن اختبار CUDA الفعلي النهائي يجب أن يتم على جهاز NVIDIA.

## المصدر والترخيص

المحرك مبني على [SPAR3D الرسمي](https://github.com/Stability-AI/stable-point-aware-3d) وموزع وفق Stability AI Community License. يجب إبقاء ملفات الترخيص والإشعارات داخل الحزمة.

**3D Powered by Stability AI.** هذا المشروع غير تابع لـStability AI أو Roblox أو Epic Games أو Hugging Face.
