using System.Diagnostics;
using System.Net.Http;
using System.Text.Json;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

namespace ResearchHub;

internal static class Program
{
    [STAThread]
    private static int Main(string[] args)
    {
        ApplicationConfiguration.Initialize();
        var root = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ResearchHub");
        var path = Environment.GetEnvironmentVariable("RESEARCH_HUB_HOME") ?? Path.Combine(root, "workspace");
        var port = 18765;
        var smoke = false;
        try
        {
            for (var i = 0; i < args.Length; i++)
            {
                switch (args[i])
                {
                    case "--workspace" when i + 1 < args.Length: path = args[++i]; break;
                    case "--port" when i + 1 < args.Length: port = int.Parse(args[++i]); break;
                    case "--smoke-test": smoke = true; break;
                    default: throw new ArgumentException("Use --workspace PATH, --port PORT, or --smoke-test.");
                }
            }
            if (port < 1024 || port > 65535) throw new ArgumentException("Choose a port between 1024 and 65535.");
            using var instance = new Mutex(true, $"Local\\ResearchHub-{port}", out var owner);
            if (!owner) throw new InvalidOperationException("Research Hub is already open on this port.");
            using var window = new HubWindow(Path.GetFullPath(path), port, smoke);
            Application.Run(window);
            return window.ExitCode;
        }
        catch (Exception ex)
        {
            if (!smoke) MessageBox.Show(ex.Message, "Research Hub", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
    }
}

internal sealed class HubWindow : Form
{
    private const string RuntimeUrl = "https://developer.microsoft.com/microsoft-edge/webview2/#download";
    private readonly string workspace;
    private readonly string log;
    private readonly bool smoke;
    private readonly int port;
    private readonly CancellationTokenSource lifetime = new();
    private readonly WebView2 view = new() { Dock = DockStyle.Fill, Visible = false, AllowExternalDrop = false };
    private readonly Label state = new() { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter,
        Text = "Starting Research Hub…", ForeColor = Color.FromArgb(44, 69, 61), Font = new Font("Segoe UI", 12) };
    private Process? backend;
    private Uri? origin;
    public int ExitCode { get; private set; } = 1;

    public HubWindow(string workspace, int port, bool smoke)
    {
        this.workspace = workspace;
        this.port = port;
        this.smoke = smoke;
        log = Path.Combine(workspace, "data", "desktop.log");
        Text = "Research Hub";
        Width = 1420;
        Height = 925;
        MinimumSize = new Size(640, 490);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(245, 243, 238);
        Controls.Add(view);
        Controls.Add(state);
        var menu = new MenuStrip { Dock = DockStyle.Top };
        var file = new ToolStripMenuItem("File");
        file.DropDownItems.Add("Open workspace folder", null, (_, _) => Open(workspace));
        file.DropDownItems.Add("Exit", null, (_, _) => Close());
        menu.Items.Add(file);
        Controls.Add(menu);
        MainMenuStrip = menu;
        Shown += (_, _) => _ = StartAsync();
        FormClosing += (_, _) => StopBackend();
    }

    private async Task StartAsync()
    {
        try
        {
            CoreWebView2Environment.GetAvailableBrowserVersionString();
            using var deadline = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
            deadline.CancelAfter(TimeSpan.FromSeconds(30));
            origin = await StartBackendAsync(deadline.Token);
            var profile = Path.Combine(workspace, "webview2");
            var environment = await CoreWebView2Environment.CreateAsync(userDataFolder: profile);
            if (lifetime.IsCancellationRequested) return;
            await view.EnsureCoreWebView2Async(environment);
            if (lifetime.IsCancellationRequested) return;
            ConfigureWebView(view.CoreWebView2);
            view.Source = new Uri(origin, "/#overview");
        }
        catch (WebView2RuntimeNotFoundException)
        {
            Record("WebView2 Runtime is unavailable.");
            if (!smoke && MessageBox.Show("Research Hub requires Microsoft Edge WebView2 Runtime. Open the official download page?",
                "Research Hub", MessageBoxButtons.YesNo, MessageBoxIcon.Information) == DialogResult.Yes) Open(RuntimeUrl);
            Close();
        }
        catch (Exception ex) when (!lifetime.IsCancellationRequested)
        {
            Fail(ex.Message);
        }
    }

    private async Task<Uri> StartBackendAsync(CancellationToken token)
    {
        var executable = Path.Combine(AppContext.BaseDirectory, "Backend", "ResearchHubBackend.exe");
        var id = Guid.NewGuid().ToString("N");
        var local = new Uri($"http://127.0.0.1:{port}/");
        var start = new ProcessStartInfo(executable) { UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardInput = true, RedirectStandardOutput = true, RedirectStandardError = true,
            WorkingDirectory = AppContext.BaseDirectory };
        start.ArgumentList.Add("desktop-server");
        start.Environment["RESEARCH_HUB_HOME"] = workspace;
        start.Environment["RESEARCH_HUB_DESKTOP_PORT"] = port.ToString();
        start.Environment["RESEARCH_HUB_INSTANCE"] = id;
        backend = Process.Start(start) ?? throw new InvalidOperationException("The local server process did not start.");
        backend.ErrorDataReceived += (_, args) => { if (!string.IsNullOrWhiteSpace(args.Data)) Record(args.Data); };
        backend.BeginErrorReadLine();
        var line = await backend.StandardOutput.ReadLineAsync(token);
        if (line == null) throw new InvalidOperationException($"The local server exited. Check that port {port} is free.");
        using var ready = JsonDocument.Parse(line);
        if (ready.RootElement.GetProperty("instance").GetString() != id ||
            ready.RootElement.GetProperty("url").GetString() != local.AbsoluteUri)
            throw new InvalidOperationException("The local server did not identify itself.");
        using var handler = new HttpClientHandler { UseProxy = false, AllowAutoRedirect = false };
        using var client = new HttpClient(handler);
        using var health = JsonDocument.Parse(await client.GetStringAsync(new Uri(local, "/api/health"), token));
        if (health.RootElement.GetProperty("instance").GetString() != id)
            throw new InvalidOperationException("Another process responded on the local server port.");
        return local;
    }

    private bool IsLocal(Uri url) => origin != null && url.GetLeftPart(UriPartial.Authority) == origin.GetLeftPart(UriPartial.Authority);

    private static bool BrowserLink(Uri url) => url.Scheme == Uri.UriSchemeHttps || url.Scheme == Uri.UriSchemeHttp || url.Scheme == "obsidian";

    private void ConfigureWebView(CoreWebView2 page)
    {
        page.Settings.AreHostObjectsAllowed = false;
        page.Settings.IsWebMessageEnabled = false;
        page.Settings.AreDevToolsEnabled = false;
        page.Settings.IsStatusBarEnabled = false;
        page.NavigationStarting += (_, args) =>
        {
            if (Uri.TryCreate(args.Uri, UriKind.Absolute, out var url) && IsLocal(url)) return;
            args.Cancel = true;
            if (args.IsUserInitiated && url != null && BrowserLink(url)) Open(url.AbsoluteUri);
        };
        page.FrameNavigationStarting += (_, args) =>
        {
            if (args.Uri is "about:blank" or "about:srcdoc") return;
            args.Cancel = !Uri.TryCreate(args.Uri, UriKind.Absolute, out var url) || !IsLocal(url);
        };
        page.NewWindowRequested += (_, args) =>
        {
            args.Handled = true;
            if (args.IsUserInitiated && Uri.TryCreate(args.Uri, UriKind.Absolute, out var url) && BrowserLink(url))
                Open(url.AbsoluteUri);
        };
        page.NavigationCompleted += (_, args) =>
        {
            if (!args.IsSuccess)
            {
                Fail($"The desktop view could not open the local server ({args.WebErrorStatus}).");
                return;
            }
            view.Visible = true;
            state.Visible = false;
            if (smoke) _ = VerifyAsync(page);
            else ExitCode = 0;
        };
    }

    private async Task VerifyAsync(CoreWebView2 page)
    {
        try
        {
            for (var attempt = 0; attempt < 80 && !lifetime.IsCancellationRequested; attempt++)
            {
                var ready = await page.ExecuteScriptAsync(
                    "!!document.querySelector('#ov-activity-title') && document.querySelectorAll('.ov-day').length === 365");
                if (ready == "true")
                {
                    ExitCode = 0;
                    Record("Desktop smoke test passed.");
                    Close();
                    return;
                }
                await Task.Delay(125, lifetime.Token);
            }
            if (!lifetime.IsCancellationRequested) Fail("The dashboard did not complete rendering during the smoke test.");
        }
        catch (Exception ex) when (!lifetime.IsCancellationRequested) { Fail(ex.Message); }
    }

    private void StopBackend()
    {
        lifetime.Cancel();
        if (backend == null) return;
        try
        {
            backend.StandardInput.Close();
            if (!backend.WaitForExit(4500)) backend.Kill(entireProcessTree: true);
        }
        catch (InvalidOperationException) { /* The server already exited. */ }
        finally { backend.Dispose(); backend = null; }
    }

    private void Fail(string message)
    {
        ExitCode = 1;
        Record(message);
        if (!smoke)
            MessageBox.Show($"{message}\n\nStartup log: {log}", Text, MessageBoxButtons.OK, MessageBoxIcon.Error);
        Close();
    }

    private void Record(string message)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(log)!);
            if (File.Exists(log) && new FileInfo(log).Length > 500_000) File.Move(log, log + ".1", overwrite: true);
            File.AppendAllText(log, $"{DateTimeOffset.Now:O} {message}\n");
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void Open(string target)
    {
        try { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); }
        catch (Exception ex)
        {
            if (!smoke) MessageBox.Show(ex.Message, Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
        }
    }
}
