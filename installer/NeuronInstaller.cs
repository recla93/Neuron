using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace NeuronInstaller
{
    internal static class Program
    {
        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new InstallerForm(args));
        }
    }

    internal sealed class InstallerForm : Form
    {
        private readonly Label sourceLabel;
        private readonly TextBox logBox;
        private readonly Button installButton;
        private readonly Button uninstallButton;
        private readonly CheckBox dataCheck;
        private readonly CheckBox llmCheck;
        private readonly ProgressBar progress;
        private string scriptPath;
        private string installedPython;
        private string installedDir;

        private static readonly Color Background = Color.FromArgb(13, 16, 35);
        private static readonly Color Surface = Color.FromArgb(31, 36, 62);
        private static readonly Color Accent = Color.FromArgb(122, 162, 247);
        private static readonly Color Foreground = Color.FromArgb(220, 225, 250);
        private static readonly Color Muted = Color.FromArgb(145, 153, 190);

        public InstallerForm(string[] args)
        {
            Text = "Neuron Installer";
            BackColor = Background;
            ForeColor = Foreground;
            MinimumSize = new Size(700, 480);
            Size = new Size(820, 590);
            StartPosition = FormStartPosition.CenterScreen;
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;

            var body = new Panel { Dock = DockStyle.Fill, Padding = new Padding(24, 18, 24, 20), BackColor = Background };
            Controls.Add(body);

            var header = new Panel { Dock = DockStyle.Top, Height = 116, BackColor = Surface, Padding = new Padding(24, 18, 24, 12) };
            Controls.Add(header);

            var title = new Label { Text = "NEURON", AutoSize = true, Font = new Font("Segoe UI", 24, FontStyle.Bold), ForeColor = Foreground, Location = new Point(24, 16) };
            header.Controls.Add(title);
            var subtitle = new Label { Text = "Install the Control Center", AutoSize = true, Font = new Font("Segoe UI", 10, FontStyle.Bold), ForeColor = Accent, Location = new Point(28, 63) };
            header.Controls.Add(subtitle);

            var controlsPanel = new Panel { Dock = DockStyle.Top, Height = 130, BackColor = Background };

            var intro = new Label { Text = "This one-time setup installs Neuron, creates the Desktop shortcut, and keeps the rest of your work inside the GUI.", Dock = DockStyle.Top, Height = 42, ForeColor = Muted, Font = new Font("Segoe UI", 10), AutoEllipsis = true };
            controlsPanel.Controls.Add(intro);

            sourceLabel = new Label { Text = "Installer source: searching\u2026", Dock = DockStyle.Top, Height = 34, ForeColor = Muted, Font = new Font("Segoe UI", 9), AutoEllipsis = true };
            controlsPanel.Controls.Add(sourceLabel);

            var buttons = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 42, BackColor = Background, FlowDirection = FlowDirection.LeftToRight };
            controlsPanel.Controls.Add(buttons);
            installButton = MakeButton("Install Neuron", true);
            installButton.Click += delegate { StartInstall(); };
            buttons.Controls.Add(installButton);
            uninstallButton = MakeButton("Uninstall", false);
            uninstallButton.Click += delegate { RunRecovery("uninstall"); };
            buttons.Controls.Add(uninstallButton);

            dataCheck = new CheckBox { Text = "Also delete Neuron memory data", AutoSize = true, ForeColor = Muted, BackColor = Background, Checked = false, Enabled = false, Margin = new Padding(0, 7, 0, 0) };
            dataCheck.CheckedChanged += delegate { uninstallButton.Text = dataCheck.Checked ? "Uninstall + data" : "Uninstall"; };
            controlsPanel.Controls.Add(dataCheck);

            llmCheck = new CheckBox { Text = "Also install standalone LLM providers (for local chat, optional)", AutoSize = true, ForeColor = Muted, BackColor = Background, Checked = false, Margin = new Padding(0, 2, 0, 0) };
            controlsPanel.Controls.Add(llmCheck);

            logBox = new TextBox { Dock = DockStyle.Fill, Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BackColor = Color.FromArgb(9, 11, 24), ForeColor = Foreground, BorderStyle = BorderStyle.FixedSingle, Font = new Font("Consolas", 9), WordWrap = false, Margin = new Padding(0, 14, 0, 0) };
            body.Controls.Add(logBox);

            progress = new ProgressBar { Dock = DockStyle.Bottom, Height = 12, Style = ProgressBarStyle.Marquee, MarqueeAnimationSpeed = 28, Visible = false };
            body.Controls.Add(progress);

            body.Controls.Add(controlsPanel);

            logBox.AppendText("Neuron Installer starting...\r\n");

            Shown += delegate
            {
                try { FindSource(args); }
                catch (Exception ex)
                {
                    sourceLabel.Text = "Startup check failed \u2014 choose the project folder.";
                    installButton.Enabled = true;
                    Append("ERROR during startup check: " + ex.Message + "\r\n");
                }
            };
        }

        private Button MakeButton(string text, bool primary)
        {
            var button = new Button { Text = text, AutoSize = true, Height = 30, FlatStyle = FlatStyle.Flat, Font = new Font("Segoe UI", 9, FontStyle.Bold), BackColor = primary ? Accent : Surface, ForeColor = primary ? Background : Foreground, FlatAppearance = { BorderSize = 0 }, Padding = new Padding(14, 4, 14, 4), Margin = new Padding(0, 0, 8, 0) };
            return button;
        }

        private void FindSource(string[] args)
        {
            DetectInstalled();
            if (args != null && args.Length > 0 && File.Exists(Path.Combine(args[0], "install.ps1")))
                scriptPath = Path.Combine(args[0], "install.ps1");

            if (String.IsNullOrEmpty(scriptPath))
            {
                var start = Path.GetDirectoryName(Application.ExecutablePath);
                for (var dir = new DirectoryInfo(start); dir != null && dir.Parent != null; dir = dir.Parent)
                {
                    var candidate = Path.Combine(dir.FullName, "install.ps1");
                    if (File.Exists(candidate)) { scriptPath = candidate; break; }
                }
            }

            if (String.IsNullOrEmpty(scriptPath))
            {
                sourceLabel.Text = "Installer source not found — click Install to choose the project folder.";
                Append("install.ps1 not found next to this .exe. Click 'Install Neuron' and pick the Neuron project folder.\r\n");
            }
            else
            {
                sourceLabel.Text = "Installer source: " + scriptPath;
                Append("Ready. The installer will create a Desktop shortcut for the Control Center.\r\n");
            }
        }

        private bool PromptForSource()
        {
            // Fallback when install.ps1 isn't found near the exe: let the user
            // point at the project folder instead of failing silently.
            using (var dialog = new FolderBrowserDialog())
            {
                dialog.Description = "Choose the Neuron project folder (the one containing install.ps1)";
                dialog.ShowNewFolderButton = false;
                if (dialog.ShowDialog(this) != DialogResult.OK) return false;
                var candidate = Path.Combine(dialog.SelectedPath, "install.ps1");
                if (!File.Exists(candidate))
                {
                    MessageBox.Show(this,
                        "install.ps1 was not found in:\r\n" + dialog.SelectedPath +
                        "\r\n\r\nPick the folder that contains install.ps1.",
                        "Neuron Installer", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                    Append("No install.ps1 in the chosen folder: " + dialog.SelectedPath + "\r\n");
                    return false;
                }
                scriptPath = candidate;
                sourceLabel.Text = "Installer source: " + scriptPath;
                Append("Source set: " + scriptPath + "\r\n");
                return true;
            }
        }

        private void DetectInstalled()
        {
            var local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            installedDir = Path.Combine(local, "gray-matter");
            installedPython = Path.Combine(installedDir, ".venv", "Scripts", "python.exe");
            var present = File.Exists(installedPython);
            uninstallButton.Enabled = present;
            dataCheck.Enabled = present;
            if (present)
            {
                installButton.Text = "Run installer again";
                Append("Existing installation detected. Recovery actions are available below.\r\n");
                Append("Control Center: installed\r\n");
            }
        }

        private void StartInstall()
        {
            // No source yet (exe not next to install.ps1)? Ask for the folder
            // instead of doing nothing — the old silent return looked broken.
            if (String.IsNullOrEmpty(scriptPath) || !File.Exists(scriptPath))
            {
                if (!PromptForSource()) return;
            }
            installButton.Enabled = false;
            uninstallButton.Enabled = false;
            llmCheck.Enabled = false;
            progress.Visible = true;
            Append("Starting Neuron installation\u2026\r\n\r\n");

            var powershell = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "WindowsPowerShell\\v1.0\\powershell.exe");
            if (!File.Exists(powershell)) powershell = "pwsh.exe";
            var psArgs = "-NoProfile -ExecutionPolicy Bypass -File \"" + scriptPath + "\" -Yes";
            if (llmCheck.Checked) psArgs += " -WithLlmProviders";
            var info = new ProcessStartInfo
            {
                FileName = powershell,
                Arguments = psArgs,
                WorkingDirectory = Path.GetDirectoryName(scriptPath),
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            try
            {
                var process = new Process { StartInfo = info, EnableRaisingEvents = true };
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Append(e.Data + "\r\n"); };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Append(e.Data + "\r\n"); };
                process.Exited += delegate { BeginInvoke((Action)delegate { FinishInstall(process.ExitCode); }); };
                process.Start();
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                Append("ERROR: " + ex.Message + "\r\n");
                FinishInstall(1);
            }
        }

        private void RunRecovery(string action)
        {
            if (String.IsNullOrEmpty(installedPython) || !File.Exists(installedPython)) return;
            if (action == "uninstall")
            {
                var warning = dataCheck.Checked
                    ? "Remove Neuron, its registrations, and its local memory data? This cannot be undone."
                    : "Remove Neuron and its registrations? Memory data will be kept.";
                if (MessageBox.Show(this, warning, "Neuron recovery", MessageBoxButtons.YesNo, MessageBoxIcon.Warning) != DialogResult.Yes) return;
                var script = Path.Combine(Path.GetDirectoryName(scriptPath ?? "") ?? "", "scripts", "uninstall.ps1");
                if (!File.Exists(script)) script = Path.Combine(installedDir, "scripts", "uninstall.ps1");
                if (!File.Exists(script))
                {
                    var fallback = "-m neuron setup --uninstall --yes" + (dataCheck.Checked ? " --purge-data" : "");
                    RunProcess(installedPython, fallback, installedDir, "Uninstall (built-in fallback)");
                    return;
                }
                var args = "-NoProfile -ExecutionPolicy Bypass -File \"" + script + "\" -Yes" + (dataCheck.Checked ? " -Data" : "");
                RunProcess("powershell.exe", args, Path.GetDirectoryName(script), "Uninstall");
                return;
            }
            var setupArgs = "-m neuron setup --status";
            RunProcess(installedPython, setupArgs, installedDir, "Status");
        }

        private void RunProcess(string fileName, string arguments, string workingDirectory, string label)
        {
            installButton.Enabled = false;
            uninstallButton.Enabled = false;
            llmCheck.Enabled = false;
            dataCheck.Enabled = false;
            progress.Visible = true;
            Append("\r\n[" + label + "] starting\u2026\r\n");
            var info = new ProcessStartInfo
            {
                FileName = fileName, Arguments = arguments, WorkingDirectory = workingDirectory,
                UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true
            };
            try
            {
                var process = new Process { StartInfo = info, EnableRaisingEvents = true };
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Append(e.Data + "\r\n"); };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { if (e.Data != null) Append(e.Data + "\r\n"); };
                process.Exited += delegate { BeginInvoke((Action)delegate
                {
                    progress.Visible = false;
                    Append("\r\n[" + label + "] " + (process.ExitCode == 0 ? "completed." : "failed \u2014 see the log above.") + "\r\n");
                    DetectInstalled();
                    installButton.Enabled = true;
                    uninstallButton.Enabled = true;
                }); };
                process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                progress.Visible = false;
                Append("ERROR: " + ex.Message + "\r\n");
                DetectInstalled();
                installButton.Enabled = true;
                uninstallButton.Enabled = true;
            }
        }

        private void FinishInstall(int exitCode)
        {
            progress.Visible = false;
            if (exitCode == 0)
            {
                Append("\r\nInstallation complete. Opening the Control Center\u2026\r\n");
                var gui = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "gray-matter", ".venv", "Scripts", "python.exe");
                if (File.Exists(gui))
                {
                    try
                    {
                        Process.Start(new ProcessStartInfo
                        {
                            FileName = gui,
                            Arguments = "-m gray_matter.cli gui",
                            UseShellExecute = false,
                            CreateNoWindow = true
                        });
                    }
                    catch { /* best effort — desktop shortcut is the fallback */ }
                }
                installButton.Text = "Installed";
                installButton.Enabled = false;
            }
            else
            {
                Append("\r\nInstallation failed. Read the log above, fix the reported issue, and try again.\r\n");
                installButton.Enabled = true;
            }
            uninstallButton.Enabled = true;
            llmCheck.Enabled = true;
            DetectInstalled();
        }

        private void Append(string text)
        {
            if (IsDisposed) return;
            if (InvokeRequired) { BeginInvoke((Action)delegate { Append(text); }); return; }
            logBox.AppendText(text);
            logBox.SelectionStart = logBox.TextLength;
            logBox.ScrollToCaret();
        }
    }
}
