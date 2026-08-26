using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace MujassamInstaller
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;

            if (args != null && args.Length > 0 &&
                String.Equals(args[0], "--self-test", StringComparison.OrdinalIgnoreCase))
            {
                return RunSelfTest(args);
            }

            bool createdNew;
            Mutex singleInstance = new Mutex(true, "Local\\MujassamAI-Installer-v1", out createdNew);
            if (!createdNew)
            {
                singleInstance.Dispose();
                MessageBox.Show("المثبّت يعمل بالفعل في نافذة أخرى.", "تثبيت Mujassam AI",
                    MessageBoxButtons.OK, MessageBoxIcon.Information);
                return 5;
            }

            try
            {
                Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
                Application.ThreadException += delegate(object sender, ThreadExceptionEventArgs eventArgs)
                {
                    ShowFatalError(eventArgs.Exception);
                };
                AppDomain.CurrentDomain.UnhandledException += delegate(object sender, UnhandledExceptionEventArgs eventArgs)
                {
                    ShowFatalError(eventArgs.ExceptionObject as Exception ??
                        new Exception("Unhandled installer error."));
                };
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new SetupForm());
                return 0;
            }
            finally
            {
                try
                {
                    singleInstance.ReleaseMutex();
                }
                catch
                {
                }
                singleInstance.Dispose();
            }
        }

        private static int RunSelfTest(string[] args)
        {
            string reportPath = args.Length > 1
                ? Path.GetFullPath(args[1])
                : Path.Combine(Path.GetTempPath(), "MujassamInstaller-self-test.txt");
            try
            {
                Uri releaseBase = InstallerConfiguration.GetReleaseBaseUri();
                string manifestSha256 = InstallerConfiguration.GetManifestSha256();
                string installRoot = InstallerEngine.GetInstallRoot();
                StringBuilder report = new StringBuilder();
                report.AppendLine("Mujassam AI installer self-test");
                report.AppendLine("ReleaseBase=" + releaseBase.AbsoluteUri);
                report.AppendLine("ManifestSHA256=" + manifestSha256);
                report.AppendLine("InstallRoot=" + installRoot);
                report.AppendLine("Is64BitOS=" + Environment.Is64BitOperatingSystem);
                report.AppendLine("Framework=" + Environment.Version);
                EnsureParentDirectory(reportPath);
                File.WriteAllText(reportPath, report.ToString(), new UTF8Encoding(false));
                return Environment.Is64BitOperatingSystem ? 0 : 10;
            }
            catch (Exception error)
            {
                try
                {
                    EnsureParentDirectory(reportPath);
                    File.WriteAllText(reportPath, error.ToString(), new UTF8Encoding(false));
                }
                catch
                {
                }
                return 20;
            }
        }

        private static void EnsureParentDirectory(string path)
        {
            string parent = Path.GetDirectoryName(path);
            if (!String.IsNullOrWhiteSpace(parent))
                Directory.CreateDirectory(parent);
        }

        private static void ShowFatalError(Exception error)
        {
            string logPath = Path.Combine(Path.GetTempPath(), "MujassamInstaller-error.log");
            try
            {
                File.WriteAllText(logPath, error.ToString(), new UTF8Encoding(false));
            }
            catch
            {
            }

            try
            {
                MessageBox.Show(
                    "حدث خطأ غير متوقع في المثبّت.\r\n\r\nتم حفظ التفاصيل في:\r\n" + logPath,
                    "تثبيت Mujassam AI",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
            catch
            {
            }
        }
    }

    internal static class InstallerConfiguration
    {
        // build.ps1 replaces these markers in a temporary copy before invoking csc.
        // RELEASE_BASE_URL wins when supplied; otherwise repository + tag are used.
        internal const string CompiledReleaseBaseUrl = "@@RELEASE_BASE_URL@@";
        internal const string CompiledRepository = "@@GITHUB_REPOSITORY@@";
        internal const string CompiledReleaseTag = "@@RELEASE_TAG@@";
        internal const string CompiledManifestSha256 = "@@MANIFEST_SHA256@@";
        internal const string ManifestAssetName = "release-manifest.json";

        private static readonly Regex RepositoryPattern = new Regex(
            "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
            RegexOptions.CultureInvariant);

        internal static Uri GetReleaseBaseUri()
        {
            string configuredBase = NormalizeInjectedValue(CompiledReleaseBaseUrl);
            Uri result;
            if (!String.IsNullOrEmpty(configuredBase))
            {
                if (!configuredBase.EndsWith("/", StringComparison.Ordinal))
                    configuredBase += "/";
                if (!Uri.TryCreate(configuredBase, UriKind.Absolute, out result))
                    throw new InstallerException("عنوان إصدار GitHub المحقون غير صالح.");
            }
            else
            {
                string repository = NormalizeInjectedValue(CompiledRepository);
                string tag = NormalizeInjectedValue(CompiledReleaseTag);
                if (!RepositoryPattern.IsMatch(repository) || String.IsNullOrWhiteSpace(tag))
                {
                    throw new InstallerException(
                        "هذا المثبّت غير مهيأ بإصدار ثابت. أعد بناء Setup.exe بعد حقن repository وrelease tag.");
                }
                if (tag.IndexOfAny(new char[] { '\r', '\n', '\0' }) >= 0)
                    throw new InstallerException("وسم الإصدار المحقون غير صالح.");
                string address = "https://github.com/" + repository + "/releases/download/" +
                    Uri.EscapeDataString(tag) + "/";
                result = new Uri(address, UriKind.Absolute);
            }

            ValidateReleaseBase(result);
            return result;
        }

        internal static string GetManifestSha256()
        {
            string value = NormalizeInjectedValue(CompiledManifestSha256);
            if (value.Length != 64)
                throw new InstallerException("لم تُحقن بصمة release-manifest.json في المثبّت.");
            for (int index = 0; index < value.Length; index++)
            {
                char current = value[index];
                bool hexadecimal = (current >= '0' && current <= '9') ||
                    (current >= 'a' && current <= 'f') || (current >= 'A' && current <= 'F');
                if (!hexadecimal)
                    throw new InstallerException("بصمة release-manifest.json المحقونة غير صالحة.");
            }
            return value.ToLowerInvariant();
        }

        private static string NormalizeInjectedValue(string value)
        {
            if (String.IsNullOrWhiteSpace(value) || value.IndexOf("@@", StringComparison.Ordinal) >= 0)
                return String.Empty;
            return value.Trim();
        }

        private static void ValidateReleaseBase(Uri value)
        {
            if (value == null || !value.IsAbsoluteUri ||
                !String.Equals(value.Scheme, Uri.UriSchemeHttps, StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(value.Host, "github.com", StringComparison.OrdinalIgnoreCase) ||
                !value.IsDefaultPort || !String.IsNullOrEmpty(value.UserInfo) ||
                !String.IsNullOrEmpty(value.Query) || !String.IsNullOrEmpty(value.Fragment) ||
                value.AbsolutePath.IndexOf("/releases/download/", StringComparison.OrdinalIgnoreCase) < 0)
            {
                throw new InstallerException("يجب أن يشير عنوان الإصدار إلى GitHub Releases عبر HTTPS.");
            }
        }
    }

    internal sealed class SetupForm : Form
    {
        private readonly ProgressBar progressBar;
        private readonly Label statusLabel;
        private readonly Label destinationLabel;
        private readonly TextBox logBox;
        private readonly Button installButton;
        private readonly Button cancelButton;
        private readonly Button openFolderButton;
        private CancellationTokenSource cancellation;
        private bool isRunning;
        private bool closeAfterCancellation;
        private string installedPath;

        internal SetupForm()
        {
            Text = "تثبيت Mujassam AI";
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = true;
            ClientSize = new Size(720, 500);
            Font = new Font("Segoe UI", 9.5F, FontStyle.Regular, GraphicsUnit.Point);
            BackColor = Color.FromArgb(246, 248, 251);
            RightToLeft = RightToLeft.Yes;
            RightToLeftLayout = true;

            progressBar = new ProgressBar();
            statusLabel = new Label();
            destinationLabel = new Label();
            logBox = new TextBox();
            installButton = CreateButton("تنزيل وتثبيت", true);
            cancelButton = CreateButton("إلغاء", false);
            openFolderButton = CreateButton("فتح مجلد التثبيت", false);

            BuildInterface();
            WireEvents();
            UpdateIdleState();
        }

        private static Button CreateButton(string text, bool primary)
        {
            return new Button
            {
                Text = text,
                AutoSize = true,
                MinimumSize = new Size(primary ? 155 : 125, 38),
                Font = new Font("Segoe UI Semibold", 9.5F, primary ? FontStyle.Bold : FontStyle.Regular),
                FlatStyle = FlatStyle.System,
                Margin = new Padding(6)
            };
        }

        private void BuildInterface()
        {
            TableLayoutPanel root = new TableLayoutPanel
            {
                Dock = DockStyle.Fill,
                ColumnCount = 1,
                RowCount = 7,
                Padding = new Padding(22, 18, 22, 18),
                BackColor = BackColor
            };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 58));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 62));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 48));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 40));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 54));
            Controls.Add(root);

            Label title = new Label
            {
                Text = "Mujassam AI",
                Dock = DockStyle.Fill,
                Font = new Font("Segoe UI Semibold", 20F, FontStyle.Bold),
                ForeColor = Color.FromArgb(29, 42, 61),
                TextAlign = ContentAlignment.MiddleRight
            };
            root.Controls.Add(title, 0, 0);

            Label introduction = new Label
            {
                Text = "ينزّل هذا المثبّت الحزمة المحمولة والنموذج من إصدار GitHub المثبّت، " +
                       "ويتحقق من سلامة كل ملف قبل التشغيل. لا يحتاج صلاحية مسؤول أو تثبيت Python.",
                Dock = DockStyle.Fill,
                ForeColor = Color.FromArgb(73, 84, 101),
                TextAlign = ContentAlignment.MiddleRight,
                AutoSize = false
            };
            root.Controls.Add(introduction, 0, 1);

            Panel destinationCard = new Panel
            {
                Dock = DockStyle.Fill,
                BackColor = Color.White,
                BorderStyle = BorderStyle.FixedSingle,
                Padding = new Padding(12, 6, 12, 6)
            };
            destinationLabel.Dock = DockStyle.Fill;
            destinationLabel.TextAlign = ContentAlignment.MiddleRight;
            destinationLabel.AutoEllipsis = true;
            destinationLabel.RightToLeft = RightToLeft.No;
            destinationCard.Controls.Add(destinationLabel);
            root.Controls.Add(destinationCard, 0, 2);

            Panel statusPanel = new Panel { Dock = DockStyle.Fill, Padding = new Padding(0, 5, 0, 3) };
            statusLabel.Dock = DockStyle.Top;
            statusLabel.Height = 24;
            statusLabel.TextAlign = ContentAlignment.MiddleRight;
            statusLabel.AutoEllipsis = true;
            statusLabel.Font = new Font("Segoe UI Semibold", 9.5F, FontStyle.Bold);
            progressBar.Dock = DockStyle.Bottom;
            progressBar.Height = 18;
            progressBar.Minimum = 0;
            progressBar.Maximum = 100;
            statusPanel.Controls.Add(statusLabel);
            statusPanel.Controls.Add(progressBar);
            root.Controls.Add(statusPanel, 0, 3);

            Label note = new Label
            {
                Text = "يمكن إعادة المحاولة بعد انقطاع الإنترنت؛ يحتفظ المثبّت بالأجزاء المكتملة مؤقتًا.",
                Dock = DockStyle.Fill,
                TextAlign = ContentAlignment.MiddleRight,
                ForeColor = Color.FromArgb(91, 101, 116)
            };
            root.Controls.Add(note, 0, 4);

            logBox.Dock = DockStyle.Fill;
            logBox.Multiline = true;
            logBox.ReadOnly = true;
            logBox.ScrollBars = ScrollBars.Vertical;
            logBox.WordWrap = true;
            logBox.BackColor = Color.White;
            logBox.BorderStyle = BorderStyle.FixedSingle;
            logBox.RightToLeft = RightToLeft.Yes;
            root.Controls.Add(logBox, 0, 5);

            FlowLayoutPanel actions = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill,
                FlowDirection = FlowDirection.RightToLeft,
                WrapContents = false,
                Padding = new Padding(0, 4, 0, 0)
            };
            actions.Controls.Add(installButton);
            actions.Controls.Add(cancelButton);
            actions.Controls.Add(openFolderButton);
            root.Controls.Add(actions, 0, 6);
        }

        private void WireEvents()
        {
            installButton.Click += BeginInstallation;
            cancelButton.Click += CancelInstallation;
            openFolderButton.Click += OpenInstallFolder;
            FormClosing += OnSetupFormClosing;
            FormClosed += delegate
            {
                if (cancellation != null)
                    cancellation.Dispose();
            };
        }

        private void UpdateIdleState()
        {
            try
            {
                destinationLabel.Text = InstallerEngine.GetInstallRoot();
                statusLabel.Text = "جاهز للتثبيت";
                installButton.Enabled = true;
            }
            catch (Exception error)
            {
                destinationLabel.Text = "تعذر تحديد مجلد المستندات";
                statusLabel.Text = error.Message;
                installButton.Enabled = false;
            }
            cancelButton.Enabled = false;
            openFolderButton.Enabled = !String.IsNullOrWhiteSpace(installedPath) &&
                Directory.Exists(installedPath);
        }

        private async void BeginInstallation(object sender, EventArgs eventArgs)
        {
            if (isRunning)
                return;

            isRunning = true;
            closeAfterCancellation = false;
            installedPath = null;
            progressBar.Value = 0;
            logBox.Clear();
            installButton.Enabled = false;
            cancelButton.Enabled = true;
            openFolderButton.Enabled = false;
            statusLabel.Text = "جارٍ بدء التثبيت...";
            cancellation = new CancellationTokenSource();
            CancellationToken token = cancellation.Token;
            Progress<InstallProgress> progress = new Progress<InstallProgress>(ApplyProgress);

            try
            {
                InstallResult result = await InstallerEngine.InstallAsync(progress, token);
                installedPath = result.InstallRoot;
                progressBar.Value = 100;
                statusLabel.Text = result.LaunchSucceeded
                    ? "اكتمل التثبيت وتم تشغيل Mujassam AI"
                    : token.IsCancellationRequested
                        ? "اكتمل التثبيت؛ أُلغي التشغيل التلقائي"
                        : "اكتمل التثبيت؛ تعذر التشغيل التلقائي";
                AppendLog("اكتمل التثبيت في: " + installedPath);
                if (!String.IsNullOrWhiteSpace(result.Warning))
                    AppendLog("تنبيه: " + result.Warning);
                System.Media.SystemSounds.Asterisk.Play();
                if (!result.LaunchSucceeded && !token.IsCancellationRequested)
                {
                    MessageBox.Show(this,
                        "اكتمل التثبيت، لكن تعذر تشغيل البرنامج تلقائيًا. يمكنك فتح مجلد التثبيت وتشغيل MujassamAI.exe.",
                        Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                }
            }
            catch (OperationCanceledException)
            {
                statusLabel.Text = "أُلغي التثبيت — يمكنك إعادة المحاولة";
                AppendLog("أُلغي التثبيت. ستُستخدم الأجزاء السليمة عند إعادة المحاولة.");
            }
            catch (Exception error)
            {
                statusLabel.Text = "تعذر إكمال التثبيت";
                AppendLog("خطأ: " + error.Message);
                MessageBox.Show(this,
                    "تعذر إكمال التثبيت. لم يُشغّل أي ملف غير متحقق منه.\r\n\r\n" + error.Message,
                    Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                isRunning = false;
                if (cancellation != null)
                {
                    cancellation.Dispose();
                    cancellation = null;
                }
                cancelButton.Enabled = false;
                installButton.Text = "إعادة المحاولة";
                installButton.Enabled = true;
                openFolderButton.Enabled = !String.IsNullOrWhiteSpace(installedPath) &&
                    Directory.Exists(installedPath);
                if (closeAfterCancellation && !IsDisposed)
                    BeginInvoke(new Action(Close));
            }
        }

        private void ApplyProgress(InstallProgress value)
        {
            if (value == null || IsDisposed)
                return;
            progressBar.Value = Math.Max(0, Math.Min(100, value.Percent));
            if (!String.IsNullOrWhiteSpace(value.Status))
                statusLabel.Text = value.Status;
            if (!String.IsNullOrWhiteSpace(value.Detail))
                AppendLog(value.Detail);
        }

        private void AppendLog(string value)
        {
            if (String.IsNullOrWhiteSpace(value))
                return;
            string stamp = DateTime.Now.ToString("HH:mm:ss", CultureInfo.InvariantCulture);
            logBox.AppendText("[" + stamp + "] " + value + Environment.NewLine);
            logBox.SelectionStart = logBox.TextLength;
            logBox.ScrollToCaret();
        }

        private void CancelInstallation(object sender, EventArgs eventArgs)
        {
            if (!isRunning || cancellation == null)
                return;
            cancelButton.Enabled = false;
            statusLabel.Text = "جارٍ الإلغاء...";
            AppendLog("طُلب إلغاء العملية.");
            cancellation.Cancel();
        }

        private void OpenInstallFolder(object sender, EventArgs eventArgs)
        {
            if (String.IsNullOrWhiteSpace(installedPath) || !Directory.Exists(installedPath))
                return;
            try
            {
                Process.Start(new ProcessStartInfo(installedPath) { UseShellExecute = true });
            }
            catch (Exception error)
            {
                MessageBox.Show(this, "تعذر فتح المجلد.\r\n\r\n" + error.Message,
                    Text, MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
        }

        private void OnSetupFormClosing(object sender, FormClosingEventArgs eventArgs)
        {
            if (!isRunning)
                return;
            DialogResult answer = MessageBox.Show(this,
                "التنزيل أو التثبيت ما زال جاريًا. هل تريد إلغاءه ثم إغلاق المثبّت؟",
                Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question,
                MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes)
            {
                eventArgs.Cancel = true;
                return;
            }
            eventArgs.Cancel = true;
            closeAfterCancellation = true;
            CancelInstallation(this, EventArgs.Empty);
        }
    }

    internal static class InstallerEngine
    {
        private const int ManifestMaximumBytes = 1024 * 1024;
        private const int BufferSize = 1024 * 1024;
        private const int DownloadAttempts = 3;
        private const int NetworkTimeoutSeconds = 120;
        private const int MaximumDirectoryPathCharacters = 247;
        private const int MaximumFilePathCharacters = 259;
        private const long MaximumPartBytes = 1900000000L;
        private const long MaximumArtifactBytes = 80L * 1024L * 1024L * 1024L;
        private const long MaximumExtractedBytes = 80L * 1024L * 1024L * 1024L;
        private const int MaximumPartsPerArtifact = 128;
        private const int MaximumZipEntries = 250000;
        private static readonly Regex Sha256Pattern = new Regex(
            "^[0-9a-fA-F]{64}$", RegexOptions.CultureInvariant);
        private static readonly Regex AssetNamePattern = new Regex(
            "^[A-Za-z0-9][A-Za-z0-9._-]{0,179}$", RegexOptions.CultureInvariant);

        internal static string GetInstallRoot()
        {
            string documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            if (String.IsNullOrWhiteSpace(documents))
                throw new InstallerException("تعذر تحديد مجلد Documents لهذا المستخدم.");
            string fullDocuments = Path.GetFullPath(documents);
            string root = Path.GetPathRoot(fullDocuments);
            if (String.Equals(fullDocuments.TrimEnd(Path.DirectorySeparatorChar),
                    root == null ? String.Empty : root.TrimEnd(Path.DirectorySeparatorChar),
                    StringComparison.OrdinalIgnoreCase))
            {
                throw new InstallerException("رفض المثبّت استخدام جذر القرص كمجلد Documents.");
            }
            return Path.Combine(fullDocuments, "MujassamAI-Portable");
        }

        internal static async Task<InstallResult> InstallAsync(
            IProgress<InstallProgress> progress,
            CancellationToken cancellationToken)
        {
            Uri releaseBase = InstallerConfiguration.GetReleaseBaseUri();
            string expectedManifestSha256 = InstallerConfiguration.GetManifestSha256();
            string installRoot = GetInstallRoot();
            string cacheRoot = GetCacheRoot(releaseBase, expectedManifestSha256);
            Directory.CreateDirectory(cacheRoot);

            Report(progress, 1, "جارٍ تنزيل معلومات الإصدار...", releaseBase.AbsoluteUri);
            Uri manifestUri = new Uri(releaseBase, InstallerConfiguration.ManifestAssetName);
            string manifestJson = await DownloadManifestAsync(manifestUri,
                expectedManifestSha256, cancellationToken)
                .ConfigureAwait(false);
            ReleaseManifest manifest = ReleaseManifest.ParseAndValidate(manifestJson);
            cancellationToken.ThrowIfCancellationRequested();
            Report(progress, 3, "تم التحقق من بنية الإصدار", "الإصدار يحتوي على جميع الأجزاء المطلوبة.");

            string runtimeArchive = Path.Combine(cacheRoot, manifest.Runtime.FileName);
            bool runtimeArchiveReady = await IsValidFileAsync(runtimeArchive,
                manifest.Runtime.Bytes, manifest.Runtime.Sha256, cancellationToken).ConfigureAwait(false);
            if (!runtimeArchiveReady)
            {
                long existingArchiveBytes = File.Exists(runtimeArchive)
                    ? Math.Min(manifest.Runtime.Bytes, new FileInfo(runtimeArchive).Length)
                    : 0;
                long runtimeCacheNeed = checked(EstimateMissingPartBytes(manifest.Runtime, cacheRoot) +
                    manifest.Runtime.Bytes - existingArchiveBytes);
                EnsureEnoughFreeSpace(cacheRoot, runtimeCacheNeed, 0);
                await DownloadArtifactPartsAsync(releaseBase, manifest.Runtime, cacheRoot,
                    3, 30, "جارٍ تنزيل أجزاء الحزمة...", progress, cancellationToken)
                    .ConfigureAwait(false);
                Report(progress, 31, "جارٍ تجميع الحزمة المحمولة...", null);
                await AssembleArtifactAsync(manifest.Runtime, cacheRoot, runtimeArchive,
                    31, 38, progress, cancellationToken).ConfigureAwait(false);
            }
            else
            {
                Report(progress, 38, "تم العثور على runtime.zip سليم في الذاكرة المؤقتة", null);
            }
            DeleteArtifactPartFiles(manifest.Runtime, cacheRoot);

            EnsureEnoughFreeSpace(cacheRoot,
                EstimateMissingPartBytes(manifest.Model, cacheRoot), 0);
            await DownloadArtifactPartsAsync(releaseBase, manifest.Model, cacheRoot,
                39, 65, "جارٍ تنزيل أجزاء النموذج...", progress, cancellationToken)
                .ConfigureAwait(false);

            string installParent = Path.GetDirectoryName(installRoot);
            if (String.IsNullOrWhiteSpace(installParent))
                throw new InstallerException("مسار التثبيت غير صالح.");
            Directory.CreateDirectory(installParent);
            string stagingRoot = Path.Combine(installParent,
                ".MujassamAI-" + Guid.NewGuid().ToString("N").Substring(0, 12));
            EnsureLegacyPathLength(stagingRoot, true);
            EnsureLegacyPathLength(installRoot, true);

            string previousInstall = null;
            try
            {
                Directory.CreateDirectory(stagingRoot);
                Report(progress, 66, "جارٍ فك الحزمة بأمان...", null);
                await ExtractArchiveSafelyAsync(runtimeArchive, stagingRoot,
                    manifest.Model.Bytes, progress, cancellationToken).ConfigureAwait(false);

                cancellationToken.ThrowIfCancellationRequested();
                string modelPath = Path.Combine(stagingRoot,
                    manifest.Model.Destination.Replace('/', Path.DirectorySeparatorChar));
                string modelParent = Path.GetDirectoryName(modelPath);
                if (String.IsNullOrWhiteSpace(modelParent))
                    throw new InstallerException("مسار النموذج في manifest غير صالح.");
                EnsureLegacyPathLength(modelPath, false);
                Directory.CreateDirectory(modelParent);
                Report(progress, 79, "جارٍ تجميع النموذج والتحقق منه...", null);
                await AssembleArtifactAsync(manifest.Model, cacheRoot, modelPath,
                    79, 96, progress, cancellationToken).ConfigureAwait(false);

                ValidateStagedApplication(stagingRoot, manifest.Executable);
                cancellationToken.ThrowIfCancellationRequested();
                Report(progress, 97, "جارٍ إنهاء التثبيت...", null);
                previousInstall = CommitStagedApplication(stagingRoot, installRoot);
                stagingRoot = null;
                DeleteArtifactPartFiles(manifest.Model, cacheRoot);
                TryDeleteFile(runtimeArchive);
            }
            finally
            {
                if (!String.IsNullOrWhiteSpace(stagingRoot))
                    TryDeleteDirectory(stagingRoot);
            }

            string executablePath = Path.Combine(installRoot, manifest.Executable);
            bool launched = false;
            string warning = !String.IsNullOrWhiteSpace(previousInstall)
                ? "تم الاحتفاظ بالتثبيت السابق في: " + previousInstall
                : null;
            if (cancellationToken.IsCancellationRequested)
            {
                warning = String.IsNullOrWhiteSpace(warning)
                    ? "اكتمل التثبيت، لكن أُلغي التشغيل التلقائي بطلب المستخدم."
                    : warning + Environment.NewLine +
                      "اكتمل التثبيت، لكن أُلغي التشغيل التلقائي بطلب المستخدم.";
            }
            else
            {
                try
                {
                    using (Process launchedProcess = Process.Start(new ProcessStartInfo
                    {
                        FileName = executablePath,
                        WorkingDirectory = installRoot,
                        UseShellExecute = true
                    }))
                    {
                        if (launchedProcess == null)
                            throw new InstallerException("لم يُرجع Windows عملية للبرنامج المثبّت.");
                    }
                    launched = true;
                }
                catch (Exception error)
                {
                    string failedInstall;
                    string rollbackError;
                    if (TryRollbackAfterLaunchFailure(installRoot, previousInstall,
                        out failedInstall, out rollbackError))
                    {
                        throw new InstallerException(
                            "تعذر تشغيل الإصدار الجديد، لذلك تمت استعادة التثبيت السابق. " +
                            "حُفظت النسخة الجديدة غير المشغّلة في: " + failedInstall, error);
                    }

                    string launchWarning = "تعذر التشغيل التلقائي: " + error.Message;
                    if (!String.IsNullOrWhiteSpace(rollbackError))
                        launchWarning += Environment.NewLine + "تعذر rollback: " + rollbackError;
                    warning = String.IsNullOrWhiteSpace(warning)
                        ? launchWarning
                        : warning + Environment.NewLine + launchWarning;
                }
            }

            if (launched)
                TryDeleteDirectory(cacheRoot);
            Report(progress, 100, "اكتمل التثبيت", null);
            return new InstallResult(installRoot, launched, warning);
        }

        private static string GetCacheRoot(Uri releaseBase, string manifestSha256)
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (String.IsNullOrWhiteSpace(local))
                local = Path.GetTempPath();
            string key;
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(Encoding.UTF8.GetBytes(
                    releaseBase.AbsoluteUri + "|" + manifestSha256));
                key = ToHex(digest).Substring(0, 20);
            }
            return Path.Combine(local, "MujassamAI", "InstallerCache", key);
        }

        private static async Task<string> DownloadManifestAsync(
            Uri uri,
            string expectedSha256,
            CancellationToken token)
        {
            HttpWebRequest request = CreateRequest(uri);
            using (token.Register(delegate { TryAbort(request); }))
            {
                try
                {
                    using (HttpWebResponse response =
                        await GetResponseWithTimeoutAsync(request, token).ConfigureAwait(false))
                    {
                        ValidateDownloadResponse(response);
                        if (response.StatusCode != HttpStatusCode.OK)
                            throw new InstallerException("تعذر تنزيل release-manifest.json من GitHub.");
                        if (response.ContentLength > ManifestMaximumBytes)
                            throw new InstallerException("حجم release-manifest.json أكبر من الحد الآمن.");
                        using (Stream input = response.GetResponseStream())
                        using (MemoryStream output = new MemoryStream())
                        {
                            byte[] buffer = new byte[32 * 1024];
                            int read;
                            while ((read = await ReadNetworkWithTimeoutAsync(input, buffer,
                                request, token)
                                .ConfigureAwait(false)) > 0)
                            {
                                if (output.Length + read > ManifestMaximumBytes)
                                    throw new InstallerException("حجم release-manifest.json أكبر من الحد الآمن.");
                                output.Write(buffer, 0, read);
                            }
                            byte[] raw = output.ToArray();
                            string actualSha256;
                            using (SHA256 hash = SHA256.Create())
                                actualSha256 = ToHex(hash.ComputeHash(raw));
                            if (!FixedTimeEquals(actualSha256, expectedSha256))
                            {
                                throw new InstallerException(
                                    "فشل تحقق SHA-256 لملف release-manifest.json. " +
                                    "قد لا يطابق Setup.exe هذا الإصدار.");
                            }
                            string text = new UTF8Encoding(false, true).GetString(raw);
                            if (text.Length > 0 && text[0] == '\uFEFF')
                                text = text.Substring(1);
                            return text;
                        }
                    }
                }
                catch (WebException error)
                {
                    if (token.IsCancellationRequested)
                        throw new OperationCanceledException(token);
                    throw new InstallerException("فشل الاتصال بـGitHub: " + WebErrorMessage(error), error);
                }
            }
        }

        private static async Task DownloadArtifactPartsAsync(
            Uri releaseBase,
            ArtifactSpec artifact,
            string cacheRoot,
            int progressStart,
            int progressEnd,
            string status,
            IProgress<InstallProgress> progress,
            CancellationToken token)
        {
            long completedBytes = 0;
            for (int index = 0; index < artifact.Parts.Count; index++)
            {
                token.ThrowIfCancellationRequested();
                PartSpec part = artifact.Parts[index];
                string partPath = Path.Combine(cacheRoot, part.Name);
                Uri partUri = new Uri(releaseBase, Uri.EscapeDataString(part.Name));
                string detail = String.Format(CultureInfo.InvariantCulture,
                    "الجزء {0}/{1}: {2}", index + 1, artifact.Parts.Count, part.Name);
                Report(progress, ScaleProgress(completedBytes, artifact.Bytes,
                    progressStart, progressEnd), status, detail);
                await DownloadPartWithRetryAsync(partUri, part, partPath, completedBytes,
                    artifact.Bytes, progressStart, progressEnd, progress, token).ConfigureAwait(false);
                completedBytes = checked(completedBytes + part.Bytes);
                Report(progress, ScaleProgress(completedBytes, artifact.Bytes,
                    progressStart, progressEnd), status, null);
            }
        }

        private static long EstimateMissingPartBytes(ArtifactSpec artifact, string cacheRoot)
        {
            long missing = 0;
            foreach (PartSpec part in artifact.Parts)
            {
                string complete = Path.Combine(cacheRoot, part.Name);
                string partial = complete + ".download";
                long existing = 0;
                if (File.Exists(complete))
                    existing = Math.Min(part.Bytes, new FileInfo(complete).Length);
                else if (File.Exists(partial))
                    existing = Math.Min(part.Bytes, new FileInfo(partial).Length);
                missing = checked(missing + part.Bytes - existing);
            }
            return missing;
        }

        private static void DeleteArtifactPartFiles(ArtifactSpec artifact, string cacheRoot)
        {
            foreach (PartSpec part in artifact.Parts)
            {
                TryDeleteFile(Path.Combine(cacheRoot, part.Name));
                TryDeleteFile(Path.Combine(cacheRoot, part.Name) + ".download");
            }
        }

        private static async Task DownloadPartWithRetryAsync(
            Uri uri,
            PartSpec part,
            string destination,
            long completedBeforePart,
            long totalBytes,
            int progressStart,
            int progressEnd,
            IProgress<InstallProgress> progress,
            CancellationToken token)
        {
            if (await IsValidFileAsync(destination, part.Bytes, part.Sha256, token)
                .ConfigureAwait(false))
            {
                Report(progress, ScaleProgress(completedBeforePart + part.Bytes, totalBytes,
                        progressStart, progressEnd),
                    "تم العثور على جزء سليم في الذاكرة المؤقتة", part.Name);
                return;
            }
            TryDeleteFile(destination);

            Exception lastError = null;
            for (int attempt = 1; attempt <= DownloadAttempts; attempt++)
            {
                token.ThrowIfCancellationRequested();
                try
                {
                    await DownloadPartOnceAsync(uri, part, destination, completedBeforePart,
                        totalBytes, progressStart, progressEnd, progress, token).ConfigureAwait(false);
                    return;
                }
                catch (OperationCanceledException)
                {
                    throw;
                }
                catch (Exception error)
                {
                    lastError = error;
                    if (attempt >= DownloadAttempts)
                        break;
                    Report(progress, ScaleProgress(completedBeforePart, totalBytes,
                            progressStart, progressEnd),
                        "تعذر تنزيل الجزء؛ ستتم إعادة المحاولة...",
                        part.Name + " — محاولة " + (attempt + 1).ToString(CultureInfo.InvariantCulture));
                    await Task.Delay(TimeSpan.FromSeconds(attempt * 2), token).ConfigureAwait(false);
                }
            }
            throw new InstallerException("فشل تنزيل الجزء " + part.Name + " بعد عدة محاولات.", lastError);
        }

        private static async Task DownloadPartOnceAsync(
            Uri uri,
            PartSpec part,
            string destination,
            long completedBeforePart,
            long totalBytes,
            int progressStart,
            int progressEnd,
            IProgress<InstallProgress> progress,
            CancellationToken token)
        {
            string partial = destination + ".download";
            long offset = File.Exists(partial) ? new FileInfo(partial).Length : 0;
            if (offset < 0 || offset > part.Bytes)
            {
                TryDeleteFile(partial);
                offset = 0;
            }
            if (offset == part.Bytes)
            {
                if (await IsValidFileAsync(partial, part.Bytes, part.Sha256, token)
                    .ConfigureAwait(false))
                {
                    MoveVerifiedFile(partial, destination);
                    return;
                }
                TryDeleteFile(partial);
                offset = 0;
            }

            HttpWebRequest request = CreateRequest(uri);
            if (offset > 0)
                request.AddRange((int)offset);
            using (token.Register(delegate { TryAbort(request); }))
            {
                try
                {
                    using (HttpWebResponse response =
                        await GetResponseWithTimeoutAsync(request, token).ConfigureAwait(false))
                    {
                        ValidateDownloadResponse(response);
                        bool resumed = offset > 0 && response.StatusCode == HttpStatusCode.PartialContent;
                        if (response.StatusCode != HttpStatusCode.OK &&
                            response.StatusCode != HttpStatusCode.PartialContent)
                        {
                            throw new InstallerException("أرجع GitHub حالة تنزيل غير متوقعة.");
                        }
                        if (offset > 0 && !resumed)
                        {
                            offset = 0;
                            TryDeleteFile(partial);
                        }
                        if (resumed)
                        {
                            string range = response.Headers[HttpResponseHeader.ContentRange];
                            ValidateContentRange(range, offset, part.Bytes, response.ContentLength);
                        }
                        long remaining = part.Bytes - offset;
                        if (response.ContentLength > remaining)
                            throw new InstallerException("حجم الجزء الذي أرسله الخادم أكبر من المتوقع.");

                        FileMode mode = offset > 0 ? FileMode.Append : FileMode.Create;
                        using (Stream input = response.GetResponseStream())
                        using (FileStream output = new FileStream(partial, mode, FileAccess.Write,
                            FileShare.None, BufferSize, true))
                        {
                            byte[] buffer = new byte[BufferSize];
                            long written = offset;
                            int read;
                            while ((read = await ReadNetworkWithTimeoutAsync(input, buffer,
                                request, token)
                                .ConfigureAwait(false)) > 0)
                            {
                                written = checked(written + read);
                                if (written > part.Bytes)
                                    throw new InstallerException("تجاوز الجزء الحجم المحدد في manifest.");
                                await output.WriteAsync(buffer, 0, read, token).ConfigureAwait(false);
                                Report(progress, ScaleProgress(completedBeforePart + written, totalBytes,
                                        progressStart, progressEnd),
                                    "جارٍ تنزيل ملفات الإصدار...", null);
                            }
                            output.Flush(true);
                            if (written != part.Bytes)
                                throw new InstallerException("انقطع تنزيل الجزء قبل اكتماله.");
                        }
                    }
                }
                catch (WebException error)
                {
                    if (token.IsCancellationRequested)
                        throw new OperationCanceledException(token);
                    HttpWebResponse errorResponse = error.Response as HttpWebResponse;
                    if (errorResponse != null &&
                        errorResponse.StatusCode == HttpStatusCode.RequestedRangeNotSatisfiable)
                    {
                        errorResponse.Dispose();
                        TryDeleteFile(partial);
                        throw new InstallerException(
                            "رفض الخادم نطاق الاستئناف؛ ستبدأ المحاولة التالية من الصفر.", error);
                    }
                    throw new InstallerException("فشل تنزيل " + part.Name + ": " + WebErrorMessage(error), error);
                }
            }

            if (!await IsValidFileAsync(partial, part.Bytes, part.Sha256, token)
                .ConfigureAwait(false))
            {
                TryDeleteFile(partial);
                throw new InstallerException("فشل تحقق SHA-256 للجزء " + part.Name + ".");
            }
            MoveVerifiedFile(partial, destination);
        }

        private static async Task AssembleArtifactAsync(
            ArtifactSpec artifact,
            string cacheRoot,
            string outputPath,
            int progressStart,
            int progressEnd,
            IProgress<InstallProgress> progress,
            CancellationToken token)
        {
            if (await IsValidFileAsync(outputPath, artifact.Bytes, artifact.Sha256, token)
                .ConfigureAwait(false))
            {
                Report(progress, progressEnd, "تم التحقق من " + artifact.FileName, null);
                return;
            }
            TryDeleteFile(outputPath);
            string outputParent = Path.GetDirectoryName(outputPath);
            if (String.IsNullOrWhiteSpace(outputParent))
                throw new InstallerException("مسار الملف المجمّع غير صالح.");
            Directory.CreateDirectory(outputParent);
            string temporary = outputPath + ".assembling";
            TryDeleteFile(temporary);

            long totalWritten = 0;
            try
            {
                using (SHA256 wholeHash = SHA256.Create())
                using (FileStream output = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write,
                    FileShare.None, BufferSize, true))
                {
                    byte[] buffer = new byte[BufferSize];
                    foreach (PartSpec part in artifact.Parts)
                    {
                        token.ThrowIfCancellationRequested();
                        string partPath = Path.Combine(cacheRoot, part.Name);
                        long partWritten = 0;
                        string partDigest;
                        using (SHA256 partHash = SHA256.Create())
                        using (FileStream input = new FileStream(partPath, FileMode.Open, FileAccess.Read,
                            FileShare.Read, BufferSize, true))
                        {
                            int read;
                            while ((read = await input.ReadAsync(buffer, 0, buffer.Length, token)
                                .ConfigureAwait(false)) > 0)
                            {
                                partWritten = checked(partWritten + read);
                                totalWritten = checked(totalWritten + read);
                                if (partWritten > part.Bytes || totalWritten > artifact.Bytes)
                                    throw new InstallerException("تغير حجم جزء بعد تنزيله.");
                                partHash.TransformBlock(buffer, 0, read, null, 0);
                                wholeHash.TransformBlock(buffer, 0, read, null, 0);
                                await output.WriteAsync(buffer, 0, read, token).ConfigureAwait(false);
                                int percent = ScaleProgress(totalWritten, artifact.Bytes,
                                    progressStart, progressEnd);
                                Report(progress, percent, "جارٍ تجميع " + artifact.FileName + "...", null);
                            }
                            partHash.TransformFinalBlock(new byte[0], 0, 0);
                            partDigest = ToHex(partHash.Hash);
                        }
                        if (partWritten != part.Bytes ||
                            !FixedTimeEquals(partDigest, part.Sha256))
                        {
                            throw new InstallerException("تغير SHA-256 للجزء " + part.Name + ".");
                        }
                    }
                    wholeHash.TransformFinalBlock(new byte[0], 0, 0);
                    output.Flush(true);
                    if (totalWritten != artifact.Bytes ||
                        !FixedTimeEquals(ToHex(wholeHash.Hash), artifact.Sha256))
                    {
                        throw new InstallerException("فشل تحقق SHA-256 للملف النهائي " +
                            artifact.FileName + ".");
                    }
                }
                MoveVerifiedFile(temporary, outputPath);
            }
            catch
            {
                TryDeleteFile(temporary);
                throw;
            }
        }

        private static async Task<long> ExtractArchiveSafelyAsync(
            string archivePath,
            string stagingRoot,
            long modelBytes,
            IProgress<InstallProgress> progress,
            CancellationToken token)
        {
            string normalizedRoot = Path.GetFullPath(stagingRoot).TrimEnd(
                Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            long totalBytes = 0;
            int entryCount = 0;
            HashSet<string> destinations = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string modelDestination = Path.GetFullPath(Path.Combine(stagingRoot,
                "models", "spar3d", "model.safetensors"));

            using (FileStream archiveStream = new FileStream(archivePath, FileMode.Open, FileAccess.Read,
                FileShare.Read, BufferSize, true))
            using (ZipArchive archive = new ZipArchive(archiveStream, ZipArchiveMode.Read, false))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    entryCount++;
                    if (entryCount > MaximumZipEntries)
                        throw new InstallerException("يحتوي runtime.zip على عدد ملفات أكبر من الحد الآمن.");
                    string destination = GetSafeZipDestination(entry, normalizedRoot);
                    if (!destinations.Add(destination))
                        throw new InstallerException("يحتوي runtime.zip على مسارات مكررة.");
                    if (!IsZipDirectory(entry))
                    {
                        if (String.Equals(destination, modelDestination,
                            StringComparison.OrdinalIgnoreCase))
                        {
                            throw new InstallerException(
                                "يجب ألا يحتوي runtime.zip على model.safetensors؛ النموذج أصل مستقل.");
                        }
                        totalBytes = checked(totalBytes + entry.Length);
                        if (entry.Length < 0 || totalBytes > MaximumExtractedBytes)
                            throw new InstallerException("حجم runtime.zip بعد الفك أكبر من الحد الآمن.");
                    }
                }

                EnsureEnoughFreeSpace(stagingRoot, checked(totalBytes + modelBytes), 0);
                long extracted = 0;
                byte[] buffer = new byte[BufferSize];
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    token.ThrowIfCancellationRequested();
                    string destination = GetSafeZipDestination(entry, normalizedRoot);
                    if (IsZipDirectory(entry))
                    {
                        Directory.CreateDirectory(destination);
                        continue;
                    }
                    string parent = Path.GetDirectoryName(destination);
                    if (String.IsNullOrWhiteSpace(parent))
                        throw new InstallerException("مسار داخل runtime.zip غير صالح.");
                    Directory.CreateDirectory(parent);
                    long entryWritten = 0;
                    using (Stream input = entry.Open())
                    using (FileStream output = new FileStream(destination, FileMode.CreateNew,
                        FileAccess.Write, FileShare.None, BufferSize, true))
                    {
                        int read;
                        while ((read = await input.ReadAsync(buffer, 0, buffer.Length, token)
                            .ConfigureAwait(false)) > 0)
                        {
                            entryWritten = checked(entryWritten + read);
                            extracted = checked(extracted + read);
                            if (entryWritten > entry.Length || extracted > totalBytes)
                                throw new InstallerException("تجاوز ملف داخل ZIP حجمه المعلن.");
                            await output.WriteAsync(buffer, 0, read, token).ConfigureAwait(false);
                            Report(progress, ScaleProgress(extracted, Math.Max(1, totalBytes), 66, 78),
                                "جارٍ فك الحزمة بأمان...", null);
                        }
                        output.Flush(true);
                    }
                    if (entryWritten != entry.Length)
                        throw new InstallerException("لم يكتمل فك ملف داخل runtime.zip.");
                }
                if (extracted != totalBytes)
                    throw new InstallerException("لم يطابق الحجم المفكوك بيانات runtime.zip.");
                return totalBytes;
            }
        }

        private static string GetSafeZipDestination(ZipArchiveEntry entry, string normalizedRoot)
        {
            if (entry == null || String.IsNullOrWhiteSpace(entry.FullName) ||
                entry.FullName.IndexOf('\0') >= 0)
                throw new InstallerException("يحتوي runtime.zip على اسم ملف غير صالح.");
            int unixType = (entry.ExternalAttributes >> 16) & 0xF000;
            if (unixType == 0xA000 ||
                (entry.ExternalAttributes & (int)FileAttributes.ReparsePoint) != 0)
            {
                throw new InstallerException(
                    "لا يسمح المثبّت بالروابط الرمزية أو reparse points داخل runtime.zip.");
            }

            if (entry.FullName.IndexOf('\\') >= 0)
                throw new InstallerException("رفض المثبّت فواصل Windows غير القياسية داخل ZIP.");
            bool directoryEntry = entry.FullName.EndsWith("/", StringComparison.Ordinal);
            string canonical = directoryEntry
                ? entry.FullName.Substring(0, entry.FullName.Length - 1)
                : entry.FullName;
            if (String.IsNullOrEmpty(canonical) || canonical.StartsWith("/", StringComparison.Ordinal) ||
                canonical.EndsWith("/", StringComparison.Ordinal) ||
                canonical.IndexOf("//", StringComparison.Ordinal) >= 0)
            {
                throw new InstallerException("يحتوي runtime.zip على مقطع مسار فارغ.");
            }

            string relative = canonical.Replace('/', Path.DirectorySeparatorChar);
            if (Path.IsPathRooted(relative) || relative.IndexOf(':') >= 0)
                throw new InstallerException("رفض المثبّت مسارًا مطلقًا داخل runtime.zip.");
            string[] segments = relative.Split(new char[] { Path.DirectorySeparatorChar });
            if (segments.Length == 0)
                throw new InstallerException("يحتوي runtime.zip على مسار فارغ.");
            foreach (string segment in segments)
            {
                if (segment == "." || segment == ".." || segment.EndsWith(" ", StringComparison.Ordinal) ||
                    segment.EndsWith(".", StringComparison.Ordinal) ||
                    segment.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || IsReservedWindowsName(segment))
                {
                    throw new InstallerException("يحتوي runtime.zip على اسم غير آمن: " + segment);
                }
            }

            string destination = Path.GetFullPath(Path.Combine(normalizedRoot, relative));
            if (!destination.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase))
                throw new InstallerException("حاول runtime.zip الكتابة خارج مجلد التثبيت.");
            destination = destination.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);
            EnsureLegacyPathLength(destination, directoryEntry);
            return destination;
        }

        private static void EnsureLegacyPathLength(string path, bool directory)
        {
            int maximum = directory ? MaximumDirectoryPathCharacters : MaximumFilePathCharacters;
            if (String.IsNullOrWhiteSpace(path) || path.Length > maximum)
            {
                throw new InstallerException(
                    "أحد مسارات الحزمة أطول من الحد الذي يعمل بأمان على جميع أجهزة Windows: " + path);
            }
            if (!directory)
            {
                string parent = Path.GetDirectoryName(path);
                if (!String.IsNullOrWhiteSpace(parent) &&
                    parent.Length > MaximumDirectoryPathCharacters)
                {
                    throw new InstallerException(
                        "مجلد داخل الحزمة أطول من الحد الذي يدعمه Windows دون إعدادات إضافية.");
                }
            }
        }

        private static bool IsReservedWindowsName(string segment)
        {
            int firstDot = segment.IndexOf('.');
            string name = (firstDot >= 0 ? segment.Substring(0, firstDot) : segment)
                .ToUpperInvariant();
            if (name == "CON" || name == "PRN" || name == "AUX" || name == "NUL")
                return true;
            if (name.Length == 4 && (name.StartsWith("COM", StringComparison.Ordinal) ||
                name.StartsWith("LPT", StringComparison.Ordinal)))
            {
                char number = name[3];
                return (number >= '1' && number <= '9') || number == '\u00B9' ||
                    number == '\u00B2' || number == '\u00B3';
            }
            return false;
        }

        private static bool IsZipDirectory(ZipArchiveEntry entry)
        {
            return entry.FullName.EndsWith("/", StringComparison.Ordinal) ||
                entry.FullName.EndsWith("\\", StringComparison.Ordinal);
        }

        private static void EnsureEnoughFreeSpace(string path, long requiredBytes, long alreadyWritten)
        {
            if (requiredBytes <= 0)
                return;
            try
            {
                string root = Path.GetPathRoot(Path.GetFullPath(path));
                if (String.IsNullOrWhiteSpace(root))
                    return;
                DriveInfo drive = new DriveInfo(root);
                const long reserve = 1024L * 1024L * 1024L;
                long need = checked(requiredBytes + reserve - Math.Max(0, alreadyWritten));
                if (drive.AvailableFreeSpace < need)
                {
                    throw new InstallerException(String.Format(CultureInfo.InvariantCulture,
                        "المساحة الحرة غير كافية. المطلوب تقريبًا {0:N1} GB والمتاح {1:N1} GB.",
                        need / 1073741824.0, drive.AvailableFreeSpace / 1073741824.0));
                }
            }
            catch (InstallerException)
            {
                throw;
            }
            catch
            {
                // Some redirected Documents folders do not expose DriveInfo. Extraction still
                // remains bounded and normal IO errors are handled without committing staging.
            }
        }

        private static void ValidateStagedApplication(string root, string executable)
        {
            string[] required = new string[]
            {
                executable,
                Path.Combine("rt", "python.exe"),
                Path.Combine("app", "worker.py"),
                Path.Combine("app", "vendor", "stable-point-aware-3d", "run.py")
            };
            foreach (string relative in required)
            {
                string path = Path.Combine(root, relative);
                if (!File.Exists(path) || new FileInfo(path).Length <= 0)
                    throw new InstallerException("الحزمة المفكوكة غير مكتملة؛ الملف مفقود: " + relative);
            }
        }

        private static string CommitStagedApplication(string stagingRoot, string installRoot)
        {
            string backup = null;
            if (File.Exists(installRoot))
                throw new InstallerException("يوجد ملف مكان مجلد التثبيت المطلوب: " + installRoot);
            if (Directory.Exists(installRoot))
            {
                FileAttributes attributes = File.GetAttributes(installRoot);
                if ((attributes & FileAttributes.ReparsePoint) != 0)
                    throw new InstallerException("رفض المثبّت استبدال مجلد تثبيت من نوع link أو junction.");
                backup = Path.Combine(Path.GetDirectoryName(installRoot),
                    ".MujassamAI-old-" + Guid.NewGuid().ToString("N").Substring(0, 12));
                EnsureLegacyPathLength(backup, true);
                Directory.Move(installRoot, backup);
            }

            try
            {
                Directory.Move(stagingRoot, installRoot);
            }
            catch
            {
                if (!String.IsNullOrWhiteSpace(backup) && Directory.Exists(backup) &&
                    !Directory.Exists(installRoot))
                {
                    try
                    {
                        Directory.Move(backup, installRoot);
                    }
                    catch
                    {
                    }
                }
                throw;
            }

            return backup;
        }

        private static bool TryRollbackAfterLaunchFailure(
            string installRoot,
            string previousInstall,
            out string failedInstall,
            out string rollbackError)
        {
            failedInstall = null;
            rollbackError = null;
            if (String.IsNullOrWhiteSpace(previousInstall) || !Directory.Exists(previousInstall) ||
                !Directory.Exists(installRoot))
            {
                return false;
            }

            string failed = Path.Combine(Path.GetDirectoryName(installRoot),
                ".MujassamAI-failed-" + Guid.NewGuid().ToString("N").Substring(0, 12));
            EnsureLegacyPathLength(failed, true);
            bool movedNewInstall = false;
            try
            {
                Directory.Move(installRoot, failed);
                movedNewInstall = true;
                Directory.Move(previousInstall, installRoot);
                failedInstall = failed;
                return true;
            }
            catch (Exception error)
            {
                rollbackError = error.Message;
                if (movedNewInstall && !Directory.Exists(installRoot) && Directory.Exists(failed))
                {
                    try
                    {
                        Directory.Move(failed, installRoot);
                    }
                    catch (Exception restoreError)
                    {
                        rollbackError += Environment.NewLine +
                            "تعذر كذلك إعادة الإصدار الجديد إلى مساره: " + restoreError.Message;
                    }
                }
                return false;
            }
        }

        private static HttpWebRequest CreateRequest(Uri uri)
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(uri);
            request.Method = "GET";
            request.UserAgent = "MujassamAI-Installer/1.0";
            request.Accept = "application/octet-stream, application/json;q=0.9, */*;q=0.5";
            request.Headers[HttpRequestHeader.AcceptEncoding] = "identity";
            request.AllowAutoRedirect = true;
            request.MaximumAutomaticRedirections = 10;
            request.AutomaticDecompression = DecompressionMethods.None;
            request.Timeout = 30000;
            request.ReadWriteTimeout = 60000;
            request.KeepAlive = true;
            return request;
        }

        private static async Task<HttpWebResponse> GetResponseWithTimeoutAsync(
            HttpWebRequest request,
            CancellationToken userToken)
        {
            using (CancellationTokenSource deadline =
                CancellationTokenSource.CreateLinkedTokenSource(userToken))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(NetworkTimeoutSeconds));
                using (deadline.Token.Register(delegate { TryAbort(request); }))
                {
                    try
                    {
                        return (HttpWebResponse)await request.GetResponseAsync().ConfigureAwait(false);
                    }
                    catch (Exception error)
                    {
                        if (userToken.IsCancellationRequested)
                            throw new OperationCanceledException(userToken);
                        if (deadline.IsCancellationRequested)
                            throw new InstallerException("انتهت مهلة الاتصال بخادم GitHub.", error);
                        throw;
                    }
                }
            }
        }

        private static async Task<int> ReadNetworkWithTimeoutAsync(
            Stream input,
            byte[] buffer,
            HttpWebRequest request,
            CancellationToken userToken)
        {
            using (CancellationTokenSource stall =
                CancellationTokenSource.CreateLinkedTokenSource(userToken))
            {
                stall.CancelAfter(TimeSpan.FromSeconds(NetworkTimeoutSeconds));
                using (stall.Token.Register(delegate { TryAbort(request); }))
                {
                    try
                    {
                        return await input.ReadAsync(buffer, 0, buffer.Length, stall.Token)
                            .ConfigureAwait(false);
                    }
                    catch (Exception error)
                    {
                        if (userToken.IsCancellationRequested)
                            throw new OperationCanceledException(userToken);
                        if (stall.IsCancellationRequested)
                            throw new InstallerException(
                                "توقف التنزيل دون بيانات لأكثر من دقيقتين.", error);
                        throw;
                    }
                }
            }
        }

        private static void ValidateContentRange(
            string value,
            long expectedStart,
            long expectedTotal,
            long responseLength)
        {
            Match match = Regex.Match(value ?? String.Empty,
                "^bytes ([0-9]+)-([0-9]+)/([0-9]+)$",
                RegexOptions.CultureInvariant | RegexOptions.IgnoreCase);
            long start;
            long end;
            long total;
            if (!match.Success ||
                !Int64.TryParse(match.Groups[1].Value, NumberStyles.None,
                    CultureInfo.InvariantCulture, out start) ||
                !Int64.TryParse(match.Groups[2].Value, NumberStyles.None,
                    CultureInfo.InvariantCulture, out end) ||
                !Int64.TryParse(match.Groups[3].Value, NumberStyles.None,
                    CultureInfo.InvariantCulture, out total) ||
                start != expectedStart || total != expectedTotal || end < start || end >= total ||
                (responseLength >= 0 && responseLength != end - start + 1))
            {
                throw new InstallerException("أرسل الخادم Content-Range غير صالح للاستئناف.");
            }
        }

        private static void ValidateDownloadResponse(HttpWebResponse response)
        {
            if (response == null || response.ResponseUri == null ||
                !String.Equals(response.ResponseUri.Scheme, Uri.UriSchemeHttps,
                    StringComparison.OrdinalIgnoreCase) || !IsAllowedDownloadHost(response.ResponseUri.Host))
            {
                throw new InstallerException("رفض المثبّت إعادة توجيه تنزيل خارج نطاقات GitHub الآمنة.");
            }
        }

        private static bool IsAllowedDownloadHost(string host)
        {
            if (String.Equals(host, "github.com", StringComparison.OrdinalIgnoreCase))
                return true;
            return host != null && host.EndsWith(".githubusercontent.com",
                StringComparison.OrdinalIgnoreCase);
        }

        private static string WebErrorMessage(WebException error)
        {
            HttpWebResponse response = error.Response as HttpWebResponse;
            if (response != null)
            {
                using (response)
                    return "HTTP " + ((int)response.StatusCode).ToString(CultureInfo.InvariantCulture);
            }
            return error.Status.ToString();
        }

        private static void TryAbort(HttpWebRequest request)
        {
            try
            {
                request.Abort();
            }
            catch
            {
            }
        }

        private static async Task<bool> IsValidFileAsync(
            string path,
            long expectedBytes,
            string expectedSha256,
            CancellationToken token)
        {
            if (!File.Exists(path))
                return false;
            FileInfo info = new FileInfo(path);
            if (info.Length != expectedBytes)
                return false;
            string actual = await ComputeSha256Async(path, token).ConfigureAwait(false);
            return FixedTimeEquals(actual, expectedSha256);
        }

        private static async Task<string> ComputeSha256Async(string path, CancellationToken token)
        {
            using (SHA256 hash = SHA256.Create())
            using (FileStream input = new FileStream(path, FileMode.Open, FileAccess.Read,
                FileShare.Read, BufferSize, true))
            {
                byte[] buffer = new byte[BufferSize];
                int read;
                while ((read = await input.ReadAsync(buffer, 0, buffer.Length, token)
                    .ConfigureAwait(false)) > 0)
                {
                    hash.TransformBlock(buffer, 0, read, null, 0);
                }
                hash.TransformFinalBlock(new byte[0], 0, 0);
                return ToHex(hash.Hash);
            }
        }

        private static bool FixedTimeEquals(string left, string right)
        {
            if (left == null || right == null || left.Length != right.Length)
                return false;
            int difference = 0;
            for (int index = 0; index < left.Length; index++)
                difference |= left[index] ^ right[index];
            return difference == 0;
        }

        private static string ToHex(byte[] value)
        {
            StringBuilder result = new StringBuilder(value.Length * 2);
            foreach (byte item in value)
                result.Append(item.ToString("x2", CultureInfo.InvariantCulture));
            return result.ToString();
        }

        private static void MoveVerifiedFile(string source, string destination)
        {
            if (File.Exists(destination))
                File.Delete(destination);
            File.Move(source, destination);
        }

        private static void TryDeleteFile(string path)
        {
            try
            {
                if (!String.IsNullOrWhiteSpace(path) && File.Exists(path))
                    File.Delete(path);
            }
            catch
            {
            }
        }

        private static void TryDeleteDirectory(string path)
        {
            try
            {
                if (!String.IsNullOrWhiteSpace(path) && Directory.Exists(path))
                    Directory.Delete(path, true);
            }
            catch
            {
            }
        }

        private static int ScaleProgress(long completed, long total, int start, int end)
        {
            if (total <= 0)
                return end;
            double ratio = Math.Max(0.0, Math.Min(1.0, completed / (double)total));
            return start + (int)Math.Round((end - start) * ratio, MidpointRounding.AwayFromZero);
        }

        private static void Report(
            IProgress<InstallProgress> progress,
            int percent,
            string status,
            string detail)
        {
            if (progress != null)
                progress.Report(new InstallProgress(percent, status, detail));
        }

        internal sealed class ReleaseManifest
        {
            internal ArtifactSpec Runtime;
            internal ArtifactSpec Model;
            internal string Executable;

            internal static ReleaseManifest ParseAndValidate(string json)
            {
                ManifestWire wire;
                try
                {
                    JavaScriptSerializer serializer = new JavaScriptSerializer();
                    serializer.MaxJsonLength = ManifestMaximumBytes;
                    serializer.RecursionLimit = 20;
                    wire = serializer.Deserialize<ManifestWire>(json);
                }
                catch (Exception error)
                {
                    throw new InstallerException("release-manifest.json ليس JSON صالحًا.", error);
                }
                if (wire == null || wire.schema_version != 1 || wire.runtime == null ||
                    wire.model == null || String.IsNullOrWhiteSpace(wire.executable))
                {
                    throw new InstallerException("بنية release-manifest.json غير مدعومة.");
                }
                if (!String.Equals(wire.executable, "MujassamAI.exe", StringComparison.Ordinal))
                    throw new InstallerException("اسم البرنامج في manifest غير متوقع.");

                ArtifactSpec runtime = ParseArtifact(wire.runtime, "runtime.zip", null);
                ArtifactSpec model = ParseArtifact(wire.model, "model.safetensors",
                    "models/spar3d/model.safetensors");
                HashSet<string> names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                foreach (PartSpec part in runtime.Parts)
                {
                    if (!names.Add(part.Name))
                        throw new InstallerException("اسم جزء مكرر في manifest: " + part.Name);
                }
                foreach (PartSpec part in model.Parts)
                {
                    if (!names.Add(part.Name))
                        throw new InstallerException("اسم جزء مكرر في manifest: " + part.Name);
                }
                return new ReleaseManifest
                {
                    Runtime = runtime,
                    Model = model,
                    Executable = wire.executable
                };
            }

            private static ArtifactSpec ParseArtifact(
                ArtifactWire wire,
                string requiredFileName,
                string requiredDestination)
            {
                if (wire == null || !String.Equals(wire.file_name, requiredFileName,
                    StringComparison.Ordinal) || wire.bytes <= 0 || wire.bytes > MaximumArtifactBytes ||
                    !Sha256Pattern.IsMatch(wire.sha256 ?? String.Empty) || wire.parts == null ||
                    wire.parts.Count == 0 || wire.parts.Count > MaximumPartsPerArtifact)
                {
                    throw new InstallerException("وصف " + requiredFileName + " في manifest غير صالح.");
                }
                if (requiredDestination != null &&
                    !String.Equals(wire.destination, requiredDestination, StringComparison.Ordinal))
                {
                    throw new InstallerException("مسار model.safetensors في manifest غير صالح.");
                }

                List<PartSpec> parts = new List<PartSpec>();
                long sum = 0;
                for (int index = 0; index < wire.parts.Count; index++)
                {
                    PartWire item = wire.parts[index];
                    string expectedPartName = requiredFileName + ".part" +
                        (index + 1).ToString("D3", CultureInfo.InvariantCulture);
                    if (item == null || !AssetNamePattern.IsMatch(item.name ?? String.Empty) ||
                        !String.Equals(item.name, expectedPartName, StringComparison.Ordinal) ||
                        item.name == "." || item.name == ".." || item.bytes <= 0 ||
                        item.bytes > MaximumPartBytes ||
                        !Sha256Pattern.IsMatch(item.sha256 ?? String.Empty))
                    {
                        throw new InstallerException("يحتوي manifest على جزء غير صالح.");
                    }
                    sum = checked(sum + item.bytes);
                    parts.Add(new PartSpec(item.name, item.bytes, item.sha256.ToLowerInvariant()));
                }
                if (sum != wire.bytes)
                    throw new InstallerException("مجموع أحجام أجزاء " + requiredFileName + " غير مطابق.");
                return new ArtifactSpec(requiredFileName, requiredDestination, wire.bytes,
                    wire.sha256.ToLowerInvariant(), parts);
            }

            private sealed class ManifestWire
            {
                public int schema_version { get; set; }
                public ArtifactWire runtime { get; set; }
                public ArtifactWire model { get; set; }
                public string executable { get; set; }
            }

            private sealed class ArtifactWire
            {
                public string file_name { get; set; }
                public string destination { get; set; }
                public long bytes { get; set; }
                public string sha256 { get; set; }
                public List<PartWire> parts { get; set; }
            }

            private sealed class PartWire
            {
                public string name { get; set; }
                public long bytes { get; set; }
                public string sha256 { get; set; }
            }
        }
    }

    internal sealed class ArtifactSpec
    {
        internal readonly string FileName;
        internal readonly string Destination;
        internal readonly long Bytes;
        internal readonly string Sha256;
        internal readonly List<PartSpec> Parts;

        internal ArtifactSpec(string fileName, string destination, long bytes, string sha256,
            List<PartSpec> parts)
        {
            FileName = fileName;
            Destination = destination;
            Bytes = bytes;
            Sha256 = sha256;
            Parts = parts;
        }
    }

    internal sealed class PartSpec
    {
        internal readonly string Name;
        internal readonly long Bytes;
        internal readonly string Sha256;

        internal PartSpec(string name, long bytes, string sha256)
        {
            Name = name;
            Bytes = bytes;
            Sha256 = sha256;
        }
    }

    internal sealed class InstallProgress
    {
        internal readonly int Percent;
        internal readonly string Status;
        internal readonly string Detail;

        internal InstallProgress(int percent, string status, string detail)
        {
            Percent = percent;
            Status = status;
            Detail = detail;
        }
    }

    internal sealed class InstallResult
    {
        internal readonly string InstallRoot;
        internal readonly bool LaunchSucceeded;
        internal readonly string Warning;

        internal InstallResult(string installRoot, bool launchSucceeded, string warning)
        {
            InstallRoot = installRoot;
            LaunchSucceeded = launchSucceeded;
            Warning = warning;
        }
    }

    internal sealed class InstallerException : Exception
    {
        internal InstallerException(string message)
            : base(message)
        {
        }

        internal InstallerException(string message, Exception innerException)
            : base(message, innerException)
        {
        }
    }
}
