using System;
using System.Text.Json;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace winui_ui.Pages;

public sealed partial class SettingsPage : Page
{
    private bool _isLoading;

    public SettingsPage()
    {
        InitializeComponent();
        Loaded += SettingsPage_Loaded;
    }

    private async void SettingsPage_Loaded(object sender, RoutedEventArgs e)
    {
        if (App.MainWindow?.IpcClient == null) return;

        _isLoading = true;
        try
        {
            var prefs = await App.MainWindow.IpcClient.SendRequestAsync("get_preferences");
            ApplyPreferences(prefs);
        }
        catch (Exception ex)
        {
            ShowError(ex.Message);
        }
        finally
        {
            _isLoading = false;
        }
    }

    private void ApplyPreferences(JsonElement prefs)
    {
        if (prefs.TryGetProperty("theme", out var themeProp))
        {
            string theme = themeProp.GetString() ?? "system";
            for (int i = 0; i < ThemeBox.Items.Count; i++)
            {
                if (ThemeBox.Items[i] is ComboBoxItem item && item.Tag?.ToString() == theme)
                {
                    ThemeBox.SelectedIndex = i;
                    break;
                }
            }
        }

        if (prefs.TryGetProperty("run_in_background_on_close", out var backgroundProp))
            BackgroundToggle.IsOn = backgroundProp.GetBoolean();

        if (prefs.TryGetProperty("auto_backup_on_stop", out var backupProp))
            AutoBackupToggle.IsOn = backupProp.GetBoolean();

        if (prefs.TryGetProperty("default_ram_mb", out var ramProp))
        {
            DefaultRamBox.Value = ramProp.GetInt32();
            UpdateRamText((int)DefaultRamBox.Value);
        }
    }

    private async void ThemeBox_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_isLoading || ThemeBox.SelectedItem is not ComboBoxItem item) return;
        string theme = item.Tag?.ToString() ?? "system";
        App.ApplyTheme(theme);
        await SavePreference("theme", theme);
    }

    private async void PreferenceToggle_Toggled(object sender, RoutedEventArgs e)
    {
        if (_isLoading || sender is not ToggleSwitch toggle) return;

        if (toggle == BackgroundToggle)
            await SavePreference("run_in_background_on_close", toggle.IsOn);
        else if (toggle == AutoBackupToggle)
            await SavePreference("auto_backup_on_stop", toggle.IsOn);
    }

    private async void DefaultRamBox_ValueChanged(NumberBox sender, NumberBoxValueChangedEventArgs args)
    {
        if (_isLoading || double.IsNaN(args.NewValue)) return;
        int ram = Math.Max(512, (int)Math.Round(args.NewValue / 256.0) * 256);
        UpdateRamText(ram);
        await SavePreference("default_ram_mb", ram);
    }

    private void UpdateRamText(int ram)
    {
        DefaultRamText.Text = $"{ram} MB ({Math.Round(ram / 1024.0, 1)} GB) for new servers.";
    }

    private async System.Threading.Tasks.Task SavePreference(string key, object value)
    {
        if (App.MainWindow?.IpcClient == null) return;
        try
        {
            ErrorBar.IsOpen = false;
            await App.MainWindow.IpcClient.SendRequestAsync("update_preference", new { key, value });
        }
        catch (Exception ex)
        {
            ShowError(ex.Message);
        }
    }

    private void ShowError(string message)
    {
        ErrorBar.Message = message;
        ErrorBar.IsOpen = true;
    }
}
