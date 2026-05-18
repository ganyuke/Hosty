using System;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using winui_ui.Pages;

namespace winui_ui;

public sealed partial class MainWindow : Window
{
    public PythonBackendClient IpcClient { get; private set; } = null!;
    private List<ServerModel> _servers = new();
    private bool _autostartAttempted = false;

    // Store references to the dynamic menu items so we can remove/update them
    private readonly List<NavigationViewItem> _serverMenuItems = new();
    private NavigationViewItemHeader? _serversHeader;
    private NavigationViewItem? _createServerItem;

    public MainWindow()
    {
        InitializeComponent();

        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);
        AppWindow.TitleBar.PreferredHeightOption = TitleBarHeightOption.Tall;
        AppWindow.SetIcon("Assets/AppIcon.ico");

        this.Closed += MainWindow_Closed;

        InitializeBackend();
    }

    private void InitializeBackend()
    {
        IpcClient = new PythonBackendClient();
        IpcClient.BackendReady += IpcClient_BackendReady;
        IpcClient.ServerAdded += (s, data) => Dispatch(async () => await RefreshServers());
        IpcClient.ServerRemoved += (s, data) => Dispatch(async () => await RefreshServers());
        IpcClient.ServerChanged += (s, data) => Dispatch(async () => await RefreshServers());
        IpcClient.BackupComplete += (s, data) => Dispatch(() => ShowTeachingTip("Backup created", ReadMessage(data)));
        IpcClient.BackupSkipped += (s, data) => Dispatch(() => System.Diagnostics.Debug.WriteLine($"Backup skipped: {ReadMessage(data)}"));

        // Resolve paths relative to the build output directory
        string baseDir = AppContext.BaseDirectory;
        System.Diagnostics.Debug.WriteLine($"[Hosty] AppContext.BaseDirectory = {baseDir}");

        // Project root: in dev the baseDir is something like
        //   <repo>\hosty\winui_ui\bin\<arch>\Debug\net9.0-windows10.0.26100.0\win-x64\
        // We climb up to find the repo root by looking for the "hosty" package directory.
        string projectRoot = baseDir;
        var probe = new DirectoryInfo(baseDir);
        while (probe != null)
        {
            // Look for the hosty Python package directory (contains __init__.py)
            string hostyPkg = Path.Combine(probe.FullName, "hosty", "__init__.py");
            if (File.Exists(hostyPkg))
            {
                projectRoot = probe.FullName;
                break;
            }
            probe = probe.Parent;
        }

        System.Diagnostics.Debug.WriteLine($"[Hosty] Resolved projectRoot = {projectRoot}");

        string pythonPath = Path.Combine(projectRoot, ".venv", "Scripts", "python.exe");
        if (!File.Exists(pythonPath))
        {
            System.Diagnostics.Debug.WriteLine($"[Hosty] .venv python not found at {pythonPath}, falling back to PATH");
            pythonPath = "python";
        }

        string ipcPath = Path.Combine(projectRoot, "hosty", "win_ipc.py");
        if (!File.Exists(ipcPath))
        {
            System.Diagnostics.Debug.WriteLine($"[Hosty] win_ipc.py not found at {ipcPath}");
        }

        System.Diagnostics.Debug.WriteLine($"[Hosty] pythonPath = {pythonPath}");
        System.Diagnostics.Debug.WriteLine($"[Hosty] ipcPath = {ipcPath}");

        try
        {
            IpcClient.Start(pythonPath, ipcPath, projectRoot);
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"Failed to start Python IPC: {ex.Message}");
        }
    }

    private void Dispatch(Action action)
    {
        DispatcherQueue.TryEnqueue(() => action());
    }

    private static string ReadMessage(JsonElement data)
    {
        return data.TryGetProperty("message", out var msg) ? msg.GetString() ?? "" : "";
    }

    private void IpcClient_BackendReady(object? sender, EventArgs e)
    {
        Dispatch(async () => {
            await LoadPreferences();
            await RefreshServers();
            await StartAutostartServer();
            // Navigate to Home initially
            NavFrame.Navigate(typeof(HomePage));
        });
    }

    private async Task LoadPreferences()
    {
        try
        {
            var prefs = await IpcClient.SendRequestAsync("get_preferences");
            if (prefs.TryGetProperty("theme", out var themeProp))
            {
                App.ApplyTheme(themeProp.GetString());
            }
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"Error loading preferences: {ex.Message}");
        }
    }

    private async Task RefreshServers()
    {
        if (IpcClient == null) return;

        try
        {
            var result = await IpcClient.SendRequestAsync("get_servers");
            _servers = JsonSerializer.Deserialize<List<ServerModel>>(result.GetRawText()) ?? new();
            
            BuildServerMenu();
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"Error refreshing servers: {ex.Message}");
        }
    }

    private void BuildServerMenu()
    {
        // 1. Remove existing dynamic items
        foreach (var item in _serverMenuItems)
        {
            NavView.MenuItems.Remove(item);
        }
        _serverMenuItems.Clear();

        if (_serversHeader != null)
        {
            NavView.MenuItems.Remove(_serversHeader);
        }
        if (_createServerItem != null)
        {
            NavView.MenuItems.Remove(_createServerItem);
        }

        // 2. Add header
        _serversHeader = new NavigationViewItemHeader { Content = "Servers" };
        NavView.MenuItems.Add(_serversHeader);

        // 3. Add Server items
        foreach (var server in _servers)
        {
            Microsoft.UI.Xaml.Controls.IconElement iconElement;
            if (!string.IsNullOrEmpty(server.IconPath) && System.IO.File.Exists(server.IconPath))
            {
                try
                {
                    iconElement = new Microsoft.UI.Xaml.Controls.ImageIcon
                    {
                        Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(new Uri(server.IconPath))
                    };
                }
                catch
                {
                    iconElement = new Microsoft.UI.Xaml.Controls.SymbolIcon(Microsoft.UI.Xaml.Controls.Symbol.Contact);
                }
            }
            else
            {
                try
                {
                    iconElement = new Microsoft.UI.Xaml.Controls.ImageIcon
                    {
                        Source = new Microsoft.UI.Xaml.Media.Imaging.SvgImageSource(new Uri("ms-appx:///Assets/AppIcon.svg"))
                    };
                }
                catch
                {
                    iconElement = new Microsoft.UI.Xaml.Controls.SymbolIcon(Microsoft.UI.Xaml.Controls.Symbol.Contact);
                }
            }

            var item = new NavigationViewItem
            {
                Content = server.Name,
                Icon = iconElement,
                Tag = $"server_{server.Id}"
            };
            NavView.MenuItems.Add(item);
            _serverMenuItems.Add(item);
        }

        // 4. Add "Create Server" item
        _createServerItem = new NavigationViewItem
        {
            Content = "Add Server",
            Icon = new SymbolIcon(Symbol.Add),
            Tag = "create_server"
        };
        NavView.MenuItems.Add(_createServerItem);
    }

    private void TitleBar_PaneToggleRequested(TitleBar sender, object args)
    {
        NavView.IsPaneOpen = !NavView.IsPaneOpen;
    }

    private async void NavView_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.IsSettingsSelected)
        {
            NavFrame.Navigate(typeof(SettingsPage));
            return;
        }

        if (args.SelectedItem is NavigationViewItem item)
        {
            string? tag = item.Tag?.ToString();
            if (string.IsNullOrEmpty(tag)) return;

            if (tag == "home")
            {
                NavFrame.Navigate(typeof(HomePage));
            }
            else if (tag == "create_server")
            {
                await ShowCreateServerDialog();
            }
            else if (tag == "about")
            {
                NavFrame.Navigate(typeof(AboutPage));
            }
            else if (tag.StartsWith("server_"))
            {
                string serverId = tag.Substring(7);
                var server = _servers.Find(s => s.Id == serverId);
                if (server != null)
                {
                    // Pass the server and client tuple
                    NavFrame.Navigate(typeof(ServerDetailPage), Tuple.Create(server, IpcClient));
                }
            }
        }
    }

    internal async Task ShowCreateServerDialog()
    {
        var dialog = new winui_ui.Dialogs.CreateServerDialog();
        dialog.XamlRoot = this.Content.XamlRoot;
        await dialog.ShowAsync();
        
        // Refresh when closed
        await RefreshServers();
        if (!string.IsNullOrEmpty(dialog.CreatedServerId))
        {
            SelectServer(dialog.CreatedServerId);
        }
    }

    private async Task StartAutostartServer()
    {
        if (_autostartAttempted) return;
        _autostartAttempted = true;

        var autostartServer = _servers.Find(s => s.AutoStart);
        if (autostartServer == null) return;

        try
        {
            await IpcClient.SendRequestAsync("start_server", new { server_id = autostartServer.Id });
            ShowTeachingTip("Starting server", autostartServer.Name);
        }
        catch (Exception ex)
        {
            System.Diagnostics.Debug.WriteLine($"Autostart failed: {ex.Message}");
        }
    }

    internal async Task DeleteServer(ServerModel server)
    {
        var dialog = new ContentDialog
        {
            Title = "Delete server?",
            Content = $"Delete \"{server.Name}\" and its files? This cannot be undone.",
            PrimaryButtonText = "Delete",
            CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
            XamlRoot = this.Content.XamlRoot
        };

        var result = await dialog.ShowAsync();
        if (result != ContentDialogResult.Primary) return;

        await IpcClient.SendRequestAsync("delete_server", new { server_id = server.Id, delete_files = true });
        await RefreshServers();
        NavFrame.Navigate(typeof(HomePage));
    }

    internal void SelectServer(string serverId)
    {
        foreach (var item in _serverMenuItems)
        {
            if (item.Tag?.ToString() == $"server_{serverId}")
            {
                NavView.SelectedItem = item;
                var server = _servers.Find(s => s.Id == serverId);
                if (server != null)
                {
                    NavFrame.Navigate(typeof(ServerDetailPage), Tuple.Create(server, IpcClient));
                }
                return;
            }
        }
    }

    internal void ShowTeachingTip(string title, string subtitle)
    {
        var tip = new TeachingTip
        {
            Title = title,
            Subtitle = subtitle,
            IsLightDismissEnabled = true,
            IsOpen = true,
            PreferredPlacement = TeachingTipPlacementMode.BottomRight,
            XamlRoot = this.Content.XamlRoot
        };
        if (this.Content is Panel panel)
        {
            panel.Children.Add(tip);
            tip.Closed += (_, _) => panel.Children.Remove(tip);
        }
    }

    private void MainWindow_Closed(object sender, WindowEventArgs args)
    {
        IpcClient?.Dispose();
    }
}
