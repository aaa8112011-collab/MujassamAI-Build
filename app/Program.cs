using System;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace MujassamPortable
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            if (args != null && args.Length > 0 &&
                String.Equals(args[0], "--self-test", StringComparison.OrdinalIgnoreCase))
            {
                return RunSelfTest(args);
            }

            Application.SetUnhandledExceptionMode(UnhandledExceptionMode.CatchException);
            Application.ThreadException += OnThreadException;
            AppDomain.CurrentDomain.UnhandledException += OnUnhandledException;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
            return 0;
        }

        private static int RunSelfTest(string[] args)
        {
            string reportPath = args.Length > 1
                ? Path.GetFullPath(args[1])
                : Path.Combine(Path.GetTempPath(), "MujassamPortable-self-test.txt");
            try
            {
                string missing = MainForm.ValidatePortableLayout();
                StringBuilder report = new StringBuilder();
                report.AppendLine("Mujassam Portable GUI self-test");
                report.AppendLine("BaseDirectory=" + AppDomain.CurrentDomain.BaseDirectory);
                report.AppendLine("Is64BitOS=" + Environment.Is64BitOperatingSystem);
                report.AppendLine("Is64BitProcess=" + Environment.Is64BitProcess);
                report.AppendLine("PortableLayout=" + (String.IsNullOrEmpty(missing) ? "OK" : "MISSING"));
                if (!String.IsNullOrEmpty(missing))
                    report.AppendLine(missing);
                EnsureParentDirectory(reportPath);
                File.WriteAllText(reportPath, report.ToString(), new UTF8Encoding(false));
                return String.IsNullOrEmpty(missing) && Environment.Is64BitProcess ? 0 : 10;
            }
            catch (Exception ex)
            {
                try
                {
                    EnsureParentDirectory(reportPath);
                    File.WriteAllText(reportPath, ex.ToString(), new UTF8Encoding(false));
                }
                catch
                {
                }
                return 20;
            }
        }

        private static void OnThreadException(object sender, ThreadExceptionEventArgs e)
        {
            ShowFatalError(e.Exception);
        }

        private static void EnsureParentDirectory(string path)
        {
            string parent = Path.GetDirectoryName(path);
            if (!String.IsNullOrWhiteSpace(parent))
                Directory.CreateDirectory(parent);
        }

        private static void OnUnhandledException(object sender, UnhandledExceptionEventArgs e)
        {
            ShowFatalError(e.ExceptionObject as Exception ?? new Exception("Unhandled application error."));
        }

        private static void ShowFatalError(Exception error)
        {
            string logPath = Path.Combine(Path.GetTempPath(), "MujassamPortable-startup-error.log");
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
                    "حدث خطأ غير متوقع في الواجهة.\r\n\r\nتم حفظ التفاصيل في:\r\n" + logPath,
                    "Mujassam AI",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            }
            catch
            {
            }
        }
    }
}
