using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace MujassamPortable
{
    public sealed class MainForm : Form
    {
        private const string ProgressPrefix = "MJPROGRESS|";
        private const string ArtifactPrefix = "MJARTIFACT|";
        private const string ErrorPrefix = "MJERROR|";
        private const int MaximumLogCharacters = 240000;
        internal const int JobSchemaVersion = 3;

        private const string TargetRoblox = "roblox";
        private const string TargetUnreal = "unreal";
        private const string EngineHunyuanMini = "hunyuan3d_2mini_low_vram";
        private const string EngineHunyuanPbr = "hunyuan3d_2_1_pbr";
        private const string EngineSpar3dLegacy = "spar3d_legacy";
        private const string HunyuanAcceptanceFileName = "acceptance-v1.txt";
        private const string Hunyuan21AcceptanceFileName = "acceptance-v2-1.txt";
        private const string Hunyuan21AcceptanceVersion = "hunyuan3d-2.1-v1";
        private const string Hunyuan21SourceCommit =
            "82920d643c0dc2f7bfd7255f45f62d386edfe60c";
        private const string Hunyuan21LicenseSha256 =
            "20b7e73b7996a815226ae4c08d18a7891c417749f2de687d1db90b4e36b78789";
        // Build workflows must replace this token with the publisher's confirmed
        // full legal name/entity before compiling a distributable application.
        private const string Hunyuan21ProviderLegalName = "@@MUJASSAM_PROVIDER_LEGAL_NAME@@";
        private const string Hunyuan21CiProviderSentinel =
            "CI validation build — Hunyuan3D 2.1 disabled";
        private const string TextureNative2K = "native_2k";
        private const string TextureAiStudio4K = "ai_4k";
        private const string TextureAiStudio8K = "export_8k";
        private const string HardwareAuto = "auto";
        private const string Hardware8Gb = "vram_8gb";
        private const string Hardware16GbPlus = "vram_16gb_plus";
        private const string GeometryRobloxGameReady = "target_ready";
        private const string GeometryRobloxMaster = "max_detail";
        private const string GeometryUnrealOriginal = "original";

        private readonly TextBox imagePathBox;
        private readonly TextBox outputPathBox;
        private readonly PictureBox imagePreview;
        private readonly ComboBox engineBox;
        private readonly ComboBox targetBox;
        private readonly ComboBox textureBox;
        private readonly ComboBox hardwareBox;
        private readonly ComboBox geometryBox;
        private readonly Label optionsHint;
        private readonly Button browseImageButton;
        private readonly Button browseOutputButton;
        private readonly Button generateButton;
        private readonly Button cancelButton;
        private readonly Button openResultButton;
        private readonly ProgressBar progressBar;
        private readonly Label statusLabel;
        private readonly TextBox logBox;

        private readonly object processLock = new object();
        private Process workerProcess;
        private WindowsProcessJob workerJob;
        private string activeJobPath;
        private string activeOutputDirectory;
        private string resultArtifactPath;
        private string reportedWorkerError;
        private bool cancellationRequested;
        private bool formIsClosing;

        public MainForm()
        {
            Text = "Mujassam AI — تحويل صورة إلى مجسم 3D";
            StartPosition = FormStartPosition.CenterScreen;
            MinimumSize = new Size(900, 690);
            ClientSize = new Size(1040, 760);
            Font = new Font("Segoe UI", 9.5F, FontStyle.Regular, GraphicsUnit.Point);
            AutoScaleMode = AutoScaleMode.Dpi;
            BackColor = Color.FromArgb(245, 247, 250);
            RightToLeft = RightToLeft.Yes;
            RightToLeftLayout = true;

            imagePathBox = CreatePathTextBox();
            outputPathBox = CreatePathTextBox();
            imagePreview = new PictureBox();
            engineBox = CreateDropDown();
            targetBox = CreateDropDown();
            textureBox = CreateDropDown();
            hardwareBox = CreateDropDown();
            geometryBox = CreateDropDown();
            optionsHint = new Label();
            browseImageButton = CreateSecondaryButton("اختيار صورة...");
            browseOutputButton = CreateSecondaryButton("اختيار مجلد...");
            generateButton = CreatePrimaryButton("إنشاء المجسم");
            cancelButton = CreateSecondaryButton("إلغاء");
            openResultButton = CreateSecondaryButton("فتح النتيجة");
            progressBar = new ProgressBar();
            statusLabel = new Label();
            logBox = new TextBox();

            BuildInterface();
            PopulateOptions();
            WireEvents();
            SetDefaultOutputFolder();
            UpdateTargetControls();
            SetRunningState(false);
        }

        private static TextBox CreatePathTextBox()
        {
            return new TextBox
            {
                Dock = DockStyle.Fill,
                BorderStyle = BorderStyle.FixedSingle,
                RightToLeft = RightToLeft.No,
                Font = new Font("Segoe UI", 9.5F),
                Margin = new Padding(5, 7, 5, 7)
            };
        }

        private static ComboBox CreateDropDown()
        {
            return new ComboBox
            {
                DropDownStyle = ComboBoxStyle.DropDownList,
                Dock = DockStyle.Fill,
                FlatStyle = FlatStyle.System,
                IntegralHeight = false,
                DropDownHeight = 150,
                Margin = new Padding(5, 6, 5, 6)
            };
        }

        private static Label CreateFieldLabel(string text)
        {
            return new Label
            {
                Text = text,
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleRight,
                AutoEllipsis = true,
                Margin = new Padding(4)
            };
        }

        private static Button CreateSecondaryButton(string text)
        {
            return new Button
            {
                Text = text,
                AutoSize = true,
                MinimumSize = new Size(118, 34),
                FlatStyle = FlatStyle.System,
                Margin = new Padding(6)
            };
        }

        private static Button CreatePrimaryButton(string text)
        {
            Button button = CreateSecondaryButton(text);
            button.MinimumSize = new Size(155, 38);
            button.Font = new Font("Segoe UI Semibold", 10F, FontStyle.Bold);
            return button;
        }

        private void BuildInterface()
        {
            TableLayoutPanel root = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                BackColor = BackColor,
                Padding = new Padding(18, 14, 18, 14),
                ColumnCount = 1,
                RowCount = 6
            };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 58));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 205));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 65));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 53));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 165));
            Controls.Add(root);

            Panel titlePanel = new Panel { Dock = DockStyle.Fill };
            Label title = new Label
            {
                Text = "حوّل صورة واحدة إلى مجسم 3D محليًا",
                Dock = DockStyle.Top,
                Height = 31,
                Font = new Font("Segoe UI Semibold", 17F, FontStyle.Bold),
                ForeColor = Color.FromArgb(28, 39, 55),
                TextAlign = ContentAlignment.MiddleRight
            };
            Label subtitle = new Label
            {
                Text = "اختر الصورة والإعدادات، ثم اترك النافذة مفتوحة حتى يكتمل التصدير.",
                Dock = DockStyle.Bottom,
                Height = 23,
                ForeColor = Color.FromArgb(90, 99, 112),
                TextAlign = ContentAlignment.MiddleRight
            };
            titlePanel.Controls.Add(title);
            titlePanel.Controls.Add(subtitle);
            root.Controls.Add(titlePanel, 0, 0);

            TableLayoutPanel inputArea = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 2,
                RowCount = 1,
                Margin = new Padding(0, 8, 0, 8)
            };
            inputArea.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 43));
            inputArea.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 57));
            root.Controls.Add(inputArea, 0, 1);

            Panel previewCard = CreateCard();
            previewCard.Margin = new Padding(8, 0, 0, 0);
            Label previewTitle = new Label
            {
                Text = "معاينة الصورة",
                Dock = DockStyle.Top,
                Height = 34,
                Font = new Font("Segoe UI Semibold", 10F, FontStyle.Bold),
                TextAlign = ContentAlignment.MiddleRight,
                Padding = new Padding(10, 0, 10, 0)
            };
            imagePreview.Dock = DockStyle.Fill;
            imagePreview.SizeMode = PictureBoxSizeMode.Zoom;
            imagePreview.BackColor = Color.FromArgb(225, 229, 235);
            imagePreview.BorderStyle = BorderStyle.FixedSingle;
            imagePreview.Margin = new Padding(12);
            Panel imageHost = new Panel { Dock = DockStyle.Fill, Padding = new Padding(12, 5, 12, 12) };
            imageHost.Controls.Add(imagePreview);
            previewCard.Controls.Add(imageHost);
            previewCard.Controls.Add(previewTitle);
            inputArea.Controls.Add(previewCard, 0, 0);

            Panel pathsCard = CreateCard();
            pathsCard.Margin = new Padding(0, 0, 8, 0);
            TableLayoutPanel paths = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                Padding = new Padding(13, 12, 13, 10),
                ColumnCount = 3,
                RowCount = 4
            };
            paths.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 115));
            paths.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            paths.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 132));
            paths.RowStyles.Add(new RowStyle(SizeType.Absolute, 49));
            paths.RowStyles.Add(new RowStyle(SizeType.Absolute, 49));
            paths.RowStyles.Add(new RowStyle(SizeType.Absolute, 22));
            paths.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            paths.Controls.Add(CreateFieldLabel("الصورة"), 0, 0);
            paths.Controls.Add(imagePathBox, 1, 0);
            paths.Controls.Add(browseImageButton, 2, 0);
            paths.Controls.Add(CreateFieldLabel("مجلد الإخراج"), 0, 1);
            paths.Controls.Add(outputPathBox, 1, 1);
            paths.Controls.Add(browseOutputButton, 2, 1);
            Label inputHint = new Label
            {
                Text = "أفضل نتيجة: عنصر واحد كامل، خلفية شفافة أو بسيطة، وإضاءة واضحة.",
                Dock = DockStyle.Fill,
                ForeColor = Color.FromArgb(91, 101, 116),
                TextAlign = ContentAlignment.MiddleRight,
                AutoEllipsis = true,
                Margin = new Padding(5, 0, 5, 0)
            };
            paths.SetColumnSpan(inputHint, 2);
            paths.Controls.Add(inputHint, 1, 2);
            Label packageHint = new Label
            {
                Text = "سيعمل المحرك مباشرة من داخل الحزمة نفسها، دون تثبيت Python أو مكونات إضافية.",
                Dock = DockStyle.Top,
                Height = 45,
                ForeColor = Color.FromArgb(70, 80, 95),
                TextAlign = ContentAlignment.MiddleRight,
                Padding = new Padding(5, 9, 5, 0),
                AutoEllipsis = true
            };
            paths.SetColumnSpan(packageHint, 2);
            paths.Controls.Add(packageHint, 1, 3);
            pathsCard.Controls.Add(paths);
            inputArea.Controls.Add(pathsCard, 1, 0);

            Panel optionsCard = CreateCard();
            optionsCard.Margin = new Padding(0, 2, 0, 8);
            TableLayoutPanel options = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                Padding = new Padding(13, 10, 13, 9),
                ColumnCount = 4,
                RowCount = 4
            };
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 105));
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50F));
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 112));
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50F));
            options.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
            options.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
            options.RowStyles.Add(new RowStyle(SizeType.Absolute, 45));
            options.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            options.Controls.Add(CreateFieldLabel("محرك 3D"), 0, 0);
            options.Controls.Add(engineBox, 1, 0);
            options.Controls.Add(CreateFieldLabel("قدرة الجهاز"), 2, 0);
            options.Controls.Add(hardwareBox, 3, 0);
            options.Controls.Add(CreateFieldLabel("الهدف"), 0, 1);
            options.Controls.Add(targetBox, 1, 1);
            options.Controls.Add(CreateFieldLabel("الهندسة"), 2, 1);
            options.Controls.Add(geometryBox, 3, 1);
            options.Controls.Add(CreateFieldLabel("دقة الخامة"), 0, 2);
            options.Controls.Add(textureBox, 1, 2);
            options.SetColumnSpan(textureBox, 3);
            optionsHint.Dock = DockStyle.Fill;
            optionsHint.ForeColor = Color.FromArgb(91, 101, 116);
            optionsHint.TextAlign = ContentAlignment.MiddleRight;
            optionsHint.AutoEllipsis = true;
            optionsHint.Padding = new Padding(4, 4, 4, 0);
            options.SetColumnSpan(optionsHint, 4);
            options.Controls.Add(optionsHint, 0, 3);
            optionsCard.Controls.Add(options);
            root.Controls.Add(optionsCard, 0, 2);

            Panel progressPanel = new Panel { Dock = DockStyle.Fill, Padding = new Padding(3, 6, 3, 5) };
            statusLabel.Text = "جاهز";
            statusLabel.Dock = DockStyle.Top;
            statusLabel.Height = 27;
            statusLabel.TextAlign = ContentAlignment.MiddleRight;
            statusLabel.Font = new Font("Segoe UI Semibold", 9.5F, FontStyle.Bold);
            statusLabel.AutoEllipsis = true;
            progressBar.Dock = DockStyle.Bottom;
            progressBar.Height = 19;
            progressBar.Minimum = 0;
            progressBar.Maximum = 100;
            progressPanel.Controls.Add(statusLabel);
            progressPanel.Controls.Add(progressBar);
            root.Controls.Add(progressPanel, 0, 3);

            FlowLayoutPanel actions = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill,
                FlowDirection = FlowDirection.RightToLeft,
                WrapContents = false,
                Padding = new Padding(0, 2, 0, 2)
            };
            actions.Controls.Add(generateButton);
            actions.Controls.Add(cancelButton);
            actions.Controls.Add(openResultButton);
            root.Controls.Add(actions, 0, 4);

            Panel logCard = CreateCard();
            logCard.Margin = new Padding(0, 6, 0, 0);
            Label logTitle = new Label
            {
                Text = "السجل",
                Dock = DockStyle.Top,
                Height = 30,
                Padding = new Padding(9, 0, 9, 0),
                TextAlign = ContentAlignment.MiddleRight,
                Font = new Font("Segoe UI Semibold", 9.5F, FontStyle.Bold)
            };
            logBox.Dock = DockStyle.Fill;
            logBox.Multiline = true;
            logBox.ReadOnly = true;
            logBox.ScrollBars = ScrollBars.Vertical;
            logBox.WordWrap = false;
            logBox.BackColor = Color.FromArgb(251, 252, 253);
            logBox.BorderStyle = BorderStyle.None;
            logBox.RightToLeft = RightToLeft.No;
            logBox.Font = new Font("Consolas", 9F);
            logBox.Margin = new Padding(10);
            Panel logHost = new Panel { Dock = DockStyle.Fill, Padding = new Padding(10, 0, 10, 10) };
            logHost.Controls.Add(logBox);
            logCard.Controls.Add(logHost);
            logCard.Controls.Add(logTitle);
            root.Controls.Add(logCard, 0, 5);
        }

        private static Panel CreateCard()
        {
            return new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle
            };
        }

        private void PopulateOptions()
        {
            bool hunyuanMiniInstalled = IsHunyuanMiniInstalled();
            bool hunyuanPbrInstalled = IsHunyuanPbrInstalled();
            engineBox.Items.Add(hunyuanMiniInstalled
                ? "Hunyuan3D 2mini — متاح لـ 8GB (موصى به)"
                : "Hunyuan3D 2mini — يحتاج تحديث المحرك");
            engineBox.Items.Add(hunyuanPbrInstalled
                ? "Hunyuan3D 2.1 Ultimate / PBR — أقوى جودة"
                : "Hunyuan3D 2.1 Ultimate / PBR — يحتاج تحديث المحرك");
            engineBox.Items.Add("SPAR3D — قديم / احتياطي");
            engineBox.SelectedIndex = hunyuanMiniInstalled ? 0 : (hunyuanPbrInstalled ? 1 : 2);

            targetBox.Items.Add("Roblox Studio");
            targetBox.Items.Add("Unreal Engine");
            targetBox.SelectedIndex = 0;

            hardwareBox.Items.Add("Auto — يكتشف الجهاز تلقائيًا");
            hardwareBox.Items.Add("8GB VRAM — Hunyuan 2mini Low-VRAM");
            hardwareBox.Items.Add("16GB+ VRAM — معالجة أثقل و8K");
            hardwareBox.SelectedIndex = 0;
        }

        private void WireEvents()
        {
            browseImageButton.Click += BrowseImage;
            browseOutputButton.Click += BrowseOutput;
            generateButton.Click += StartGeneration;
            cancelButton.Click += CancelGeneration;
            openResultButton.Click += OpenResult;
            engineBox.SelectedIndexChanged += delegate { UpdateEngineControls(); };
            targetBox.SelectedIndexChanged += delegate { UpdateTargetControls(); };
            textureBox.SelectedIndexChanged += delegate { UpdateOptionsHint(); };
            hardwareBox.SelectedIndexChanged += delegate { UpdateOptionsHint(); };
            geometryBox.SelectedIndexChanged += delegate { UpdateOptionsHint(); };
            imagePathBox.TextChanged += delegate { TryLoadPreview(imagePathBox.Text); };
            FormClosing += OnMainFormClosing;
            FormClosed += OnMainFormClosed;
        }

        private void SetDefaultOutputFolder()
        {
            string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string downloads = String.IsNullOrWhiteSpace(profile)
                ? String.Empty
                : Path.Combine(profile, "Downloads");
            if (String.IsNullOrWhiteSpace(downloads) || !Directory.Exists(downloads))
                downloads = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            outputPathBox.Text = Path.Combine(downloads, "MujassamAI-Exports");
        }

        private void UpdateTargetControls()
        {
            bool roblox = targetBox.SelectedIndex == 0;
            int previousTexture = textureBox.SelectedIndex;

            textureBox.BeginUpdate();
            textureBox.Items.Clear();
            textureBox.Items.Add("Native 2K — أصلي وأسرع");
            textureBox.Items.Add("AI Studio 4K — موصى به");
            if (!roblox)
                textureBox.Items.Add("AI Studio 8K — Unreal فقط");
            textureBox.SelectedIndex = previousTexture >= 0 && previousTexture < textureBox.Items.Count
                ? previousTexture
                : 1;
            textureBox.EndUpdate();

            geometryBox.BeginUpdate();
            geometryBox.Items.Clear();
            if (roblox)
            {
                geometryBox.Items.Add("Game-ready — جاهز للعبة");
                geometryBox.Items.Add("Master — أعلى تفاصيل");
            }
            else
            {
                geometryBox.Items.Add("Original — كامل التفاصيل");
            }
            geometryBox.SelectedIndex = 0;
            geometryBox.EndUpdate();

            bool idle = workerProcess == null;
            textureBox.Enabled = idle;
            geometryBox.Enabled = idle;
            UpdateOptionsHint();
        }

        private void UpdateEngineControls()
        {
            // Ultimate PBR is designed around its full-quality output.  Keep the
            // user's hardware selection untouched, but choose the matching 4K
            // texture profile when they switch to this engine.
            if (engineBox.SelectedIndex == 1 && textureBox.Items.Count > 1 &&
                textureBox.SelectedIndex != 1)
                textureBox.SelectedIndex = 1;
            UpdateOptionsHint();
        }

        private void UpdateOptionsHint()
        {
            if (engineBox.SelectedIndex < 0 || targetBox.SelectedIndex < 0 || textureBox.SelectedIndex < 0 ||
                hardwareBox.SelectedIndex < 0 || geometryBox.SelectedIndex < 0)
            {
                optionsHint.Text = String.Empty;
                return;
            }

            string hardwareHint;
            if (engineBox.SelectedIndex == 1)
                hardwareHint = "Ultimate يعمل دائمًا بملف الجودة الكامل؛ إعداد Low-VRAM لا يخفّض المحرك";
            else if (hardwareBox.SelectedIndex == 1)
                hardwareHint = "8GB متاح فعليًا لـ Hunyuan 2mini عبر Low-VRAM";
            else if (hardwareBox.SelectedIndex == 2)
                hardwareHint = "16GB+ يفتح الوضع الكامل وتصدير 8K على الجهاز الأقوى";
            else
                hardwareHint = "Auto يفحص VRAM ويختار الوضع المناسب";

            string engineHint;
            if (engineBox.SelectedIndex == 0)
                engineHint = "Hunyuan3D 2mini متاح على 8GB؛ يستخدم Low-VRAM وقد يستعين بـRAM ويستغرق وقتًا أطول";
            else if (engineBox.SelectedIndex == 1)
                engineHint = "Hunyuan3D 2.1 Ultimate هو أقوى وضع محلي، وينشئ هندسة كاملة وخامات PBR";
            else
                engineHint = "SPAR3D هو المحرك القديم الاحتياطي";

            string textureHint;
            if (engineBox.SelectedIndex == 1 && textureBox.SelectedIndex == 0)
                textureHint = "Native 2K يصغّر خرائط PBR النهائية إلى 2K";
            else if (engineBox.SelectedIndex == 1 && textureBox.SelectedIndex == 1)
                textureHint = "AI Studio 4K هو الإعداد الموصى به لخرائط PBR";
            else if (textureBox.SelectedIndex == 0)
                textureHint = "Native 2K أسرع بلا ترميم AI";
            else if (textureBox.SelectedIndex == 2)
                textureHint = "AI 8K مخصص لتصدير Unreal ويحتاج وقتًا وذاكرة أكثر";
            else
                textureHint = "AI Studio 4K يرمم الخامة وهو الخيار الموصى به";

            string geometryHint;
            if (targetBox.SelectedIndex == 1)
                geometryHint = "Unreal Original يحافظ على المجسم الكامل";
            else if (geometryBox.SelectedIndex == 1)
                geometryHint = "Roblox Master يحفظ أعلى تفاصيل وقد يحتاج تقليلًا قبل النشر";
            else
                geometryHint = "Roblox Game-ready أخف وجاهز للاستخدام";

            optionsHint.Text = engineHint + " • " + hardwareHint + " • " + textureHint + " • " + geometryHint + ".";
        }

        private void BrowseImage(object sender, EventArgs e)
        {
            using (OpenFileDialog dialog = new OpenFileDialog())
            {
                dialog.Title = "اختر صورة العنصر";
                dialog.Filter = "صور مدعومة (*.png;*.jpg;*.jpeg;*.webp)|*.png;*.jpg;*.jpeg;*.webp|كل الملفات (*.*)|*.*";
                dialog.CheckFileExists = true;
                dialog.Multiselect = false;
                if (File.Exists(imagePathBox.Text))
                    dialog.InitialDirectory = Path.GetDirectoryName(Path.GetFullPath(imagePathBox.Text));
                if (dialog.ShowDialog(this) == DialogResult.OK)
                    imagePathBox.Text = dialog.FileName;
            }
        }

        private void BrowseOutput(object sender, EventArgs e)
        {
            using (FolderBrowserDialog dialog = new FolderBrowserDialog())
            {
                dialog.Description = "اختر مجلد حفظ المجسم والملفات المرافقة";
                dialog.ShowNewFolderButton = true;
                if (Directory.Exists(outputPathBox.Text))
                    dialog.SelectedPath = outputPathBox.Text;
                if (dialog.ShowDialog(this) == DialogResult.OK)
                    outputPathBox.Text = dialog.SelectedPath;
            }
        }

        private void TryLoadPreview(string path)
        {
            Image replacement = null;
            try
            {
                if (File.Exists(path))
                {
                    byte[] bytes = File.ReadAllBytes(path);
                    using (MemoryStream stream = new MemoryStream(bytes, false))
                    using (Image loaded = Image.FromStream(stream, true, true))
                        replacement = new Bitmap(loaded);
                }
            }
            catch (Exception ex)
            {
                AppendLog("تعذر عرض معاينة الصورة: " + ex.Message);
            }

            Image previous = imagePreview.Image;
            imagePreview.Image = replacement;
            if (previous != null)
                previous.Dispose();
        }

        private void StartGeneration(object sender, EventArgs e)
        {
            if (workerProcess != null)
                return;

            string imagePath;
            string outputDirectory;
            try
            {
                imagePath = Path.GetFullPath(imagePathBox.Text.Trim());
                outputDirectory = Path.GetFullPath(outputPathBox.Text.Trim());
            }
            catch (Exception ex)
            {
                ShowInputError("أحد المسارات غير صالح: " + ex.Message);
                return;
            }

            if (!File.Exists(imagePath))
            {
                ShowInputError("اختر صورة موجودة أولًا.");
                return;
            }
            string extension = Path.GetExtension(imagePath).ToLowerInvariant();
            if (extension != ".png" && extension != ".jpg" && extension != ".jpeg" && extension != ".webp")
            {
                ShowInputError("صيغة الصورة غير مدعومة. استخدم PNG أو JPG أو JPEG أو WebP.");
                return;
            }
            if (String.IsNullOrWhiteSpace(outputDirectory))
            {
                ShowInputError("اختر مجلد إخراج.");
                return;
            }
            if (engineBox.SelectedIndex == 0 && !IsHunyuanMiniInstalled())
            {
                ShowInputError(
                    "مكوّن Hunyuan3D غير موجود داخل مجلد البرنامج.\r\n\r\n" +
                    "نزّل تحديث Hunyuan3D واستخرج محتوياته فوق مجلد MujassamAI-Portable ثم افتح البرنامج مجددًا.");
                return;
            }
            if (engineBox.SelectedIndex == 1 && !IsHunyuanPbrInstalled())
            {
                ShowInputError(
                    "مكوّن Hunyuan3D 2.1 Ultimate / PBR غير موجود داخل مجلد البرنامج.\r\n\r\n" +
                    "نزّل تحديث Ultimate PBR واستخرج محتوياته فوق مجلد MujassamAI-Portable ثم افتح البرنامج مجددًا.");
                return;
            }
            if (engineBox.SelectedIndex == 1 && !IsHunyuan21ProviderConfigured())
            {
                ShowInputError(
                    "بيانات مزوّد Hunyuan3D 2.1 القانونية غير مضمنة في هذه الحزمة.\r\n\r\n" +
                    "لا يمكن تشغيل Ultimate / PBR من حزمة غير مهيأة للنشر. استخدم إصدارًا رسميًا مكتملًا.");
                return;
            }
            if (!EnsureHunyuanLicenseAccepted())
                return;

            string baseDirectory = AppDomain.CurrentDomain.BaseDirectory;
            string pythonPath = Path.Combine(baseDirectory, "rt", "python.exe");
            string workerPath = Path.Combine(baseDirectory, "app", "worker.py");
            if (!File.Exists(pythonPath) || !File.Exists(workerPath))
            {
                string missing = !File.Exists(pythonPath) ? pythonPath : workerPath;
                ShowInputError(
                    "الحزمة غير مكتملة. أعد تشغيل Setup.exe المطابق لهذا الإصدار ثم اختر إعادة التثبيت.\r\n\r\nالملف المفقود:\r\n" + missing);
                return;
            }

            try
            {
                Directory.CreateDirectory(outputDirectory);
                string jobPath = WriteJobFile(imagePath, outputDirectory);
                BeginWorker(pythonPath, workerPath, jobPath, outputDirectory);
            }
            catch (Exception ex)
            {
                DeleteActiveJobFile();
                SetRunningState(false);
                AppendLog("فشل بدء المحرك: " + ex);
                MessageBox.Show(this, "تعذر بدء المحرك.\r\n\r\n" + ex.Message, Text,
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private string WriteJobFile(string imagePath, string outputDirectory)
        {
            Dictionary<string, object> job = CreateJobPayload(
                imagePath,
                outputDirectory,
                engineBox.SelectedIndex,
                targetBox.SelectedIndex,
                textureBox.SelectedIndex,
                hardwareBox.SelectedIndex,
                geometryBox.SelectedIndex);

            string jobsDirectory = Path.Combine(Path.GetTempPath(), "MujassamPortable", "jobs");
            Directory.CreateDirectory(jobsDirectory);
            string jobId = Guid.NewGuid().ToString("N");
            string finalPath = Path.Combine(jobsDirectory, jobId + ".json");
            string temporaryPath = finalPath + ".new";
            JavaScriptSerializer serializer = new JavaScriptSerializer();
            serializer.MaxJsonLength = Int32.MaxValue;
            File.WriteAllText(temporaryPath, serializer.Serialize(job), new UTF8Encoding(false));
            if (File.Exists(finalPath))
                File.Delete(finalPath);
            File.Move(temporaryPath, finalPath);
            return finalPath;
        }

        internal static Dictionary<string, object> CreateJobPayload(
            string imagePath,
            string outputDirectory,
            int engineIndex,
            int targetIndex,
            int textureIndex,
            int hardwareIndex,
            int geometryIndex)
        {
            string engineMode;
            if (engineIndex == 1)
                engineMode = EngineHunyuanPbr;
            else if (engineIndex == 2)
                engineMode = EngineSpar3dLegacy;
            else
                engineMode = EngineHunyuanMini;

            string target = targetIndex == 0 ? TargetRoblox : TargetUnreal;
            string textureMode;
            if (textureIndex == 0)
                textureMode = TextureNative2K;
            else if (textureIndex == 2 && target == TargetUnreal)
                textureMode = TextureAiStudio8K;
            else
                textureMode = TextureAiStudio4K;

            string hardwarePreset;
            if (hardwareIndex == 1)
                hardwarePreset = Hardware8Gb;
            else if (hardwareIndex == 2)
                hardwarePreset = Hardware16GbPlus;
            else
                hardwarePreset = HardwareAuto;

            string geometryMode;
            if (target == TargetUnreal)
                geometryMode = GeometryUnrealOriginal;
            else if (geometryIndex == 1)
                geometryMode = GeometryRobloxMaster;
            else
                geometryMode = GeometryRobloxGameReady;

            Dictionary<string, object> job = new Dictionary<string, object>();
            job["schema_version"] = JobSchemaVersion;
            job["image_path"] = imagePath;
            job["output_dir"] = outputDirectory;
            job["engine_mode"] = engineMode;
            job["target"] = target;
            job["texture_mode"] = textureMode;
            job["hardware_preset"] = hardwarePreset;
            job["geometry_mode"] = geometryMode;
            return job;
        }

        internal static string ValidateConfigurationSchema()
        {
            Dictionary<string, object> roblox = CreateJobPayload(
                "input.png", "output", 0, 0, 1, 0, 1);
            if (!Object.Equals(roblox["schema_version"], JobSchemaVersion) ||
                !Object.Equals(roblox["engine_mode"], EngineHunyuanMini) ||
                !Object.Equals(roblox["target"], TargetRoblox) ||
                !Object.Equals(roblox["texture_mode"], TextureAiStudio4K) ||
                !Object.Equals(roblox["hardware_preset"], HardwareAuto) ||
                !Object.Equals(roblox["geometry_mode"], GeometryRobloxMaster))
                return "Roblox schema mapping failed.";

            Dictionary<string, object> unreal = CreateJobPayload(
                "input.png", "output", 1, 1, 2, 2, 0);
            if (!Object.Equals(unreal["schema_version"], JobSchemaVersion) ||
                !Object.Equals(unreal["engine_mode"], EngineHunyuanPbr) ||
                !Object.Equals(unreal["target"], TargetUnreal) ||
                !Object.Equals(unreal["texture_mode"], TextureAiStudio8K) ||
                !Object.Equals(unreal["hardware_preset"], Hardware16GbPlus) ||
                !Object.Equals(unreal["geometry_mode"], GeometryUnrealOriginal))
                return "Unreal schema mapping failed.";

            Dictionary<string, object> pbrRoblox = CreateJobPayload(
                "input.png", "output", 1, 0, 1, 0, 0);
            if (!Object.Equals(pbrRoblox["engine_mode"], EngineHunyuanPbr) ||
                !Object.Equals(pbrRoblox["target"], TargetRoblox) ||
                !Object.Equals(pbrRoblox["texture_mode"], TextureAiStudio4K) ||
                !Object.Equals(pbrRoblox["hardware_preset"], HardwareAuto) ||
                !Object.Equals(pbrRoblox["geometry_mode"], GeometryRobloxGameReady))
                return "Ultimate PBR schema mapping failed.";

            Dictionary<string, object> legacy = CreateJobPayload(
                "input.png", "output", 2, 0, 0, 1, 0);
            if (!Object.Equals(legacy["engine_mode"], EngineSpar3dLegacy))
                return "SPAR3D schema mapping failed.";

            if (!RequiresHunyuanAcceptance(0) || !RequiresHunyuanAcceptance(1) ||
                RequiresHunyuanAcceptance(2))
                return "Hunyuan license acceptance mapping failed.";
            string acceptancePath = GetHunyuanAcceptancePath();
            if (!Path.IsPathRooted(acceptancePath) ||
                !String.Equals(Path.GetFileName(acceptancePath), HunyuanAcceptanceFileName,
                    StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(Path.GetFileName(Path.GetDirectoryName(acceptancePath)), "Licenses",
                    StringComparison.OrdinalIgnoreCase))
                return "Hunyuan license acceptance path failed.";
            string pbrAcceptancePath = GetHunyuan21AcceptancePath();
            if (!Path.IsPathRooted(pbrAcceptancePath) ||
                !String.Equals(Path.GetFileName(pbrAcceptancePath), Hunyuan21AcceptanceFileName,
                    StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(Path.GetFileName(Path.GetDirectoryName(pbrAcceptancePath)), "Licenses",
                    StringComparison.OrdinalIgnoreCase) ||
                String.Equals(acceptancePath, pbrAcceptancePath, StringComparison.OrdinalIgnoreCase) ||
                Hunyuan21SourceCommit.Length != 40 || Hunyuan21LicenseSha256.Length != 64)
                return "Hunyuan 2.1 license acceptance path failed.";
            if (roblox.Count != 8 || unreal.Count != 8 || pbrRoblox.Count != 8 || legacy.Count != 8)
                return "Unexpected job schema field count.";
            return String.Empty;
        }

        private static bool RequiresHunyuanAcceptance(int engineIndex)
        {
            return engineIndex == 0 || engineIndex == 1;
        }

        private static bool IsHunyuanMiniInstalled()
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            return File.Exists(Path.Combine(root, "app", "engines", "hunyuan2", "hunyuan2_worker.py")) &&
                File.Exists(Path.Combine(root, "app", "engines", "hunyuan2", "ENGINE-MANIFEST.json"));
        }

        private static bool IsHunyuanPbrInstalled()
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            return File.Exists(Path.Combine(root, "app", "engines", "hunyuan21", "hunyuan21_worker.py")) &&
                File.Exists(Path.Combine(root, "app", "engines", "hunyuan21", "ENGINE-MANIFEST.json"));
        }

        private static bool IsHunyuan21ProviderConfigured()
        {
            return !String.IsNullOrWhiteSpace(Hunyuan21ProviderLegalName) &&
                Hunyuan21ProviderLegalName.IndexOf("@@", StringComparison.Ordinal) < 0 &&
                !String.Equals(Hunyuan21ProviderLegalName, Hunyuan21CiProviderSentinel,
                    StringComparison.Ordinal);
        }

        internal static string GetHunyuanAcceptancePath()
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (String.IsNullOrWhiteSpace(localAppData))
                localAppData = Path.GetTempPath();
            return Path.Combine(localAppData, "MujassamAI", "Licenses", HunyuanAcceptanceFileName);
        }

        internal static string GetHunyuan21AcceptancePath()
        {
            string localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (String.IsNullOrWhiteSpace(localAppData))
                localAppData = Path.GetTempPath();
            return Path.Combine(localAppData, "MujassamAI", "Licenses", Hunyuan21AcceptanceFileName);
        }

        private bool EnsureHunyuanLicenseAccepted()
        {
            if (!RequiresHunyuanAcceptance(engineBox.SelectedIndex))
                return true;

            bool hunyuan21 = engineBox.SelectedIndex == 1;
            string acceptancePath = hunyuan21
                ? GetHunyuan21AcceptancePath()
                : GetHunyuanAcceptancePath();
            if (File.Exists(acceptancePath) &&
                (!hunyuan21 || IsCurrentHunyuan21Acceptance(acceptancePath)))
                return true;

            string message;
            string dialogTitle;
            if (hunyuan21)
            {
                dialogTitle = "ترخيص Hunyuan3D 2.1";
                message =
                    "قبل تشغيل Hunyuan3D 2.1 Ultimate / PBR، يلزم تأكيد ما يلي:\r\n\r\n" +
                    "• أؤكد أن الاستخدام يتم خارج الاتحاد الأوروبي (EU) والمملكة المتحدة (UK) " +
                    "وكوريا الجنوبية، وهي المناطق المستثناة من الترخيص.\r\n\r\n" +
                    "• قرأت وأوافق على Tencent Hunyuan 3D 2.1 Community License Agreement " +
                    "وسياسة الاستخدام المقبول المرفقة به.\r\n\r\n" +
                    "• الاسم القانوني لمزوّد هذا التطبيق والتكامل المحلي هو: " + Hunyuan21ProviderLegalName +
                    ". Tencent غير مرتبطة بهذا المنتج ولا تشاركه أو ترعاه أو تؤيده.\r\n\r\n" +
                    "اختر نعم للموافقة والمتابعة، أو لا للإلغاء.";
            }
            else
            {
                dialogTitle = "ترخيص Hunyuan3D";
                message =
                    "قبل تشغيل Hunyuan3D، يلزم تأكيد ما يلي:\r\n\r\n" +
                    "• أؤكد أن الاستخدام يتم داخل منطقة يسمح بها الترخيص. " +
                    "الترخيص يستثني الاتحاد الأوروبي (EU) والمملكة المتحدة (UK) وكوريا الجنوبية.\r\n\r\n" +
                    "• أوافق على Tencent Hunyuan 3D 2.0 Community License Agreement.\r\n\r\n" +
                    "اختر نعم للموافقة والمتابعة، أو لا للإلغاء.";
            }
            DialogResult answer = MessageBox.Show(
                this,
                message,
                dialogTitle,
                MessageBoxButtons.YesNo,
                MessageBoxIcon.Information,
                MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes)
                return false;

            try
            {
                string directory = Path.GetDirectoryName(acceptancePath);
                Directory.CreateDirectory(directory);
                string temporaryPath = acceptancePath + ".new";
                string record;
                if (hunyuan21)
                {
                    record =
                        "MujassamAI Hunyuan acceptance v2.1\r\n" +
                        "acceptance_version=" + Hunyuan21AcceptanceVersion + "\r\n" +
                        "source_commit=" + Hunyuan21SourceCommit + "\r\n" +
                        "license=Tencent Hunyuan 3D 2.1 Community License Agreement\r\n" +
                        "license_sha256=" + Hunyuan21LicenseSha256 + "\r\n" +
                        "territory_confirmation=outside EU, UK, and South Korea\r\n" +
                        "license_terms_acknowledged=true\r\n" +
                        "acceptable_use_policy_acknowledged=true\r\n" +
                        "provider_legal_name=" + Hunyuan21ProviderLegalName + "\r\n" +
                        "provider_disclosure_acknowledged=true\r\n" +
                        "tencent_non_affiliation_acknowledged=true\r\n" +
                        "accepted_utc=" + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + "\r\n";
                }
                else
                {
                    record =
                        "MujassamAI Hunyuan acceptance v1\r\n" +
                        "license=Tencent Hunyuan 3D 2.0 Community License Agreement\r\n" +
                        "territory_confirmation=outside EU, UK, and South Korea\r\n" +
                        "accepted_utc=" + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + "\r\n";
                }
                File.WriteAllText(temporaryPath, record, new UTF8Encoding(false));
                if (File.Exists(acceptancePath))
                    File.Replace(temporaryPath, acceptancePath, null);
                else
                    File.Move(temporaryPath, acceptancePath);
                return File.Exists(acceptancePath);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    this,
                    "تعذر حفظ موافقة الترخيص، لذلك لن يبدأ Hunyuan3D.\r\n\r\n" + ex.Message,
                    dialogTitle,
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
                return false;
            }
        }

        private static bool IsCurrentHunyuan21Acceptance(string path)
        {
            try
            {
                FileInfo file = new FileInfo(path);
                if (!file.Exists || file.Length <= 0 || file.Length > 16384)
                    return false;
                string[] acceptanceLines = File.ReadAllLines(path, Encoding.UTF8);
                HashSet<string> lines = new HashSet<string>(
                    acceptanceLines,
                    StringComparer.Ordinal);
                string[] acceptedUtcLines = Array.FindAll(
                    acceptanceLines,
                    delegate(string line)
                    {
                        return line.StartsWith("accepted_utc=", StringComparison.Ordinal);
                    });
                string acceptedUtc = acceptedUtcLines.Length == 1
                    ? acceptedUtcLines[0].Substring("accepted_utc=".Length)
                    : String.Empty;
                bool hasExplicitTimeZone =
                    acceptedUtc.EndsWith("Z", StringComparison.OrdinalIgnoreCase) ||
                    (acceptedUtc.Length >= 6 &&
                     (acceptedUtc[acceptedUtc.Length - 6] == '+' ||
                      acceptedUtc[acceptedUtc.Length - 6] == '-') &&
                     acceptedUtc[acceptedUtc.Length - 3] == ':' &&
                     Char.IsDigit(acceptedUtc[acceptedUtc.Length - 5]) &&
                     Char.IsDigit(acceptedUtc[acceptedUtc.Length - 4]) &&
                     Char.IsDigit(acceptedUtc[acceptedUtc.Length - 2]) &&
                     Char.IsDigit(acceptedUtc[acceptedUtc.Length - 1]));
                DateTimeOffset parsedAcceptedUtc;
                bool acceptedUtcValid = hasExplicitTimeZone && DateTimeOffset.TryParse(
                    acceptedUtc,
                    CultureInfo.InvariantCulture,
                    DateTimeStyles.None,
                    out parsedAcceptedUtc) &&
                    parsedAcceptedUtc.ToUniversalTime() <= DateTimeOffset.UtcNow.AddMinutes(5);
                return lines.Contains("MujassamAI Hunyuan acceptance v2.1") &&
                    lines.Contains("acceptance_version=" + Hunyuan21AcceptanceVersion) &&
                    lines.Contains("source_commit=" + Hunyuan21SourceCommit) &&
                    lines.Contains("license=Tencent Hunyuan 3D 2.1 Community License Agreement") &&
                    lines.Contains("license_sha256=" + Hunyuan21LicenseSha256) &&
                    lines.Contains("territory_confirmation=outside EU, UK, and South Korea") &&
                    lines.Contains("license_terms_acknowledged=true") &&
                    lines.Contains("acceptable_use_policy_acknowledged=true") &&
                    lines.Contains("provider_legal_name=" + Hunyuan21ProviderLegalName) &&
                    lines.Contains("provider_disclosure_acknowledged=true") &&
                    lines.Contains("tencent_non_affiliation_acknowledged=true") &&
                    acceptedUtcValid;
            }
            catch (Exception)
            {
                return false;
            }
        }

        private void BeginWorker(string pythonPath, string workerPath, string jobPath, string outputDirectory)
        {
            resultArtifactPath = null;
            reportedWorkerError = null;
            cancellationRequested = false;
            activeJobPath = jobPath;
            activeOutputDirectory = outputDirectory;
            progressBar.Value = 0;
            if (engineBox.SelectedIndex == 0)
                statusLabel.Text = "جارٍ تشغيل Hunyuan3D 2mini على وضع 8GB Low-VRAM...";
            else if (engineBox.SelectedIndex == 1)
                statusLabel.Text = "جارٍ تشغيل Hunyuan3D 2.1 Ultimate / PBR بأعلى جودة...";
            else
                statusLabel.Text = "جارٍ تشغيل SPAR3D الاحتياطي...";
            logBox.Clear();
            AppendLog("الصورة: " + imagePathBox.Text);
            AppendLog("الإخراج: " + outputDirectory);
            AppendLog("المحرك: " + engineBox.Text);
            AppendLog("الهدف: " + targetBox.Text);
            AppendLog("الإعدادات: " + hardwareBox.Text + " | " + textureBox.Text + " | " + geometryBox.Text);
            AppendLog("بدء محرك 3D المحلي المحدد...");

            ProcessStartInfo start = new ProcessStartInfo();
            start.FileName = pythonPath;
            start.Arguments = "-I -X utf8 " + QuoteWindowsArgument(workerPath) +
                              " --job " + QuoteWindowsArgument(jobPath);
            start.WorkingDirectory = AppDomain.CurrentDomain.BaseDirectory;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardOutput = true;
            start.RedirectStandardError = true;
            start.StandardOutputEncoding = new UTF8Encoding(false);
            start.StandardErrorEncoding = new UTF8Encoding(false);
            start.ErrorDialog = false;
            start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            start.EnvironmentVariables["PYTHONUTF8"] = "1";
            start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            start.EnvironmentVariables["MUJASSAM_PORTABLE_ROOT"] = AppDomain.CurrentDomain.BaseDirectory;
            start.EnvironmentVariables["MUJASSAM_JOB_PATH"] = jobPath;

            string runtimeDirectory = Path.GetDirectoryName(pythonPath);
            string torchLibraryDirectory = Path.Combine(runtimeDirectory, "Lib", "site-packages", "torch", "lib");
            string currentPath = start.EnvironmentVariables["PATH"] ?? String.Empty;
            start.EnvironmentVariables["PATH"] = runtimeDirectory + Path.PathSeparator +
                torchLibraryDirectory + Path.PathSeparator + currentPath;

            Process process = new Process();
            process.StartInfo = start;
            process.EnableRaisingEvents = true;
            process.OutputDataReceived += WorkerOutputReceived;
            process.ErrorDataReceived += WorkerErrorReceived;

            SetRunningState(true);
            if (!process.Start())
                throw new InvalidOperationException("رفض Windows تشغيل Python المضمّن.");

            WindowsProcessJob job = null;
            try
            {
                job = new WindowsProcessJob();
                job.Assign(process);
            }
            catch (Exception ex)
            {
                if (job != null)
                    job.Dispose();
                job = null;
                AppendLog("تحذير: تعذر إنشاء Job Object للإلغاء الآمن: " + ex.Message);
            }

            lock (processLock)
            {
                workerProcess = process;
                workerJob = job;
            }

            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            Task.Factory.StartNew(delegate
            {
                process.WaitForExit();
                return process.ExitCode;
            }, CancellationToken.None, TaskCreationOptions.LongRunning, TaskScheduler.Default)
            .ContinueWith(delegate(Task<int> completed)
            {
                int exitCode = completed.IsFaulted ? -1 : completed.Result;
                PostToUi(delegate { CompleteWorker(process, exitCode, completed.Exception); });
            }, TaskScheduler.Default);
        }

        private void WorkerOutputReceived(object sender, DataReceivedEventArgs e)
        {
            if (e.Data == null)
                return;
            try
            {
                HandleWorkerLine(e.Data);
            }
            catch (Exception ex)
            {
                lock (processLock)
                    reportedWorkerError = "سطر بروتوكول غير صالح: " + ex.Message;
                PostToUi(delegate { AppendLog("رفض سطرًا من المحرك: " + ex.Message); });
            }
        }

        private void WorkerErrorReceived(object sender, DataReceivedEventArgs e)
        {
            if (e.Data == null)
                return;
            PostToUi(delegate { AppendLog("[stderr] " + e.Data); });
        }

        private void HandleWorkerLine(string line)
        {
            if (line.StartsWith(ProgressPrefix, StringComparison.Ordinal))
            {
                string payload = line.Substring(ProgressPrefix.Length);
                string[] pieces = payload.Split(new char[] { '|' }, 2);
                int value;
                if (pieces.Length == 2 && Int32.TryParse(pieces[0], NumberStyles.Integer,
                    CultureInfo.InvariantCulture, out value))
                {
                    value = Math.Max(0, Math.Min(100, value));
                    string message = pieces[1].Trim();
                    PostToUi(delegate
                    {
                        progressBar.Value = value;
                        statusLabel.Text = String.IsNullOrWhiteSpace(message) ? "جارٍ العمل..." : message;
                        AppendLog(String.Format(CultureInfo.InvariantCulture, "[{0,3}%] {1}", value, message));
                    });
                }
                else
                {
                    PostToUi(delegate { AppendLog("سطر تقدم غير صالح: " + line); });
                }
                return;
            }

            if (line.StartsWith(ArtifactPrefix, StringComparison.Ordinal))
            {
                string emitted = line.Substring(ArtifactPrefix.Length).Trim();
                string candidate = ResolveArtifactPath(emitted);
                lock (processLock)
                    resultArtifactPath = candidate;
                PostToUi(delegate { AppendLog("النتيجة: " + candidate); });
                return;
            }

            if (line.StartsWith(ErrorPrefix, StringComparison.Ordinal))
            {
                string payload = line.Substring(ErrorPrefix.Length).Trim();
                int separator = payload.IndexOf('|');
                string message = separator >= 0 && separator + 1 < payload.Length
                    ? payload.Substring(separator + 1).Trim()
                    : payload;
                if (String.IsNullOrWhiteSpace(message))
                    message = "أبلغ المحرك عن خطأ غير موصوف.";
                lock (processLock)
                    reportedWorkerError = message;
                PostToUi(delegate
                {
                    statusLabel.Text = "فشل إنشاء المجسم";
                    AppendLog("خطأ: " + message);
                });
                return;
            }

            PostToUi(delegate { AppendLog(line); });
        }

        private string ResolveArtifactPath(string emitted)
        {
            if (String.IsNullOrWhiteSpace(emitted))
                throw new InvalidDataException("MJARTIFACT لا يحتوي مسارًا.");
            string outputRoot = activeOutputDirectory;
            string candidate = Path.IsPathRooted(emitted)
                ? Path.GetFullPath(emitted)
                : Path.GetFullPath(Path.Combine(outputRoot, emitted));
            if (!IsPathInside(outputRoot, candidate))
                throw new InvalidDataException("رفض مسار نتيجة خارج مجلد الإخراج: " + emitted);
            return candidate;
        }

        private static bool IsPathInside(string root, string candidate)
        {
            string normalizedRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
            string normalizedCandidate = Path.GetFullPath(candidate);
            return normalizedCandidate.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase);
        }

        private void CompleteWorker(Process completedProcess, int exitCode, AggregateException taskError)
        {
            string artifact;
            string workerError;
            bool cancelled;
            WindowsProcessJob completedJob = null;

            lock (processLock)
            {
                if (!Object.ReferenceEquals(workerProcess, completedProcess))
                    return;
                artifact = resultArtifactPath;
                workerError = reportedWorkerError;
                cancelled = cancellationRequested;
                completedJob = workerJob;
                workerProcess = null;
                workerJob = null;
            }

            if (completedJob != null)
                completedJob.Dispose();
            completedProcess.Dispose();
            DeleteActiveJobFile();

            if (formIsClosing || IsDisposed)
                return;

            SetRunningState(false);
            if (cancelled)
            {
                progressBar.Value = 0;
                statusLabel.Text = "أُلغي إنشاء المجسم";
                AppendLog("أُلغي الطلب بواسطة المستخدم.");
                return;
            }

            if (taskError != null && String.IsNullOrWhiteSpace(workerError))
                workerError = taskError.GetBaseException().Message;
            if (exitCode != 0 || !String.IsNullOrWhiteSpace(workerError))
            {
                string message = !String.IsNullOrWhiteSpace(workerError)
                    ? workerError
                    : "انتهى المحرك برمز " + exitCode.ToString(CultureInfo.InvariantCulture) + ". راجع السجل.";
                statusLabel.Text = "فشل إنشاء المجسم";
                AppendLog("فشل: " + message);
                MessageBox.Show(this, message, "تعذر إنشاء المجسم", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            if (String.IsNullOrWhiteSpace(artifact) || (!File.Exists(artifact) && !Directory.Exists(artifact)))
            {
                statusLabel.Text = "لم يُعثر على ملف النتيجة";
                AppendLog("انتهى المحرك بنجاح لكنه لم يرسل MJARTIFACT صالحًا.");
                MessageBox.Show(this, "انتهى المحرك دون ملف نتيجة صالح. راجع السجل.", Text,
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            resultArtifactPath = artifact;
            progressBar.Value = 100;
            statusLabel.Text = "اكتمل إنشاء المجسم";
            openResultButton.Enabled = true;
            AppendLog("اكتمل بنجاح.");
            System.Media.SystemSounds.Asterisk.Play();
        }

        private void CancelGeneration(object sender, EventArgs e)
        {
            Process process;
            WindowsProcessJob job;
            lock (processLock)
            {
                process = workerProcess;
                job = workerJob;
                if (process == null)
                    return;
                cancellationRequested = true;
            }

            cancelButton.Enabled = false;
            statusLabel.Text = "جارٍ الإلغاء...";
            AppendLog("طلب المستخدم إلغاء العملية...");
            try
            {
                if (job != null)
                {
                    job.Terminate(1223);
                }
                else if (!process.HasExited)
                {
                    process.Kill();
                }
            }
            catch (Exception ex)
            {
                AppendLog("تعذر إيقاف المحرك فورًا: " + ex.Message);
            }
        }

        private void OpenResult(object sender, EventArgs e)
        {
            string artifact = resultArtifactPath;
            if (String.IsNullOrWhiteSpace(artifact))
                return;
            try
            {
                if (Directory.Exists(artifact))
                {
                    Process.Start(new ProcessStartInfo(artifact) { UseShellExecute = true });
                    return;
                }
                if (!File.Exists(artifact))
                    throw new FileNotFoundException("ملف النتيجة لم يعد موجودًا.", artifact);
                try
                {
                    Process.Start(new ProcessStartInfo(artifact) { UseShellExecute = true });
                }
                catch (Win32Exception)
                {
                    Process.Start(new ProcessStartInfo
                    {
                        FileName = "explorer.exe",
                        Arguments = "/select," + QuoteWindowsArgument(artifact),
                        UseShellExecute = true
                    });
                }
            }
            catch (Exception ex)
            {
                MessageBox.Show(this, "تعذر فتح النتيجة.\r\n\r\n" + ex.Message, Text,
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void SetRunningState(bool running)
        {
            imagePathBox.Enabled = !running;
            outputPathBox.Enabled = !running;
            browseImageButton.Enabled = !running;
            browseOutputButton.Enabled = !running;
            engineBox.Enabled = !running;
            targetBox.Enabled = !running;
            textureBox.Enabled = !running;
            hardwareBox.Enabled = !running;
            geometryBox.Enabled = !running;
            generateButton.Enabled = !running;
            cancelButton.Enabled = running;
            openResultButton.Enabled = !running && !String.IsNullOrWhiteSpace(resultArtifactPath) &&
                (File.Exists(resultArtifactPath) || Directory.Exists(resultArtifactPath));
        }

        private void ShowInputError(string message)
        {
            MessageBox.Show(this, message, "تحقق من الإعدادات", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }

        private void AppendLog(string message)
        {
            if (String.IsNullOrEmpty(message))
                return;
            if (logBox.TextLength > MaximumLogCharacters)
            {
                int keep = MaximumLogCharacters / 2;
                logBox.Text = "[تم اختصار السجل القديم]\r\n" +
                    logBox.Text.Substring(Math.Max(0, logBox.TextLength - keep));
                logBox.SelectionStart = logBox.TextLength;
            }
            string stamp = DateTime.Now.ToString("HH:mm:ss", CultureInfo.InvariantCulture);
            logBox.AppendText("[" + stamp + "] " + message + Environment.NewLine);
        }

        private void PostToUi(Action action)
        {
            if (action == null || IsDisposed || Disposing)
                return;
            try
            {
                if (InvokeRequired)
                    BeginInvoke(action);
                else
                    action();
            }
            catch (ObjectDisposedException)
            {
            }
            catch (InvalidOperationException)
            {
                // The window closed while a final worker line was being delivered.
            }
        }

        private void DeleteActiveJobFile()
        {
            string path = activeJobPath;
            activeJobPath = null;
            if (String.IsNullOrWhiteSpace(path))
                return;
            try
            {
                if (File.Exists(path))
                    File.Delete(path);
            }
            catch
            {
                // A stale JSON file contains paths and settings only; it has no credential.
            }
        }

        private void OnMainFormClosing(object sender, FormClosingEventArgs e)
        {
            if (workerProcess == null)
                return;
            DialogResult answer = MessageBox.Show(this,
                "إنشاء المجسم ما زال قيد التشغيل. هل تريد إلغاءه وإغلاق البرنامج؟",
                Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question,
                MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes)
            {
                e.Cancel = true;
                return;
            }
            formIsClosing = true;
            CancelGeneration(this, EventArgs.Empty);
        }

        private void OnMainFormClosed(object sender, FormClosedEventArgs e)
        {
            Process process;
            WindowsProcessJob job;
            lock (processLock)
            {
                process = workerProcess;
                job = workerJob;
                workerProcess = null;
                workerJob = null;
            }
            try
            {
                if (job != null)
                    job.Dispose();
                else if (process != null && !process.HasExited)
                    process.Kill();
            }
            catch
            {
            }
            if (process != null)
                process.Dispose();
            DeleteActiveJobFile();
            if (imagePreview.Image != null)
            {
                imagePreview.Image.Dispose();
                imagePreview.Image = null;
            }
        }

        internal static string QuoteWindowsArgument(string value)
        {
            if (value == null)
                return "\"\"";
            if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '\n', '\v', '"' }) < 0)
                return value;

            StringBuilder quoted = new StringBuilder();
            quoted.Append('"');
            int backslashes = 0;
            foreach (char current in value)
            {
                if (current == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (current == '"')
                {
                    quoted.Append('\\', backslashes * 2 + 1);
                    quoted.Append('"');
                    backslashes = 0;
                    continue;
                }
                if (backslashes > 0)
                {
                    quoted.Append('\\', backslashes);
                    backslashes = 0;
                }
                quoted.Append(current);
            }
            if (backslashes > 0)
                quoted.Append('\\', backslashes * 2);
            quoted.Append('"');
            return quoted.ToString();
        }

        internal static string ValidatePortableLayout()
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            string python = Path.Combine(root, "rt", "python.exe");
            string worker = Path.Combine(root, "app", "worker.py");
            string quality = Path.Combine(root, "app", "quality", "realesrgan_x2.py");
            string aiModel = Path.Combine(root, "models", "realesrgan", "RealESRGAN_x2plus.pth");
            List<string> missing = new List<string>();
            if (!File.Exists(python))
                missing.Add(python);
            if (!File.Exists(worker))
                missing.Add(worker);
            if (!File.Exists(quality))
                missing.Add(quality);
            if (!File.Exists(aiModel))
                missing.Add(aiModel);
            return missing.Count == 0 ? String.Empty : String.Join(Environment.NewLine, missing.ToArray());
        }

        private sealed class WindowsProcessJob : IDisposable
        {
            private const uint KillOnJobClose = 0x00002000;
            private IntPtr handle;

            internal WindowsProcessJob()
            {
                handle = CreateJobObject(IntPtr.Zero, null);
                if (handle == IntPtr.Zero)
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "تعذر إنشاء Windows Job Object.");
                try
                {
                    ExtendedLimitInformation info = new ExtendedLimitInformation();
                    info.BasicLimitInformation.LimitFlags = KillOnJobClose;
                    int size = Marshal.SizeOf(typeof(ExtendedLimitInformation));
                    IntPtr memory = Marshal.AllocHGlobal(size);
                    try
                    {
                        Marshal.StructureToPtr(info, memory, false);
                        if (!SetInformationJobObject(handle, 9, memory, (uint)size))
                            throw new Win32Exception(Marshal.GetLastWin32Error(), "تعذر إعداد Windows Job Object.");
                    }
                    finally
                    {
                        Marshal.FreeHGlobal(memory);
                    }
                }
                catch
                {
                    CloseHandle(handle);
                    handle = IntPtr.Zero;
                    throw;
                }
            }

            internal void Assign(Process process)
            {
                if (handle == IntPtr.Zero)
                    throw new ObjectDisposedException("WindowsProcessJob");
                if (!AssignProcessToJobObject(handle, process.Handle))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "تعذر ربط المحرك بـWindows Job Object.");
            }

            internal void Terminate(uint exitCode)
            {
                if (handle != IntPtr.Zero && !TerminateJobObject(handle, exitCode))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "تعذر إنهاء عمليات المحرك.");
            }

            public void Dispose()
            {
                if (handle != IntPtr.Zero)
                {
                    CloseHandle(handle);
                    handle = IntPtr.Zero;
                }
                GC.SuppressFinalize(this);
            }

            ~WindowsProcessJob()
            {
                Dispose();
            }

            [StructLayout(LayoutKind.Sequential)]
            private struct BasicLimitInformation
            {
                public long PerProcessUserTimeLimit;
                public long PerJobUserTimeLimit;
                public uint LimitFlags;
                public UIntPtr MinimumWorkingSetSize;
                public UIntPtr MaximumWorkingSetSize;
                public uint ActiveProcessLimit;
                public UIntPtr Affinity;
                public uint PriorityClass;
                public uint SchedulingClass;
            }

            [StructLayout(LayoutKind.Sequential)]
            private struct IoCounters
            {
                public ulong ReadOperationCount;
                public ulong WriteOperationCount;
                public ulong OtherOperationCount;
                public ulong ReadTransferCount;
                public ulong WriteTransferCount;
                public ulong OtherTransferCount;
            }

            [StructLayout(LayoutKind.Sequential)]
            private struct ExtendedLimitInformation
            {
                public BasicLimitInformation BasicLimitInformation;
                public IoCounters IoInfo;
                public UIntPtr ProcessMemoryLimit;
                public UIntPtr JobMemoryLimit;
                public UIntPtr PeakProcessMemoryUsed;
                public UIntPtr PeakJobMemoryUsed;
            }

            [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
            private static extern IntPtr CreateJobObject(IntPtr securityAttributes, string name);

            [DllImport("kernel32.dll", SetLastError = true)]
            [return: MarshalAs(UnmanagedType.Bool)]
            private static extern bool SetInformationJobObject(IntPtr job, int informationClass,
                IntPtr information, uint informationLength);

            [DllImport("kernel32.dll", SetLastError = true)]
            [return: MarshalAs(UnmanagedType.Bool)]
            private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

            [DllImport("kernel32.dll", SetLastError = true)]
            [return: MarshalAs(UnmanagedType.Bool)]
            private static extern bool TerminateJobObject(IntPtr job, uint exitCode);

            [DllImport("kernel32.dll")]
            [return: MarshalAs(UnmanagedType.Bool)]
            private static extern bool CloseHandle(IntPtr handle);
        }
    }
}
