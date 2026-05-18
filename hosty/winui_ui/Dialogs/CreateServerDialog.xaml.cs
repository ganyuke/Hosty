using System;
using System.Collections.Generic;
using System.Text.Json;
using System.Threading.Tasks;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace winui_ui.Dialogs
{
    public sealed partial class CreateServerDialog : ContentDialog
    {
        private List<string> _gameVersions = new();
        private List<string> _loaderVersions = new();
        private bool _isInstalling = false;
        public string CreatedServerId { get; private set; } = "";

        public CreateServerDialog()
        {
            this.InitializeComponent();
            this.Loaded += CreateServerDialog_Loaded;
            this.Unloaded += CreateServerDialog_Unloaded;
        }

        private async void CreateServerDialog_Loaded(object sender, RoutedEventArgs e)
        {
            IsPrimaryButtonEnabled = false;

            if (App.MainWindow?.IpcClient != null)
            {
                App.MainWindow.IpcClient.InstallProgress += OnInstallProgress;
                App.MainWindow.IpcClient.InstallComplete += OnInstallComplete;
                App.MainWindow.IpcClient.InstallError += OnInstallError;

                try
                {
                    var result = await App.MainWindow.IpcClient.SendRequestAsync("get_versions");
                    
                    if (result.TryGetProperty("game_versions", out var gameVersionsProp))
                    {
                        foreach (var ver in gameVersionsProp.EnumerateArray())
                        {
                            if (ver.GetString() is string val)
                            {
                                _gameVersions.Add(val);
                            }
                        }
                    }

                    if (result.TryGetProperty("loader_versions", out var loaderVersionsProp))
                    {
                        foreach (var ver in loaderVersionsProp.EnumerateArray())
                        {
                            if (ver.GetString() is string val)
                            {
                                _loaderVersions.Add(val);
                            }
                        }
                    }

                    DispatcherQueue.TryEnqueue(() =>
                    {
                        VersionBox.Items.Clear();
                        foreach (var v in _gameVersions)
                        {
                            VersionBox.Items.Add(v);
                        }
                        if (VersionBox.Items.Count > 0)
                            VersionBox.SelectedIndex = 0;

                        if (_loaderVersions.Count > 0)
                            LoaderText.Text = $"Fabric Loader: {_loaderVersions[0]}";
                            
                        ValidateInput();
                    });
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"Failed to load versions: {ex.Message}");
                    DispatcherQueue.TryEnqueue(() =>
                    {
                        VersionBox.Items.Clear();
                        
                        // Robust offline fallbacks in case Fabric API is down or connection fails
                        _gameVersions = new List<string> { "1.21.1", "1.21", "1.20.4", "1.20.1", "1.19.4", "1.18.2" };
                        _loaderVersions = new List<string> { "0.16.0" };

                        foreach (var v in _gameVersions)
                        {
                            VersionBox.Items.Add(v);
                        }
                        VersionBox.SelectedIndex = 0;
                        LoaderText.Text = $"Fabric Loader: 0.16.0 (Offline Fallback: {ex.Message})";
                        
                        ValidateInput();
                    });
                }
            }
        }

        private void CreateServerDialog_Unloaded(object sender, RoutedEventArgs e)
        {
            if (App.MainWindow?.IpcClient != null)
            {
                App.MainWindow.IpcClient.InstallProgress -= OnInstallProgress;
                App.MainWindow.IpcClient.InstallComplete -= OnInstallComplete;
                App.MainWindow.IpcClient.InstallError -= OnInstallError;
            }
        }

        private void RamSlider_ValueChanged(object sender, Microsoft.UI.Xaml.Controls.Primitives.RangeBaseValueChangedEventArgs e)
        {
            if (RamValueText != null)
            {
                int ram = (int)e.NewValue;
                RamValueText.Text = $"{ram} MB ({Math.Round(ram / 1024.0, 1)} GB)";
            }
        }

        private void Input_Changed(object sender, object e)
        {
            ValidateInput();
        }

        private void ValidateInput()
        {
            if (_isInstalling) return;

            string name = NameBox.Text.Trim();
            bool hasVersion = VersionBox.SelectedItem != null && _gameVersions.Count > 0;
            IsPrimaryButtonEnabled = !string.IsNullOrEmpty(name) && hasVersion;
        }

        private async void ContentDialog_PrimaryButtonClick(ContentDialog sender, ContentDialogButtonClickEventArgs args)
        {
            if (_isInstalling)
            {
                // If it was somehow clicked again while installing
                args.Cancel = true;
                return;
            }

            args.Cancel = true; // Prevent dialog from closing
            
            string name = NameBox.Text.Trim();
            string mcVersion = VersionBox.SelectedItem?.ToString() ?? "";
            string loaderVersion = _loaderVersions.Count > 0 ? _loaderVersions[0] : "";
            int ramMb = (int)RamSlider.Value;
            bool optimise = OptimiseToggle.IsOn;

            _isInstalling = true;
            IsPrimaryButtonEnabled = false;

            ConfigPanel.Visibility = Visibility.Collapsed;
            ProgressPanel.Visibility = Visibility.Visible;

            if (App.MainWindow?.IpcClient != null)
            {
                try
                {
                    // Call the new install_server IPC endpoint
                    await App.MainWindow.IpcClient.SendRequestAsync("install_server", new
                    {
                        name = name,
                        mc_version = mcVersion,
                        loader_version = loaderVersion,
                        ram_mb = ramMb,
                        install_optimisations = optimise
                    });
                    // Response returns immediately as True. The actual progress continues asynchronously.
                }
                catch (Exception ex)
                {
                    System.Diagnostics.Debug.WriteLine($"Failed to start install: {ex.Message}");
                    ProgressTitle.Text = "Failed to start";
                    ProgressSubtitle.Text = ex.Message;
                }
            }
        }

        private void ContentDialog_CloseButtonClick(ContentDialog sender, ContentDialogButtonClickEventArgs args)
        {
            if (_isInstalling && ProgressTitle.Text != "Failed to start")
            {
                args.Cancel = true; // Can't cancel while installing right now
            }
        }

        private void OnInstallProgress(object? sender, JsonElement data)
        {
            DispatcherQueue.TryEnqueue(() =>
            {
                if (data.TryGetProperty("progress", out var progProp))
                    InstallProgressBar.Value = progProp.GetDouble() * 100;
                    
                if (data.TryGetProperty("message", out var msgProp))
                    ProgressSubtitle.Text = msgProp.GetString();
            });
        }

        private void OnInstallComplete(object? sender, JsonElement data)
        {
            DispatcherQueue.TryEnqueue(() =>
            {
                if (data.TryGetProperty("server_id", out var sidProp))
                    CreatedServerId = sidProp.GetString() ?? "";

                InstallProgressBar.Value = 100;
                ProgressTitle.Text = "Server Created!";
                ProgressSubtitle.Text = "Ready to start.";
                
                // Close the dialog after a brief delay
                Task.Delay(1500).ContinueWith(_ => DispatcherQueue.TryEnqueue(() => this.Hide()));
            });
        }

        private void OnInstallError(object? sender, JsonElement data)
        {
            DispatcherQueue.TryEnqueue(() =>
            {
                ProgressTitle.Text = "Installation Error";
                if (data.TryGetProperty("error", out var errProp))
                    ProgressSubtitle.Text = errProp.GetString();
            });
        }
    }
}
