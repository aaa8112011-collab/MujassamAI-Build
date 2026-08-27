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

namespace MujassamHunyuanUpdater
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            ServicePointManager.SecurityProtocol |= SecurityProtocolType.Tls12;
            if (args != null && args.Length > 0 &&
                String.Equals(args[0], "--self-test", StringComparison.OrdinalIgnoreCase))
                return RunSelfTest(args);

            bool created;
            using (Mutex mutex = new Mutex(true, "Local\\MujassamAI-Hunyuan-Updater-v1", out created))
            {
                if (!created)
                {
                    MessageBox.Show("محدّث Hunyuan3D يعمل بالفعل.", "تحديث Mujassam AI",
                        MessageBoxButtons.OK, MessageBoxIcon.Information);
                    return 5;
                }
                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new UpdateForm());
                try { mutex.ReleaseMutex(); } catch { }
            }
            return 0;
        }

        private static int RunSelfTest(string[] args)
        {
            string output = args.Length > 1 ? Path.GetFullPath(args[1]) :
                Path.Combine(Path.GetTempPath(), "MujassamHunyuanUpdater-self-test.txt");
            try
            {
                StringBuilder report = new StringBuilder();
                report.AppendLine("Mujassam AI Hunyuan updater self-test");
                report.AppendLine("ReleaseBase=" + UpdaterConfiguration.GetReleaseBaseUri().AbsoluteUri);
                report.AppendLine("ReleaseTag=" + UpdaterConfiguration.GetReleaseTag());
                report.AppendLine("ManifestSHA256=" + UpdaterConfiguration.GetManifestSha256());
                report.AppendLine("InstallRoot=" + UpdateEngine.GetInstallRoot());
                report.AppendLine("Is64BitOS=" + Environment.Is64BitOperatingSystem);
                Directory.CreateDirectory(Path.GetDirectoryName(output));
                File.WriteAllText(output, report.ToString(), new UTF8Encoding(false));
                return Environment.Is64BitOperatingSystem ? 0 : 10;
            }
            catch (Exception error)
            {
                try { File.WriteAllText(output, error.ToString(), new UTF8Encoding(false)); } catch { }
                return 20;
            }
        }
    }

    internal static class UpdaterConfiguration
    {
        internal const string CompiledReleaseBaseUrl = "@@RELEASE_BASE_URL@@";
        internal const string CompiledRepository = "@@GITHUB_REPOSITORY@@";
        internal const string CompiledReleaseTag = "@@RELEASE_TAG@@";
        internal const string CompiledManifestSha256 = "@@MANIFEST_SHA256@@";
        internal const string ManifestAssetName = "hunyuan-update-release-manifest.json";
        private static readonly Regex RepositoryPattern = new Regex(
            "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", RegexOptions.CultureInvariant);

        internal static Uri GetReleaseBaseUri()
        {
            string injected = Normalize(CompiledReleaseBaseUrl);
            Uri uri;
            if (!String.IsNullOrEmpty(injected))
            {
                if (!injected.EndsWith("/", StringComparison.Ordinal)) injected += "/";
                if (!Uri.TryCreate(injected, UriKind.Absolute, out uri))
                    throw new UpdateException("عنوان GitHub Release غير صالح.");
            }
            else
            {
                string repository = Normalize(CompiledRepository);
                string tag = Normalize(CompiledReleaseTag);
                if (!RepositoryPattern.IsMatch(repository) || String.IsNullOrWhiteSpace(tag))
                    throw new UpdateException("المحدّث غير مربوط بإصدار ثابت.");
                uri = new Uri("https://github.com/" + repository + "/releases/download/" +
                    Uri.EscapeDataString(tag) + "/", UriKind.Absolute);
            }
            if (!String.Equals(uri.Scheme, "https", StringComparison.OrdinalIgnoreCase) ||
                !String.Equals(uri.Host, "github.com", StringComparison.OrdinalIgnoreCase) ||
                !uri.IsDefaultPort || !String.IsNullOrEmpty(uri.Query) ||
                uri.AbsolutePath.IndexOf("/releases/download/", StringComparison.OrdinalIgnoreCase) < 0)
                throw new UpdateException("يجب أن يكون رابط التحديث GitHub Release آمنًا.");
            return uri;
        }

        internal static string GetReleaseTag()
        {
            string tag = Normalize(CompiledReleaseTag);
            if (String.IsNullOrWhiteSpace(tag) || tag.IndexOfAny(new char[] { '\r', '\n', '\0' }) >= 0)
                throw new UpdateException("وسم الإصدار غير صالح.");
            return tag;
        }

        internal static string GetManifestSha256()
        {
            string value = Normalize(CompiledManifestSha256).ToLowerInvariant();
            if (!Regex.IsMatch(value, "^[0-9a-f]{64}$", RegexOptions.CultureInvariant))
                throw new UpdateException("بصمة manifest غير محقونة أو غير صالحة.");
            return value;
        }

        private static string Normalize(string value)
        {
            if (String.IsNullOrWhiteSpace(value) || value.IndexOf("@@", StringComparison.Ordinal) >= 0)
                return String.Empty;
            return value.Trim();
        }
    }

    internal sealed class UpdateForm : Form
    {
        private readonly Label status;
        private readonly Label destination;
        private readonly ProgressBar bar;
        private readonly TextBox log;
        private readonly Button update;
        private readonly Button cancel;
        private CancellationTokenSource cancellation;
        private bool running;

        internal UpdateForm()
        {
            Text = "تحديث Hunyuan3D — Mujassam AI";
            ClientSize = new Size(700, 470);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            RightToLeft = RightToLeft.Yes;
            RightToLeftLayout = true;
            Font = new Font("Segoe UI", 9.5F);
            BackColor = Color.FromArgb(246, 248, 251);

            TableLayoutPanel root = new TableLayoutPanel
            {
                Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 7,
                Padding = new Padding(22, 18, 22, 18)
            };
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 55));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 58));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 52));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 55));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 35));
            root.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            root.RowStyles.Add(new RowStyle(SizeType.Absolute, 54));
            Controls.Add(root);

            root.Controls.Add(new Label
            {
                Text = "تحديث Hunyuan3D لجهاز 8GB VRAM",
                Dock = DockStyle.Fill, Font = new Font("Segoe UI Semibold", 18F, FontStyle.Bold),
                TextAlign = ContentAlignment.MiddleRight
            }, 0, 0);
            root.Controls.Add(new Label
            {
                Text = "ينزّل أجزاء صغيرة من GitHub Release، ويستكمل تلقائيًا بعد انقطاع الإنترنت، " +
                       "ثم يتحقق من SHA-256 قبل تغيير أي ملف.",
                Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleRight
            }, 0, 1);
            destination = new Label
            {
                Dock = DockStyle.Fill, BackColor = Color.White, BorderStyle = BorderStyle.FixedSingle,
                Padding = new Padding(10), TextAlign = ContentAlignment.MiddleRight,
                RightToLeft = RightToLeft.No, AutoEllipsis = true,
                Text = UpdateEngine.GetInstallRoot()
            };
            root.Controls.Add(destination, 0, 2);

            Panel progressPanel = new Panel { Dock = DockStyle.Fill };
            status = new Label { Dock = DockStyle.Top, Height = 25, TextAlign = ContentAlignment.MiddleRight };
            bar = new ProgressBar { Dock = DockStyle.Bottom, Height = 18, Minimum = 0, Maximum = 100 };
            progressPanel.Controls.Add(status);
            progressPanel.Controls.Add(bar);
            root.Controls.Add(progressPanel, 0, 3);
            root.Controls.Add(new Label
            {
                Text = "أغلق Mujassam AI قبل التحديث. الأجزاء المكتملة تبقى محفوظة لإعادة المحاولة.",
                Dock = DockStyle.Fill, ForeColor = Color.FromArgb(83, 94, 110),
                TextAlign = ContentAlignment.MiddleRight
            }, 0, 4);
            log = new TextBox
            {
                Dock = DockStyle.Fill, Multiline = true, ReadOnly = true,
                ScrollBars = ScrollBars.Vertical, BackColor = Color.White,
                RightToLeft = RightToLeft.Yes
            };
            root.Controls.Add(log, 0, 5);
            FlowLayoutPanel buttons = new FlowLayoutPanel
            {
                Dock = DockStyle.Fill, FlowDirection = FlowDirection.RightToLeft, WrapContents = false
            };
            update = new Button { Text = "تنزيل وتحديث", Width = 155, Height = 38 };
            cancel = new Button { Text = "إلغاء", Width = 110, Height = 38, Enabled = false };
            buttons.Controls.Add(update);
            buttons.Controls.Add(cancel);
            root.Controls.Add(buttons, 0, 6);
            status.Text = "جاهز";

            update.Click += BeginUpdate;
            cancel.Click += delegate { if (cancellation != null) cancellation.Cancel(); };
            FormClosing += delegate(object sender, FormClosingEventArgs eventArgs)
            {
                if (!running) return;
                if (MessageBox.Show(this, "هل تريد إلغاء التحديث؟", Text,
                    MessageBoxButtons.YesNo, MessageBoxIcon.Question) != DialogResult.Yes)
                    eventArgs.Cancel = true;
                else if (cancellation != null) cancellation.Cancel();
            };
        }

        private async void BeginUpdate(object sender, EventArgs eventArgs)
        {
            if (running) return;
            running = true;
            update.Enabled = false;
            cancel.Enabled = true;
            bar.Value = 0;
            log.Clear();
            cancellation = new CancellationTokenSource();
            Progress<UpdateProgress> progress = new Progress<UpdateProgress>(delegate(UpdateProgress value)
            {
                bar.Value = Math.Max(0, Math.Min(100, value.Percent));
                if (!String.IsNullOrWhiteSpace(value.Status)) status.Text = value.Status;
                if (!String.IsNullOrWhiteSpace(value.Detail))
                    log.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + value.Detail + Environment.NewLine);
            });
            try
            {
                string root = await UpdateEngine.UpdateAsync(progress, cancellation.Token);
                bar.Value = 100;
                status.Text = "اكتمل التحديث وتم تشغيل Mujassam AI";
                log.AppendText("اكتمل التحديث: " + root + Environment.NewLine);
                System.Media.SystemSounds.Asterisk.Play();
            }
            catch (OperationCanceledException)
            {
                status.Text = "أُلغي التحديث — يمكنك إعادة المحاولة";
            }
            catch (Exception error)
            {
                status.Text = "تعذر التحديث";
                string diagnostic = WriteDiagnostic(error);
                log.AppendText(error.Message + Environment.NewLine + "السجل: " + diagnostic + Environment.NewLine);
                MessageBox.Show(this, "تعذر التحديث. لم يُثبت أي ملف غير متحقق منه.\r\n\r\n" +
                    error.Message + "\r\n\r\nالسجل: " + diagnostic, Text,
                    MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            finally
            {
                running = false;
                cancel.Enabled = false;
                update.Enabled = true;
                update.Text = "إعادة المحاولة";
                cancellation.Dispose();
                cancellation = null;
            }
        }

        private static string WriteDiagnostic(Exception error)
        {
            try
            {
                string folder = Path.Combine(Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData), "MujassamAI", "UpdaterLogs");
                Directory.CreateDirectory(folder);
                string path = Path.Combine(folder, "hunyuan-update-" +
                    DateTime.UtcNow.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + ".log");
                File.WriteAllText(path, error.ToString(), new UTF8Encoding(false));
                return path;
            }
            catch { return Path.GetTempPath(); }
        }
    }

    internal static class UpdateEngine
    {
        private const int BufferSize = 1024 * 1024;
        private const int MaximumManifestBytes = 1024 * 1024;
        private const long MaximumPartBytes = 64L * 1024L * 1024L;
        private const long MaximumArchiveBytes = 4L * 1024L * 1024L * 1024L;
        private const long MaximumExtractedBytes = 4L * 1024L * 1024L * 1024L;
        private const int MaximumEntries = 150000;
        private const int Attempts = 6;
        private const int TimeoutSeconds = 120;
        private static readonly Regex HashPattern = new Regex("^[0-9a-f]{64}$",
            RegexOptions.CultureInvariant);
        private static readonly Regex AssetPattern = new Regex("^[A-Za-z0-9][A-Za-z0-9._-]{0,179}$",
            RegexOptions.CultureInvariant);

        internal static string GetInstallRoot()
        {
            string documents = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            if (String.IsNullOrWhiteSpace(documents))
                throw new UpdateException("تعذر تحديد Documents.");
            return Path.Combine(Path.GetFullPath(documents), "MujassamAI-Portable");
        }

        internal static async Task<string> UpdateAsync(IProgress<UpdateProgress> progress,
            CancellationToken token)
        {
            string installRoot = GetInstallRoot();
            ValidateExistingInstall(installRoot);
            if (Process.GetProcessesByName("MujassamAI").Length > 0)
                throw new UpdateException("أغلق Mujassam AI بالكامل ثم اضغط إعادة المحاولة.");

            Uri releaseBase = UpdaterConfiguration.GetReleaseBaseUri();
            string expectedManifestHash = UpdaterConfiguration.GetManifestSha256();
            Report(progress, 1, "جارٍ تنزيل معلومات الإصدار...", releaseBase.AbsoluteUri);
            string releaseJson = await DownloadTextAsync(new Uri(releaseBase,
                UpdaterConfiguration.ManifestAssetName), expectedManifestHash, token).ConfigureAwait(false);
            ReleaseManifest release = ReleaseManifest.Parse(releaseJson);
            if (!String.Equals(release.ReleaseTag, UpdaterConfiguration.GetReleaseTag(),
                StringComparison.Ordinal))
                throw new UpdateException("لا يطابق manifest الإصدار المثبت داخل المحدّث.");

            string cache = GetCacheRoot(releaseBase, expectedManifestHash);
            Directory.CreateDirectory(cache);
            long completed = 0;
            for (int index = 0; index < release.Archive.Parts.Count; index++)
            {
                PartSpec part = release.Archive.Parts[index];
                string destination = Path.Combine(cache, part.Name);
                Report(progress, Scale(completed, release.Archive.Bytes, 3, 55),
                    "جارٍ تنزيل أجزاء Hunyuan3D...", "الجزء " + (index + 1) + "/" + release.Archive.Parts.Count);
                await DownloadPartWithRetryAsync(new Uri(releaseBase, Uri.EscapeDataString(part.Name)),
                    part, destination, completed, release.Archive.Bytes, progress, token).ConfigureAwait(false);
                completed += part.Bytes;
            }

            string archive = Path.Combine(cache, release.Archive.FileName);
            Report(progress, 56, "جارٍ تجميع التحديث والتحقق منه...", null);
            await AssembleAsync(release.Archive, cache, archive, progress, token).ConfigureAwait(false);

            string installParent = Path.GetDirectoryName(installRoot);
            string staging = Path.Combine(installParent, "MHU-" + Guid.NewGuid().ToString("N").Substring(0, 8));
            string backup = Path.Combine(installParent, "MujassamAI-HunyuanBackup-" +
                Guid.NewGuid().ToString("N").Substring(0, 8));
            Directory.CreateDirectory(staging);
            try
            {
                Report(progress, 66, "جارٍ فك التحديث بأمان...", null);
                PayloadManifest payload = await ExtractAndVerifyAsync(archive, staging, progress, token)
                    .ConfigureAwait(false);
                token.ThrowIfCancellationRequested();
                ValidateExistingInstall(installRoot);
                if (Process.GetProcessesByName("MujassamAI").Length > 0)
                    throw new UpdateException("تم تشغيل Mujassam AI أثناء التحديث. أغلقه وأعد المحاولة.");
                Report(progress, 91, "جارٍ تثبيت الملفات مع حماية rollback...", null);
                CommitWithRollback(staging, installRoot, backup, payload);
            }
            finally
            {
                TryDeleteDirectory(staging);
            }

            TryDeleteDirectory(backup);
            foreach (PartSpec part in release.Archive.Parts)
            {
                TryDeleteFile(Path.Combine(cache, part.Name));
                TryDeleteFile(Path.Combine(cache, part.Name) + ".download");
            }
            TryDeleteFile(archive);
            Report(progress, 98, "جارٍ تشغيل Mujassam AI...", null);
            Process.Start(new ProcessStartInfo
            {
                FileName = Path.Combine(installRoot, "MujassamAI.exe"),
                WorkingDirectory = installRoot,
                UseShellExecute = true
            });
            Report(progress, 100, "اكتمل التحديث", null);
            return installRoot;
        }

        private static void ValidateExistingInstall(string root)
        {
            string[] required = new string[]
            {
                "MujassamAI.exe", Path.Combine("rt", "python.exe"), Path.Combine("app", "worker.py")
            };
            if (!Directory.Exists(root) ||
                (File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0)
                throw new UpdateException("لم أجد تثبيت MujassamAI-Portable الأصلي داخل Documents.");
            foreach (string relative in required)
            {
                string path = Path.Combine(root, relative);
                if (!File.Exists(path) || new FileInfo(path).Length <= 0)
                    throw new UpdateException("التثبيت الأصلي ناقص: " + relative);
            }
        }

        private static string GetCacheRoot(Uri releaseBase, string manifestHash)
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            using (SHA256 sha = SHA256.Create())
            {
                string key = ToHex(sha.ComputeHash(Encoding.UTF8.GetBytes(
                    releaseBase.AbsoluteUri + "|" + manifestHash))).Substring(0, 20);
                return Path.Combine(local, "MujassamAI", "HunyuanUpdateCache", key);
            }
        }

        private static async Task<string> DownloadTextAsync(Uri uri, string expectedHash,
            CancellationToken token)
        {
            byte[] bytes = await DownloadSmallAsync(uri, token).ConfigureAwait(false);
            if (bytes.Length > MaximumManifestBytes || !FixedEquals(HashBytes(bytes), expectedHash))
                throw new UpdateException("فشل تحقق SHA-256 لملف معلومات الإصدار.");
            return new UTF8Encoding(false, true).GetString(bytes).TrimStart('\uFEFF');
        }

        private static async Task<byte[]> DownloadSmallAsync(Uri uri, CancellationToken token)
        {
            HttpWebRequest request = CreateRequest(uri);
            using (token.Register(delegate { TryAbort(request); }))
            using (HttpWebResponse response = await GetResponseAsync(request, token).ConfigureAwait(false))
            {
                ValidateResponse(response);
                using (Stream input = response.GetResponseStream())
                using (MemoryStream output = new MemoryStream())
                {
                    byte[] buffer = new byte[32768];
                    int read;
                    while ((read = await ReadAsync(input, buffer, request, token).ConfigureAwait(false)) > 0)
                    {
                        if (output.Length + read > MaximumManifestBytes)
                            throw new UpdateException("ملف معلومات الإصدار أكبر من الحد الآمن.");
                        output.Write(buffer, 0, read);
                    }
                    return output.ToArray();
                }
            }
        }

        private static async Task DownloadPartWithRetryAsync(Uri uri, PartSpec part, string destination,
            long before, long total, IProgress<UpdateProgress> progress, CancellationToken token)
        {
            if (await IsValidAsync(destination, part.Bytes, part.Sha256, token).ConfigureAwait(false)) return;
            TryDeleteFile(destination);
            Exception last = null;
            for (int attempt = 1; attempt <= Attempts; attempt++)
            {
                try
                {
                    await DownloadPartOnceAsync(uri, part, destination, before, total, progress, token)
                        .ConfigureAwait(false);
                    return;
                }
                catch (OperationCanceledException) { throw; }
                catch (Exception error)
                {
                    last = error;
                    if (attempt == Attempts) break;
                    Report(progress, Scale(before, total, 3, 55),
                        "انقطع التنزيل؛ جارٍ الاستكمال تلقائيًا...",
                        part.Name + " — محاولة " + (attempt + 1));
                    await Task.Delay(TimeSpan.FromSeconds(Math.Min(10, attempt * 2)), token)
                        .ConfigureAwait(false);
                }
            }
            throw new UpdateException("فشل تنزيل " + part.Name + " بعد عدة محاولات.", last);
        }

        private static async Task DownloadPartOnceAsync(Uri uri, PartSpec part, string destination,
            long before, long total, IProgress<UpdateProgress> progress, CancellationToken token)
        {
            string partial = destination + ".download";
            long offset = File.Exists(partial) ? new FileInfo(partial).Length : 0;
            if (offset < 0 || offset > part.Bytes) { TryDeleteFile(partial); offset = 0; }
            if (offset == part.Bytes)
            {
                if (await IsValidAsync(partial, part.Bytes, part.Sha256, token).ConfigureAwait(false))
                { MoveVerified(partial, destination); return; }
                TryDeleteFile(partial); offset = 0;
            }

            HttpWebRequest request = CreateRequest(uri);
            if (offset > 0) request.AddRange((int)offset);
            using (token.Register(delegate { TryAbort(request); }))
            {
                try
                {
                    using (HttpWebResponse response = await GetResponseAsync(request, token).ConfigureAwait(false))
                    {
                        ValidateResponse(response);
                        bool resumed = offset > 0 && response.StatusCode == HttpStatusCode.PartialContent;
                        if (offset > 0 && !resumed) { offset = 0; TryDeleteFile(partial); }
                        FileMode mode = offset > 0 ? FileMode.Append : FileMode.Create;
                        using (Stream input = response.GetResponseStream())
                        using (FileStream output = new FileStream(partial, mode, FileAccess.Write,
                            FileShare.None, BufferSize, true))
                        {
                            long written = offset;
                            byte[] buffer = new byte[BufferSize];
                            int read;
                            while ((read = await ReadAsync(input, buffer, request, token).ConfigureAwait(false)) > 0)
                            {
                                written += read;
                                if (written > part.Bytes) throw new UpdateException("حجم الجزء أكبر من manifest.");
                                await output.WriteAsync(buffer, 0, read, token).ConfigureAwait(false);
                                Report(progress, Scale(before + written, total, 3, 55),
                                    "جارٍ تنزيل أجزاء Hunyuan3D...", null);
                            }
                            output.Flush(true);
                            if (written != part.Bytes) throw new UpdateException("انقطع الجزء قبل اكتماله.");
                        }
                    }
                }
                catch (WebException error)
                {
                    if (token.IsCancellationRequested) throw new OperationCanceledException(token);
                    throw new UpdateException("فشل الاتصال بـGitHub: " + error.Status, error);
                }
            }
            if (!await IsValidAsync(partial, part.Bytes, part.Sha256, token).ConfigureAwait(false))
            { TryDeleteFile(partial); throw new UpdateException("فشل SHA-256 للجزء " + part.Name); }
            MoveVerified(partial, destination);
        }

        private static async Task AssembleAsync(ArchiveSpec archive, string cache, string output,
            IProgress<UpdateProgress> progress, CancellationToken token)
        {
            if (await IsValidAsync(output, archive.Bytes, archive.Sha256, token).ConfigureAwait(false)) return;
            string temporary = output + ".assembling";
            TryDeleteFile(temporary);
            long written = 0;
            using (SHA256 whole = SHA256.Create())
            using (FileStream target = new FileStream(temporary, FileMode.Create, FileAccess.Write,
                FileShare.None, BufferSize, true))
            {
                byte[] buffer = new byte[BufferSize];
                foreach (PartSpec part in archive.Parts)
                {
                    string path = Path.Combine(cache, part.Name);
                    using (FileStream source = File.OpenRead(path))
                    {
                        int read;
                        while ((read = await source.ReadAsync(buffer, 0, buffer.Length, token)
                            .ConfigureAwait(false)) > 0)
                        {
                            written += read;
                            whole.TransformBlock(buffer, 0, read, null, 0);
                            await target.WriteAsync(buffer, 0, read, token).ConfigureAwait(false);
                            Report(progress, Scale(written, archive.Bytes, 56, 65),
                                "جارٍ تجميع التحديث والتحقق منه...", null);
                        }
                    }
                }
                whole.TransformFinalBlock(new byte[0], 0, 0);
                target.Flush(true);
                if (written != archive.Bytes || !FixedEquals(ToHex(whole.Hash), archive.Sha256))
                { TryDeleteFile(temporary); throw new UpdateException("فشل تحقق ZIP النهائي."); }
            }
            MoveVerified(temporary, output);
        }

        private static async Task<PayloadManifest> ExtractAndVerifyAsync(string archive, string staging,
            IProgress<UpdateProgress> progress, CancellationToken token)
        {
            long extracted = 0;
            HashSet<string> names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            using (ZipArchive zip = ZipFile.OpenRead(archive))
            {
                if (zip.Entries.Count > MaximumEntries) throw new UpdateException("عدد ملفات ZIP أكبر من الحد الآمن.");
                long total = 0;
                foreach (ZipArchiveEntry entry in zip.Entries)
                {
                    string destination = SafeDestination(staging, entry.FullName);
                    if (!names.Add(destination)) throw new UpdateException("مسار مكرر داخل ZIP.");
                    if (!IsDirectory(entry))
                    {
                        total += entry.Length;
                        if (total > MaximumExtractedBytes) throw new UpdateException("حجم ZIP المفكوك أكبر من الحد الآمن.");
                    }
                }
                byte[] buffer = new byte[BufferSize];
                foreach (ZipArchiveEntry entry in zip.Entries)
                {
                    token.ThrowIfCancellationRequested();
                    string destination = SafeDestination(staging, entry.FullName);
                    if (IsDirectory(entry)) { Directory.CreateDirectory(destination); continue; }
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    using (Stream source = entry.Open())
                    using (FileStream target = new FileStream(destination, FileMode.CreateNew,
                        FileAccess.Write, FileShare.None, BufferSize, true))
                    {
                        int read;
                        while ((read = await source.ReadAsync(buffer, 0, buffer.Length, token)
                            .ConfigureAwait(false)) > 0)
                        {
                            extracted += read;
                            await target.WriteAsync(buffer, 0, read, token).ConfigureAwait(false);
                            Report(progress, Scale(extracted, Math.Max(1, total), 66, 78),
                                "جارٍ فك التحديث بأمان...", null);
                        }
                        target.Flush(true);
                    }
                }
            }

            string manifestPath = Path.Combine(staging, "update-manifest.json");
            if (!File.Exists(manifestPath)) throw new UpdateException("ZIP لا يحتوي update-manifest.json.");
            PayloadManifest payload = PayloadManifest.Parse(File.ReadAllText(manifestPath, Encoding.UTF8), staging);
            long checkedBytes = 0;
            foreach (PayloadFile file in payload.Files)
            {
                token.ThrowIfCancellationRequested();
                string path = SafeDestination(staging, file.Path);
                if (!await IsValidAsync(path, file.Bytes, file.Sha256, token).ConfigureAwait(false))
                    throw new UpdateException("فشل تحقق الملف: " + file.Path);
                checkedBytes += file.Bytes;
                Report(progress, Scale(checkedBytes, payload.TotalBytes, 79, 90),
                    "جارٍ التحقق من جميع ملفات التحديث...", null);
            }
            HashSet<string> expected = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            expected.Add(Path.GetFullPath(manifestPath));
            foreach (PayloadFile file in payload.Files) expected.Add(SafeDestination(staging, file.Path));
            foreach (string file in Directory.GetFiles(staging, "*", SearchOption.AllDirectories))
                if (!expected.Contains(Path.GetFullPath(file))) throw new UpdateException("ملف زائد داخل ZIP.");
            return payload;
        }

        private static void CommitWithRollback(string staging, string install, string backup,
            PayloadManifest payload)
        {
            List<string> backed = new List<string>();
            List<string> installed = new List<string>();
            Directory.CreateDirectory(backup);
            try
            {
                foreach (PayloadFile file in payload.Files)
                {
                    string destination = SafeDestination(install, file.Path);
                    RejectReparseParents(install, destination);
                    if (!File.Exists(destination)) continue;
                    string saved = SafeDestination(backup, file.Path);
                    Directory.CreateDirectory(Path.GetDirectoryName(saved));
                    File.Move(destination, saved);
                    backed.Add(file.Path);
                }
                foreach (PayloadFile file in payload.Files)
                {
                    string source = SafeDestination(staging, file.Path);
                    string destination = SafeDestination(install, file.Path);
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    File.Move(source, destination);
                    installed.Add(file.Path);
                }
            }
            catch (Exception error)
            {
                foreach (string relative in installed)
                    TryDeleteFile(SafeDestination(install, relative));
                for (int index = backed.Count - 1; index >= 0; index--)
                {
                    string relative = backed[index];
                    string saved = SafeDestination(backup, relative);
                    string destination = SafeDestination(install, relative);
                    try
                    {
                        Directory.CreateDirectory(Path.GetDirectoryName(destination));
                        if (File.Exists(destination)) File.Delete(destination);
                        File.Move(saved, destination);
                    }
                    catch { }
                }
                throw new UpdateException("فشل نسخ التحديث وتمت محاولة استعادة الملفات القديمة. النسخة الاحتياطية: " + backup, error);
            }
        }

        private static void RejectReparseParents(string root, string destination)
        {
            string current = Path.GetDirectoryName(destination);
            string normalizedRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            while (!String.IsNullOrWhiteSpace(current) &&
                current.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase))
            {
                if (Directory.Exists(current) &&
                    (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new UpdateException("رفض المحدّث مجلد link داخل التثبيت.");
                if (String.Equals(current.TrimEnd(Path.DirectorySeparatorChar),
                    root.TrimEnd(Path.DirectorySeparatorChar), StringComparison.OrdinalIgnoreCase)) break;
                current = Path.GetDirectoryName(current);
            }
        }

        private static string SafeDestination(string root, string relative)
        {
            if (String.IsNullOrWhiteSpace(relative) || relative.IndexOf('\\') >= 0 ||
                relative.StartsWith("/", StringComparison.Ordinal) || relative.IndexOf("//") >= 0)
                throw new UpdateException("مسار غير آمن داخل التحديث.");
            string canonical = relative.EndsWith("/", StringComparison.Ordinal)
                ? relative.Substring(0, relative.Length - 1) : relative;
            foreach (string segment in canonical.Split('/'))
            {
                if (segment.Length == 0 || segment == "." || segment == ".." ||
                    segment.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0)
                    throw new UpdateException("اسم غير آمن داخل التحديث.");
            }
            string normalizedRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            string destination = Path.GetFullPath(Path.Combine(normalizedRoot,
                canonical.Replace('/', Path.DirectorySeparatorChar)));
            if (!destination.StartsWith(normalizedRoot, StringComparison.OrdinalIgnoreCase))
                throw new UpdateException("حاول ZIP الكتابة خارج مجلد التحديث.");
            return destination;
        }

        private static bool IsDirectory(ZipArchiveEntry entry)
        {
            if ((entry.ExternalAttributes & (int)FileAttributes.ReparsePoint) != 0 ||
                ((entry.ExternalAttributes >> 16) & 0xF000) == 0xA000)
                throw new UpdateException("لا يسمح بروابط رمزية داخل ZIP.");
            return entry.FullName.EndsWith("/", StringComparison.Ordinal);
        }

        private static HttpWebRequest CreateRequest(Uri uri)
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(uri);
            request.UserAgent = "MujassamAI-HunyuanUpdater/1.0";
            request.Accept = "application/octet-stream, application/json;q=0.9, */*;q=0.5";
            request.Headers[HttpRequestHeader.AcceptEncoding] = "identity";
            request.AllowAutoRedirect = true;
            request.MaximumAutomaticRedirections = 10;
            request.AutomaticDecompression = DecompressionMethods.None;
            request.Timeout = 30000;
            request.ReadWriteTimeout = 60000;
            return request;
        }

        private static async Task<HttpWebResponse> GetResponseAsync(HttpWebRequest request,
            CancellationToken userToken)
        {
            using (CancellationTokenSource deadline = CancellationTokenSource.CreateLinkedTokenSource(userToken))
            {
                deadline.CancelAfter(TimeSpan.FromSeconds(TimeoutSeconds));
                using (deadline.Token.Register(delegate { TryAbort(request); }))
                {
                    try { return (HttpWebResponse)await request.GetResponseAsync().ConfigureAwait(false); }
                    catch (Exception error)
                    {
                        if (userToken.IsCancellationRequested) throw new OperationCanceledException(userToken);
                        if (deadline.IsCancellationRequested) throw new UpdateException("انتهت مهلة GitHub.", error);
                        throw;
                    }
                }
            }
        }

        private static async Task<int> ReadAsync(Stream input, byte[] buffer, HttpWebRequest request,
            CancellationToken token)
        {
            using (CancellationTokenSource stall = CancellationTokenSource.CreateLinkedTokenSource(token))
            {
                stall.CancelAfter(TimeSpan.FromSeconds(TimeoutSeconds));
                using (stall.Token.Register(delegate { TryAbort(request); }))
                {
                    try { return await input.ReadAsync(buffer, 0, buffer.Length, stall.Token).ConfigureAwait(false); }
                    catch (Exception error)
                    {
                        if (token.IsCancellationRequested) throw new OperationCanceledException(token);
                        if (stall.IsCancellationRequested) throw new UpdateException("توقف التنزيل لأكثر من دقيقتين.", error);
                        throw;
                    }
                }
            }
        }

        private static void ValidateResponse(HttpWebResponse response)
        {
            string host = response.ResponseUri == null ? null : response.ResponseUri.Host;
            if (response.ResponseUri == null ||
                !String.Equals(response.ResponseUri.Scheme, "https", StringComparison.OrdinalIgnoreCase) ||
                !(String.Equals(host, "github.com", StringComparison.OrdinalIgnoreCase) ||
                  (host != null && host.EndsWith(".githubusercontent.com", StringComparison.OrdinalIgnoreCase))))
                throw new UpdateException("رفض المحدّث إعادة توجيه خارج نطاقات GitHub.");
            if (response.StatusCode != HttpStatusCode.OK && response.StatusCode != HttpStatusCode.PartialContent)
                throw new UpdateException("حالة HTTP غير متوقعة: " + (int)response.StatusCode);
        }

        private static async Task<bool> IsValidAsync(string path, long bytes, string sha,
            CancellationToken token)
        {
            if (!File.Exists(path) || new FileInfo(path).Length != bytes) return false;
            using (SHA256 hash = SHA256.Create())
            using (FileStream input = File.OpenRead(path))
            {
                byte[] buffer = new byte[BufferSize];
                int read;
                while ((read = await input.ReadAsync(buffer, 0, buffer.Length, token)
                    .ConfigureAwait(false)) > 0) hash.TransformBlock(buffer, 0, read, null, 0);
                hash.TransformFinalBlock(new byte[0], 0, 0);
                return FixedEquals(ToHex(hash.Hash), sha);
            }
        }

        private static string HashBytes(byte[] bytes)
        {
            using (SHA256 sha = SHA256.Create()) return ToHex(sha.ComputeHash(bytes));
        }

        private static string ToHex(byte[] value)
        {
            StringBuilder result = new StringBuilder(value.Length * 2);
            foreach (byte item in value) result.Append(item.ToString("x2", CultureInfo.InvariantCulture));
            return result.ToString();
        }

        private static bool FixedEquals(string left, string right)
        {
            if (left == null || right == null || left.Length != right.Length) return false;
            int difference = 0;
            for (int index = 0; index < left.Length; index++) difference |= left[index] ^ right[index];
            return difference == 0;
        }

        private static int Scale(long done, long total, int start, int end)
        {
            if (total <= 0) return end;
            return start + (int)Math.Round((end - start) * Math.Max(0.0,
                Math.Min(1.0, done / (double)total)));
        }

        private static void Report(IProgress<UpdateProgress> progress, int percent, string status,
            string detail)
        {
            if (progress != null) progress.Report(new UpdateProgress(percent, status, detail));
        }

        private static void MoveVerified(string source, string destination)
        {
            if (File.Exists(destination)) File.Delete(destination);
            File.Move(source, destination);
        }

        private static void TryDeleteFile(string path)
        { try { if (File.Exists(path)) File.Delete(path); } catch { } }

        private static void TryDeleteDirectory(string path)
        { try { if (Directory.Exists(path)) Directory.Delete(path, true); } catch { } }

        private static void TryAbort(HttpWebRequest request)
        { try { request.Abort(); } catch { } }

        internal sealed class ReleaseManifest
        {
            internal string ReleaseTag;
            internal ArchiveSpec Archive;

            internal static ReleaseManifest Parse(string json)
            {
                ReleaseWire wire;
                try
                {
                    JavaScriptSerializer serializer = new JavaScriptSerializer
                    {
                        MaxJsonLength = 16 * 1024 * 1024,
                        RecursionLimit = 256
                    };
                    wire = serializer.Deserialize<ReleaseWire>(json);
                }
                catch (Exception error) { throw new UpdateException("JSON الإصدار غير صالح.", error); }
                if (wire == null || wire.schema_version != 1 || wire.source_run_id != 33030801098L ||
                    String.IsNullOrWhiteSpace(wire.release_tag) || wire.archive == null)
                    throw new UpdateException("بنية manifest الإصدار غير مدعومة.");
                ArchiveWire item = wire.archive;
                if (item.file_name != "MujassamAI-Hunyuan-Update-v1.zip" || item.bytes <= 0 ||
                    item.bytes > MaximumArchiveBytes || !HashPattern.IsMatch(item.sha256 ?? "") ||
                    item.parts == null || item.parts.Count == 0 || item.parts.Count > 128)
                    throw new UpdateException("وصف ZIP غير صالح.");
                List<PartSpec> parts = new List<PartSpec>();
                long sum = 0;
                for (int index = 0; index < item.parts.Count; index++)
                {
                    PartWire part = item.parts[index];
                    string expected = item.file_name + ".part" + (index + 1).ToString("D3",
                        CultureInfo.InvariantCulture);
                    if (part == null || part.name != expected || !AssetPattern.IsMatch(part.name ?? "") ||
                        part.bytes <= 0 || part.bytes > MaximumPartBytes ||
                        !HashPattern.IsMatch(part.sha256 ?? ""))
                        throw new UpdateException("وصف جزء ZIP غير صالح.");
                    sum += part.bytes;
                    parts.Add(new PartSpec(part.name, part.bytes, part.sha256.ToLowerInvariant()));
                }
                if (sum != item.bytes) throw new UpdateException("مجموع أجزاء ZIP غير صحيح.");
                return new ReleaseManifest
                {
                    ReleaseTag = wire.release_tag,
                    Archive = new ArchiveSpec(item.file_name, item.bytes,
                        item.sha256.ToLowerInvariant(), parts)
                };
            }
        }

        internal sealed class PayloadManifest
        {
            internal List<PayloadFile> Files;
            internal long TotalBytes;

            internal static PayloadManifest Parse(string json, string staging)
            {
                PayloadWire wire;
                try
                {
                    JavaScriptSerializer serializer = new JavaScriptSerializer
                    {
                        MaxJsonLength = 16 * 1024 * 1024,
                        RecursionLimit = 256
                    };
                    wire = serializer.Deserialize<PayloadWire>(json);
                }
                catch (Exception error) { throw new UpdateException("update-manifest.json غير صالح.", error); }
                if (wire == null || wire.schema_version != 1 ||
                    wire.archive != "MujassamAI-Hunyuan-Update-v1.zip" || wire.files == null ||
                    wire.files.Count == 0 || wire.files.Count > MaximumEntries)
                    throw new UpdateException("بنية update-manifest.json غير مدعومة.");
                List<PayloadFile> files = new List<PayloadFile>();
                HashSet<string> unique = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                long total = 0;
                foreach (PayloadFileWire item in wire.files)
                {
                    if (item == null || item.bytes < 0 || !HashPattern.IsMatch(item.sha256 ?? ""))
                        throw new UpdateException("ملف غير صالح في update manifest.");
                    SafeDestination(staging, item.path);
                    if (!unique.Add(item.path)) throw new UpdateException("مسار مكرر في update manifest.");
                    total += item.bytes;
                    if (total > MaximumExtractedBytes) throw new UpdateException("حجم التحديث أكبر من الحد الآمن.");
                    files.Add(new PayloadFile(item.path, item.bytes, item.sha256.ToLowerInvariant()));
                }
                string[] required = new string[]
                {
                    "MujassamAI.exe", "app/worker.py",
                    "app/engines/hunyuan2/hunyuan2_worker.py",
                    "app/engines/hunyuan2/ENGINE-MANIFEST.json"
                };
                foreach (string path in required)
                    if (!unique.Contains(path)) throw new UpdateException("التحديث ناقص: " + path);
                return new PayloadManifest { Files = files, TotalBytes = total };
            }
        }

        private sealed class ReleaseWire
        { public int schema_version { get; set; } public string release_tag { get; set; }
          public long source_run_id { get; set; } public ArchiveWire archive { get; set; } }
        private sealed class ArchiveWire
        { public string file_name { get; set; } public long bytes { get; set; }
          public string sha256 { get; set; } public List<PartWire> parts { get; set; } }
        private sealed class PartWire
        { public string name { get; set; } public long bytes { get; set; } public string sha256 { get; set; } }
        private sealed class PayloadWire
        { public int schema_version { get; set; } public string archive { get; set; }
          public List<PayloadFileWire> files { get; set; } }
        private sealed class PayloadFileWire
        { public string path { get; set; } public long bytes { get; set; } public string sha256 { get; set; } }
    }

    internal sealed class ArchiveSpec
    {
        internal readonly string FileName; internal readonly long Bytes;
        internal readonly string Sha256; internal readonly List<PartSpec> Parts;
        internal ArchiveSpec(string fileName, long bytes, string sha256, List<PartSpec> parts)
        { FileName = fileName; Bytes = bytes; Sha256 = sha256; Parts = parts; }
    }

    internal sealed class PartSpec
    {
        internal readonly string Name; internal readonly long Bytes; internal readonly string Sha256;
        internal PartSpec(string name, long bytes, string sha256)
        { Name = name; Bytes = bytes; Sha256 = sha256; }
    }

    internal sealed class PayloadFile
    {
        internal readonly string Path; internal readonly long Bytes; internal readonly string Sha256;
        internal PayloadFile(string path, long bytes, string sha256)
        { Path = path; Bytes = bytes; Sha256 = sha256; }
    }

    internal sealed class UpdateProgress
    {
        internal readonly int Percent; internal readonly string Status; internal readonly string Detail;
        internal UpdateProgress(int percent, string status, string detail)
        { Percent = percent; Status = status; Detail = detail; }
    }

    internal sealed class UpdateException : Exception
    {
        internal UpdateException(string message) : base(message) { }
        internal UpdateException(string message, Exception inner) : base(message, inner) { }
    }
}
