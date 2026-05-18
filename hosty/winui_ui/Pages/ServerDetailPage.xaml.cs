using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI;
using Windows.ApplicationModel.DataTransfer;

namespace winui_ui.Pages
{
    public sealed partial class ServerDetailPage : Page
    {
        private ServerModel? _server;
        private PythonBackendClient? _ipcClient;
        private DispatcherTimer _pollTimer;
        private bool _isLoadingDetails = false;
        private bool _isLoadingProperties = false;
        private bool _isLoadingConnection = false;
        private string _serverStatus = "stopped";
        private string _localAddress = "";
        private readonly ObservableCollection<BackupItem> _backups = new();

        private sealed class BackupItem
        {
            public string Name { get; set; } = "";
            public string Path { get; set; } = "";
            public string Detail { get; set; } = "";
        }

        public ServerDetailPage()
        {
            InitializeComponent();
            
            _pollTimer = new DispatcherTimer();
            _pollTimer.Interval = TimeSpan.FromSeconds(2);
            _pollTimer.Tick += PollTimer_Tick;
        }

        protected override void OnNavigatedTo(NavigationEventArgs e)
        {
            base.OnNavigatedTo(e);
            
            if (e.Parameter is Tuple<ServerModel, PythonBackendClient> paramsTuple)
            {
                _server = paramsTuple.Item1;
                _ipcClient = paramsTuple.Item2;
                
                // Subscribe to console output and status events
                _ipcClient.ConsoleOutput += OnConsoleOutput;
                _ipcClient.ServerStatusChanged += OnServerStatusChanged;
                
                LoadServerDetails();
                LoadConsoleHistory();
                LoadCommonCommands();
                LoadConnectionInfo();
                LoadProperties();
                LoadServerPreferences();
                LoadBackups();
                BackupsList.ItemsSource = _backups;
                _pollTimer.Start();
            }
        }

        protected override void OnNavigatedFrom(NavigationEventArgs e)
        {
            base.OnNavigatedFrom(e);
            _pollTimer.Stop();
            
            // Unsubscribe from events
            if (_ipcClient != null)
            {
                _ipcClient.ConsoleOutput -= OnConsoleOutput;
                _ipcClient.ServerStatusChanged -= OnServerStatusChanged;
            }
        }

        private void LoadServerDetails()
        {
            if (_server == null) return;
            _isLoadingDetails = true;

            ServerNameTitle.Text = _server.Name;
            ServerSubtitle.Text = $"Minecraft {_server.McVersion} - {(_server.LoaderVersion != "" ? "Fabric " + _server.LoaderVersion : "Vanilla")}";
            MinecraftVersionText.Text = _server.McVersion;
            LoaderVersionText.Text = string.IsNullOrEmpty(_server.LoaderVersion) ? "Vanilla" : _server.LoaderVersion;
            JavaVersionText.Text = $"Java {_server.JavaVersion}";
            
            RamSlider.Value = _server.RamMb;
            RamValueText.Text = $"{_server.RamMb} MB ({Math.Round(_server.RamMb / 1024.0, 1)} GB)";
            AutostartToggle.IsOn = _server.AutoStart;
            
            // Icon
            if (!string.IsNullOrEmpty(_server.IconPath) && System.IO.File.Exists(_server.IconPath))
            {
                try
                {
                    ServerIcon.Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(new Uri(_server.IconPath));
                }
                catch
                {
                    ServerIcon.Source = new Microsoft.UI.Xaml.Media.Imaging.SvgImageSource(new Uri("ms-appx:///Assets/AppIcon.svg"));
                }
            }
            else
            {
                ServerIcon.Source = new Microsoft.UI.Xaml.Media.Imaging.SvgImageSource(new Uri("ms-appx:///Assets/AppIcon.svg"));
            }

            _isLoadingDetails = false;
        }

        private async void LoadConsoleHistory()
        {
            if (_server == null || _ipcClient == null) return;

            try
            {
                var result = await _ipcClient.SendRequestAsync("get_console_log", new { server_id = _server.Id });
                if (result.TryGetProperty("log", out var logProp))
                {
                    foreach (var line in logProp.EnumerateArray())
                    {
                        string text = line.GetString() ?? "";
                        AppendConsoleText(text);
                    }
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error loading console log: {ex.Message}");
            }
        }

        private void OnConsoleOutput(object? sender, JsonElement data)
        {
            if (_server == null) return;

            string serverId = "";
            string text = "";
            if (data.TryGetProperty("server_id", out var sidProp))
                serverId = sidProp.GetString() ?? "";
            if (data.TryGetProperty("text", out var textProp))
                text = textProp.GetString() ?? "";

            if (serverId != _server.Id) return;

            DispatcherQueue.TryEnqueue(() => AppendConsoleText(text));
        }

        private void OnServerStatusChanged(object? sender, JsonElement data)
        {
            if (_server == null) return;

            string serverId = "";
            string status = "";
            if (data.TryGetProperty("server_id", out var sidProp))
                serverId = sidProp.GetString() ?? "";
            if (data.TryGetProperty("status", out var statusProp))
                status = statusProp.GetString() ?? "";

            if (serverId != _server.Id) return;

            DispatcherQueue.TryEnqueue(() => UpdateStatusUI(status));
        }

        private void UpdateStatusUI(string status)
        {
            _serverStatus = status;
            switch (status)
            {
                case "running":
                    StatusText.Text = "Running";
                    StatusText.Foreground = new SolidColorBrush(Colors.LimeGreen);
                    ToggleServerButton.IsEnabled = true;
                    ToggleServerButton.Label = "Stop";
                    ToggleServerButton.Icon = new SymbolIcon(Symbol.Stop);
                    StatsPanel.Visibility = Visibility.Visible;
                    break;
                case "starting":
                    StatusText.Text = "Starting...";
                    StatusText.Foreground = new SolidColorBrush(Colors.Orange);
                    ToggleServerButton.IsEnabled = false;
                    ToggleServerButton.Label = "Starting...";
                    ToggleServerButton.Icon = new SymbolIcon(Symbol.Sync);
                    StatsPanel.Visibility = Visibility.Collapsed;
                    break;
                case "stopping":
                    StatusText.Text = "Stopping...";
                    StatusText.Foreground = new SolidColorBrush(Colors.Orange);
                    ToggleServerButton.IsEnabled = false;
                    ToggleServerButton.Label = "Stopping...";
                    ToggleServerButton.Icon = new SymbolIcon(Symbol.Sync);
                    break;
                default: // stopped
                    StatusText.Text = "Offline";
                    StatusText.Foreground = new SolidColorBrush(Colors.Gray);
                    ToggleServerButton.IsEnabled = true;
                    ToggleServerButton.Label = "Start";
                    ToggleServerButton.Icon = new SymbolIcon(Symbol.Play);
                    StatsPanel.Visibility = Visibility.Collapsed;
                    CpuText.Text = "-";
                    RamUsageText.Text = "-";
                    PlayerCountText.Text = "-";
                    break;
            }
        }

        private void AppendConsoleText(string text)
        {
            if (string.IsNullOrEmpty(text)) return;
            ConsoleText.Text += text;
            
            // Auto-scroll to bottom
            ConsoleScroll.UpdateLayout();
            ConsoleScroll.ChangeView(null, ConsoleScroll.ScrollableHeight, null);
        }

        private async void PollTimer_Tick(object? sender, object? e)
        {
            if (_server == null || _ipcClient == null) return;

            try
            {
                var result = await _ipcClient.SendRequestAsync("get_runtime_state", new { server_id = _server.Id });
                
                bool isRunning = false;
                if (result.TryGetProperty("is_running", out var runProp))
                    isRunning = runProp.GetBoolean();

                string status = "stopped";
                if (result.TryGetProperty("status", out var statusProp))
                    status = statusProp.GetString() ?? "stopped";

                UpdateStatusUI(status);

                if (isRunning)
                {
                    if (result.TryGetProperty("cpu_percent", out var cpuProp))
                    {
                        CpuText.Text = $"{cpuProp.GetDouble():F1}%";
                    }
                    if (result.TryGetProperty("ram_mb", out var ramProp))
                    {
                        double ramMb = ramProp.GetDouble();
                        if (ramMb >= 1024)
                            RamUsageText.Text = $"{ramMb / 1024.0:F1} GB";
                        else
                            RamUsageText.Text = $"{ramMb:F0} MB";
                    }
                    if (result.TryGetProperty("player_count", out var playerProp) &&
                        result.TryGetProperty("max_players", out var maxProp))
                    {
                        PlayerCountText.Text = $"{playerProp.GetInt32()} / {maxProp.GetInt32()}";
                    }
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error polling server state: {ex.Message}");
            }
        }

        private async void ToggleServerButton_Click(object sender, RoutedEventArgs e)
        {
            if (_server == null || _ipcClient == null) return;

            bool shouldStop = _serverStatus == "running";
            ToggleServerButton.IsEnabled = false;
            ToggleServerButton.Label = shouldStop ? "Stopping..." : "Starting...";
            try
            {
                if (shouldStop)
                {
                    await _ipcClient.SendRequestAsync("stop_server", new { server_id = _server.Id });
                }
                else
                {
                    ConsoleText.Text = "";
                    await _ipcClient.SendRequestAsync("start_server", new { server_id = _server.Id });
                }
            }
            catch (Exception ex)
            {
                var dialog = new ContentDialog
                {
                    Title = shouldStop ? "Failed to Stop" : "Failed to Start",
                    Content = ex.Message,
                    CloseButtonText = "OK",
                    XamlRoot = this.XamlRoot
                };
                await dialog.ShowAsync();
                UpdateStatusUI(_serverStatus);
            }
        }

        private async void LoadServerPreferences()
        {
            if (_ipcClient == null) return;
            try
            {
                var result = await _ipcClient.SendRequestAsync("get_preferences");
                if (result.TryGetProperty("auto_backup_on_stop", out var backupProp))
                {
                    _isLoadingDetails = true;
                    AutoBackupToggle.IsOn = backupProp.GetBoolean();
                    _isLoadingDetails = false;
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error loading server preferences: {ex.Message}");
            }
        }

        private async void LoadCommonCommands()
        {
            if (_ipcClient == null) return;
            try
            {
                var result = await _ipcClient.SendRequestAsync("get_common_commands");
                CommonCommandsMenu.Items.Clear();
                foreach (var command in result.EnumerateArray())
                {
                    string label = command.TryGetProperty("label", out var labelProp) ? labelProp.GetString() ?? "" : "";
                    string value = command.TryGetProperty("command", out var cmdProp) ? cmdProp.GetString() ?? "" : "";
                    bool needsArgs = command.TryGetProperty("needs_args", out var argsProp) && argsProp.GetBoolean();
                    var item = new MenuFlyoutItem { Text = label, Tag = value };
                    item.Click += (_, _) =>
                    {
                        ConsoleInputBox.Text = value;
                        ConsoleInputBox.Focus(FocusState.Programmatic);
                        ConsoleInputBox.SelectionStart = ConsoleInputBox.Text.Length;
                        if (!needsArgs)
                        {
                            _ = SendConsoleCommand(value);
                        }
                    };
                    CommonCommandsMenu.Items.Add(item);
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to load common commands: {ex.Message}");
            }
        }

        private async void RamSlider_ValueChanged(object sender, Microsoft.UI.Xaml.Controls.Primitives.RangeBaseValueChangedEventArgs e)
        {
            if (_server == null || _ipcClient == null || _isLoadingDetails) return;
            int ram = (int)e.NewValue;
            RamValueText.Text = $"{ram} MB ({Math.Round(ram / 1024.0, 1)} GB)";
            
            // Actually save the RAM change via IPC
            try
            {
                await _ipcClient.SendRequestAsync("update_ram", new { server_id = _server.Id, ram_mb = ram });
                _server.RamMb = ram; // Update local model
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Error saving RAM: {ex.Message}");
            }
        }

        private void AutoBackupToggle_Toggled(object sender, RoutedEventArgs e)
        {
            if (_ipcClient == null || _isLoadingDetails) return;
            _ = _ipcClient.SendRequestAsync("update_preference", new { key = "auto_backup_on_stop", value = AutoBackupToggle.IsOn });
        }

        private async void AutostartToggle_Toggled(object sender, RoutedEventArgs e)
        {
            if (_server == null || _ipcClient == null || _isLoadingDetails) return;
            try
            {
                await _ipcClient.SendRequestAsync("set_autostart", new { server_id = _server.Id, autostart = AutostartToggle.IsOn });
                _server.AutoStart = AutostartToggle.IsOn;
            }
            catch (Exception ex)
            {
                _isLoadingDetails = true;
                AutostartToggle.IsOn = _server.AutoStart;
                _isLoadingDetails = false;
                await ShowErrorDialog("Could not update autostart", ex.Message);
            }
        }

        private async void ConsoleInputBox_KeyDown(object sender, Microsoft.UI.Xaml.Input.KeyRoutedEventArgs e)
        {
            if (e.Key == Windows.System.VirtualKey.Enter)
            {
                string command = ConsoleInputBox.Text.Trim();
                await SendConsoleCommand(command);
            }
        }

        private async System.Threading.Tasks.Task SendConsoleCommand(string command)
        {
            if (string.IsNullOrWhiteSpace(command) || _server == null || _ipcClient == null) return;
            ConsoleInputBox.Text = "";
            try
            {
                AppendConsoleText($"> {command}\n");
                await _ipcClient.SendRequestAsync("send_command", new { server_id = _server.Id, command = command });
            }
            catch (Exception ex)
            {
                AppendConsoleText($"[Hosty] Error: {ex.Message}\n");
            }
        }

        private void ClearConsoleButton_Click(object sender, RoutedEventArgs e)
        {
            ConsoleText.Text = "";
        }

        private async void OpenFolderButton_Click(object sender, RoutedEventArgs e)
        {
            if (_server == null || _ipcClient == null) return;
            try
            {
                await _ipcClient.SendRequestAsync("open_server_folder", new { server_id = _server.Id });
            }
            catch (Exception ex)
            {
                await ShowErrorDialog("Could not open folder", ex.Message);
            }
        }

        private async void BackupButton_Click(object sender, RoutedEventArgs e)
        {
            if (_server == null || _ipcClient == null) return;
            try
            {
                var result = await _ipcClient.SendRequestAsync("create_world_backup", new { server_id = _server.Id }, timeoutMs: 120000);
                string message = result.TryGetProperty("message", out var msg) ? msg.GetString() ?? "Backup created." : "Backup created.";
                PropertiesInfoBar.Severity = InfoBarSeverity.Success;
                PropertiesInfoBar.Title = "Backup created";
                PropertiesInfoBar.Message = message;
                PropertiesInfoBar.IsOpen = true;
                LoadBackups();
            }
            catch (Exception ex)
            {
                await ShowErrorDialog("Could not create backup", ex.Message);
            }
        }

        private async void RenameButton_Click(object sender, RoutedEventArgs e)
        {
            if (_server == null || _ipcClient == null) return;

            var box = new TextBox { Text = _server.Name, SelectionStart = 0, SelectionLength = _server.Name.Length };
            var dialog = new ContentDialog
            {
                Title = "Rename server",
                Content = box,
                PrimaryButtonText = "Rename",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Primary,
                XamlRoot = this.XamlRoot
            };

            if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;
            string newName = box.Text.Trim();
            if (string.IsNullOrEmpty(newName)) return;

            try
            {
                await _ipcClient.SendRequestAsync("rename_server", new { server_id = _server.Id, new_name = newName });
                _server.Name = newName;
                ServerNameTitle.Text = newName;
            }
            catch (Exception ex)
            {
                await ShowErrorDialog("Could not rename server", ex.Message);
            }
        }

        private async void DeleteButton_Click(object sender, RoutedEventArgs e)
        {
            if (_server == null || App.MainWindow == null) return;
            await App.MainWindow.DeleteServer(_server);
        }

        private async void LoadProperties()
        {
            if (_server == null || _ipcClient == null) return;
            _isLoadingProperties = true;
            try
            {
                var result = await _ipcClient.SendRequestAsync("get_server_properties", new { server_id = _server.Id });
                if (result.TryGetProperty("properties", out var props))
                {
                    MotdBox.Text = GetStringProp(props, "motd", "a hosty server");
                    MaxPlayersBox.Value = GetIntProp(props, "max-players", 20);
                    SelectComboByTag(DifficultyBox, GetBoolProp(props, "hardcore", false) ? "hardcore" : GetStringProp(props, "difficulty", "easy"));
                    SelectComboByTag(GamemodeBox, GetStringProp(props, "gamemode", "survival"));
                    ViewDistanceBox.Value = GetIntProp(props, "view-distance", 10);
                    SimulationDistanceBox.Value = GetIntProp(props, "simulation-distance", 10);
                    EnableQueryToggle.IsOn = GetBoolProp(props, "enable-query", false);
                    PvpToggle.IsOn = GetBoolProp(props, "pvp", true);
                    AllowFlightToggle.IsOn = GetBoolProp(props, "allow-flight", false);
                    KeepInventoryToggle.IsOn = GetBoolProp(props, "keep-inventory", false);
                    WhitelistToggle.IsOn = GetBoolProp(props, "white-list", false);
                    CommandBlocksToggle.IsOn = GetBoolProp(props, "enable-command-block", false);
                    AllowNetherToggle.IsOn = GetBoolProp(props, "allow-nether", true);
                    SpawnProtectionBox.Value = GetIntProp(props, "spawn-protection", 16);
                }
            }
            catch (Exception ex)
            {
                ShowPropertiesMessage("Could not load properties", ex.Message, InfoBarSeverity.Error);
            }
            finally
            {
                _isLoadingProperties = false;
            }
        }

        private static string GetStringProp(JsonElement props, string key, string fallback)
        {
            return props.TryGetProperty(key, out var value) ? value.GetString() ?? fallback : fallback;
        }

        private static int GetIntProp(JsonElement props, string key, int fallback)
        {
            return int.TryParse(GetStringProp(props, key, fallback.ToString()), out int value) ? value : fallback;
        }

        private static bool GetBoolProp(JsonElement props, string key, bool fallback)
        {
            string raw = GetStringProp(props, key, fallback ? "true" : "false");
            return string.Equals(raw, "true", StringComparison.OrdinalIgnoreCase);
        }

        private static void SelectComboByTag(ComboBox combo, string tag)
        {
            for (int i = 0; i < combo.Items.Count; i++)
            {
                if (combo.Items[i] is ComboBoxItem item && item.Tag?.ToString() == tag)
                {
                    combo.SelectedIndex = i;
                    return;
                }
            }
            combo.SelectedIndex = 0;
        }

        private async void Property_LostFocus(object sender, RoutedEventArgs e)
        {
            if (_isLoadingProperties) return;
            await SaveProperties(new Dictionary<string, object> { ["motd"] = MotdBox.Text });
        }

        private async void PropertyNumber_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
        {
            if (_isLoadingProperties || double.IsNaN(args.NewValue)) return;
            string? key = sender == MaxPlayersBox ? "max-players" :
                          sender == ViewDistanceBox ? "view-distance" :
                          sender == SimulationDistanceBox ? "simulation-distance" :
                          sender == SpawnProtectionBox ? "spawn-protection" : null;
            if (key == null) return;
            await SaveProperties(new Dictionary<string, object> { [key] = Math.Round(args.NewValue).ToString() });
        }

        private async void PropertyToggle_Toggled(object sender, RoutedEventArgs e)
        {
            if (_isLoadingProperties || sender is not ToggleSwitch toggle) return;
            string? key = toggle == EnableQueryToggle ? "enable-query" :
                          toggle == PvpToggle ? "pvp" :
                          toggle == AllowFlightToggle ? "allow-flight" :
                          toggle == KeepInventoryToggle ? "keep-inventory" :
                          toggle == WhitelistToggle ? "white-list" :
                          toggle == CommandBlocksToggle ? "enable-command-block" :
                          toggle == AllowNetherToggle ? "allow-nether" : null;
            if (key == null) return;
            await SaveProperties(new Dictionary<string, object> { [key] = toggle.IsOn });
        }

        private async void PropertyCombo_SelectionChanged(object sender, SelectionChangedEventArgs e)
        {
            if (_isLoadingProperties || sender is not ComboBox combo || combo.SelectedItem is not ComboBoxItem item) return;
            string value = item.Tag?.ToString() ?? "";
            if (combo == DifficultyBox)
            {
                bool hardcore = value == "hardcore";
                await SaveProperties(new Dictionary<string, object>
                {
                    ["difficulty"] = hardcore ? "hard" : value,
                    ["hardcore"] = hardcore
                });
            }
            else if (combo == GamemodeBox)
            {
                await SaveProperties(new Dictionary<string, object> { ["gamemode"] = value });
            }
        }

        private async void LoadConnectionInfo()
        {
            if (_server == null || _ipcClient == null) return;
            _isLoadingConnection = true;
            try
            {
                var result = await _ipcClient.SendRequestAsync("get_connection_info", new { server_id = _server.Id });
                _localAddress = result.TryGetProperty("local_address", out var addressProp) ? addressProp.GetString() ?? "" : "";
                LocalAddressText.Text = _localAddress;
                ServerPortText.Text = result.TryGetProperty("server_port", out var portProp) ? portProp.GetString() ?? "25565" : "25565";
                if (result.TryGetProperty("whitelist", out var wlProp))
                    WhitelistToggle.IsOn = wlProp.GetBoolean();
            }
            catch (Exception ex)
            {
                ConnectInfoBar.Title = "Could not load connection info";
                ConnectInfoBar.Message = ex.Message;
                ConnectInfoBar.Severity = InfoBarSeverity.Error;
                ConnectInfoBar.IsOpen = true;
            }
            finally
            {
                _isLoadingConnection = false;
            }
        }

        private void CopyLocalAddressButton_Click(object sender, RoutedEventArgs e)
        {
            if (string.IsNullOrWhiteSpace(_localAddress) || _localAddress == "Not available") return;
            var package = new DataPackage();
            package.SetText(_localAddress);
            Clipboard.SetContent(package);
            ConnectInfoBar.Title = "Copied";
            ConnectInfoBar.Message = _localAddress;
            ConnectInfoBar.Severity = InfoBarSeverity.Success;
            ConnectInfoBar.IsOpen = true;
        }

        private async void WhitelistToggle_Toggled(object sender, RoutedEventArgs e)
        {
            if (_isLoadingProperties || _isLoadingConnection) return;
            await SaveProperties(new Dictionary<string, object> { ["white-list"] = WhitelistToggle.IsOn });
        }

        private async void WhitelistPlayerButton_Click(object sender, RoutedEventArgs e)
        {
            await SendPlayerCommand("whitelist add");
        }

        private async void RemoveWhitelistPlayerButton_Click(object sender, RoutedEventArgs e)
        {
            await SendPlayerCommand("whitelist remove");
        }

        private async void BanPlayerButton_Click(object sender, RoutedEventArgs e)
        {
            await SendPlayerCommand("ban");
        }

        private async void PardonPlayerButton_Click(object sender, RoutedEventArgs e)
        {
            await SendPlayerCommand("pardon");
        }

        private async System.Threading.Tasks.Task SendPlayerCommand(string verb)
        {
            string name = PlayerNameBox.Text.Trim();
            if (string.IsNullOrWhiteSpace(name)) return;
            await SendConsoleCommand($"{verb} {name}");
            PlayerNameBox.Text = "";
        }

        private async void LoadBackups()
        {
            if (_server == null || _ipcClient == null) return;
            try
            {
                var result = await _ipcClient.SendRequestAsync("list_backups", new { server_id = _server.Id });
                _backups.Clear();
                foreach (var item in result.EnumerateArray())
                {
                    string name = item.TryGetProperty("name", out var nameProp) ? nameProp.GetString() ?? "" : "";
                    string path = item.TryGetProperty("path", out var pathProp) ? pathProp.GetString() ?? "" : "";
                    long size = item.TryGetProperty("size_bytes", out var sizeProp) ? sizeProp.GetInt64() : 0;
                    bool isFull = item.TryGetProperty("is_full", out var fullProp) && fullProp.GetBoolean();
                    _backups.Add(new BackupItem
                    {
                        Name = name,
                        Path = path,
                        Detail = $"{(isFull ? "Full backup" : "World backup")} - {FormatBytes(size)}",
                    });
                }
            }
            catch (Exception ex)
            {
                System.Diagnostics.Debug.WriteLine($"Failed to load backups: {ex.Message}");
            }
        }

        private static string FormatBytes(long bytes)
        {
            if (bytes >= 1024L * 1024L * 1024L)
                return $"{bytes / (1024.0 * 1024.0 * 1024.0):F1} GB";
            if (bytes >= 1024L * 1024L)
                return $"{bytes / (1024.0 * 1024.0):F1} MB";
            if (bytes >= 1024L)
                return $"{bytes / 1024.0:F1} KB";
            return $"{bytes} B";
        }

        private void RefreshBackupsButton_Click(object sender, RoutedEventArgs e)
        {
            LoadBackups();
        }

        private async void RestoreBackupButton_Click(object sender, RoutedEventArgs e)
        {
            if (_server == null || _ipcClient == null || sender is not Button button) return;
            string path = button.Tag?.ToString() ?? "";
            if (string.IsNullOrWhiteSpace(path)) return;

            var dialog = new ContentDialog
            {
                Title = "Restore backup?",
                Content = "The server must be stopped. Restoring replaces the current world data.",
                PrimaryButtonText = "Restore",
                CloseButtonText = "Cancel",
                DefaultButton = ContentDialogButton.Close,
                XamlRoot = this.XamlRoot
            };
            if (await dialog.ShowAsync() != ContentDialogResult.Primary) return;

            try
            {
                var result = await _ipcClient.SendRequestAsync("restore_backup", new { server_id = _server.Id, path = path }, timeoutMs: 120000);
                string message = result.TryGetProperty("message", out var msgProp) ? msgProp.GetString() ?? "Restored." : "Restored.";
                ShowPropertiesMessage("Backup restored", message, InfoBarSeverity.Success);
            }
            catch (Exception ex)
            {
                await ShowErrorDialog("Could not restore backup", ex.Message);
            }
        }

        private async System.Threading.Tasks.Task SaveProperties(Dictionary<string, object> updates)
        {
            if (_server == null || _ipcClient == null) return;
            try
            {
                await _ipcClient.SendRequestAsync("update_server_properties", new { server_id = _server.Id, properties = updates });
                ShowPropertiesMessage("Saved", "Restart the server to apply most property changes.", InfoBarSeverity.Success);
            }
            catch (Exception ex)
            {
                ShowPropertiesMessage("Could not save properties", ex.Message, InfoBarSeverity.Error);
            }
        }

        private void ShowPropertiesMessage(string title, string message, InfoBarSeverity severity)
        {
            PropertiesInfoBar.Title = title;
            PropertiesInfoBar.Message = message;
            PropertiesInfoBar.Severity = severity;
            PropertiesInfoBar.IsOpen = true;
        }

        private async System.Threading.Tasks.Task ShowErrorDialog(string title, string message)
        {
            var dialog = new ContentDialog
            {
                Title = title,
                Content = message,
                CloseButtonText = "OK",
                XamlRoot = this.XamlRoot
            };
            await dialog.ShowAsync();
        }
    }
}
