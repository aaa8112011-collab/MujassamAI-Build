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

        private readonly TextBox imagePathBox;
        private readonly TextBox outputPathBox;
        private readonly PictureBox imagePreview;
        private readonly ComboBox targetBox;
        private readonly ComboBox textureBox;
        private readonly ComboBox robloxQualityBox;
        private readonly Label robloxQualityLabel;
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
            targetBox = CreateDropDown();
            textureBox = CreateDropDown();
            robloxQualityBox = CreateDropDown();
            robloxQualityLabel = CreateFieldLabel("جودة Roblox");
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
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 122));
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
                ColumnCount = 6,
                RowCount = 2
            };
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 94));
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33.33F));
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 108));
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33.33F));
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 112));
            options.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 33.34F));
            options.RowStyles.Add(new RowStyle(SizeType.Absolute, 50));
            options.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            options.Controls.Add(CreateFieldLabel("الهدف"), 0, 0);
            options.Controls.Add(targetBox, 1, 0);
            options.Controls.Add(CreateFieldLabel("دقة الخامة"), 2, 0);
            options.Controls.Add(textureBox, 3, 0);
            options.Controls.Add(robloxQualityLabel, 4, 0);
            options.Controls.Add(robloxQualityBox, 5, 0);
            Label optionsHint = new Label
            {
                Text = "1024 أخف وأسرع. استخدم 2048 عند توفر VRAM كافية. جودة Roblox تتحكم في كثافة المجسم النهائي.",
                Dock = DockStyle.Fill,
                ForeColor = Color.FromArgb(91, 101, 116),
                TextAlign = ContentAlignment.MiddleRight,
                AutoEllipsis = true,
                Padding = new Padding(4, 3, 4, 0)
            };
            options.SetColumnSpan(optionsHint, 5);
            options.Controls.Add(optionsHint, 1, 1);
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
            targetBox.Items.Add("Roblox Studio");
            targetBox.Items.Add("Unreal Engine");
            targetBox.SelectedIndex = 0;

            textureBox.Items.Add("1024 × 1024 — موصى به");
            textureBox.Items.Add("2048 × 2048 — تفاصيل أعلى");
            textureBox.SelectedIndex = 0;

            robloxQualityBox.Items.Add("متوازن — أداء أفضل");
            robloxQualityBox.Items.Add("عالي — تفاصيل أكثر");
            robloxQualityBox.SelectedIndex = 0;
        }

        private void WireEvents()
        {
            browseImageButton.Click += BrowseImage;
            browseOutputButton.Click += BrowseOutput;
            generateButton.Click += StartGeneration;
            cancelButton.Click += CancelGeneration;
            openResultButton.Click += OpenResult;
            targetBox.SelectedIndexChanged += delegate { UpdateTargetControls(); };
            imagePathBox.TextChanged += delegate { TryLoadPreview(imagePathBox.Text); };
            FormClosing += OnMainFormClosing;
            FormClosed += OnMainFormClosed;
        }

        private void SetDefaultOutputFolder()
        {
            string documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            if (String.IsNullOrWhiteSpace(documents))
                documents = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            outputPathBox.Text = Path.Combine(documents, "Mujassam AI", "Exports");
        }

        private void UpdateTargetControls()
        {
            bool roblox = targetBox.SelectedIndex == 0;
            robloxQualityBox.Enabled = roblox && workerProcess == null;
            robloxQualityLabel.Enabled = roblox;
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
            string target = targetBox.SelectedIndex == 0 ? "roblox" : "unreal";
            int textureResolution = textureBox.SelectedIndex == 1 ? 2048 : 1024;
            string robloxQuality = robloxQualityBox.SelectedIndex == 1 ? "high" : "balanced";
            Dictionary<string, object> job = new Dictionary<string, object>();
            job["schema_version"] = 1;
            job["image_path"] = imagePath;
            job["output_dir"] = outputDirectory;
            job["target"] = target;
            job["texture_resolution"] = textureResolution;
            job["roblox_quality"] = robloxQuality;

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

        private void BeginWorker(string pythonPath, string workerPath, string jobPath, string outputDirectory)
        {
            resultArtifactPath = null;
            reportedWorkerError = null;
            cancellationRequested = false;
            activeJobPath = jobPath;
            activeOutputDirectory = outputDirectory;
            progressBar.Value = 0;
            statusLabel.Text = "جارٍ تشغيل المحرك...";
            logBox.Clear();
            AppendLog("الصورة: " + imagePathBox.Text);
            AppendLog("الإخراج: " + outputDirectory);
            AppendLog("بدء محرك 3D المحلي...");

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
            targetBox.Enabled = !running;
            textureBox.Enabled = !running;
            generateButton.Enabled = !running;
            cancelButton.Enabled = running;
            openResultButton.Enabled = !running && !String.IsNullOrWhiteSpace(resultArtifactPath) &&
                (File.Exists(resultArtifactPath) || Directory.Exists(resultArtifactPath));
            robloxQualityBox.Enabled = !running && targetBox.SelectedIndex == 0;
            robloxQualityLabel.Enabled = targetBox.SelectedIndex == 0;
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
            List<string> missing = new List<string>();
            if (!File.Exists(python))
                missing.Add(python);
            if (!File.Exists(worker))
                missing.Add(worker);
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
