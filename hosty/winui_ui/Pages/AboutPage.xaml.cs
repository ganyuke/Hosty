using Microsoft.UI.Xaml.Controls;
using System.Reflection;

// To learn more about WinUI, the WinUI project structure,
// and more about our project templates, see: http://aka.ms/winui-project-info.

namespace winui_ui.Pages;

public sealed partial class AboutPage : Page
{
    public AboutPage()
    {
        InitializeComponent();
        VersionText.Text = Assembly.GetExecutingAssembly().GetName().Version?.ToString() ?? "Development";
    }
}
