param([Parameter(Mandatory=$true)][string]$StatePath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Envd accessibility fixture" Width="900" Height="700"
        AutomationProperties.Name="Envd accessibility fixture">
  <ScrollViewer VerticalScrollBarVisibility="Auto" AutomationProperties.Name="Fixture scroll area">
    <StackPanel Margin="24">
      <Button x:Name="Apply" Content="Apply changes" AutomationProperties.Name="Apply changes" Height="36"/>
      <TextBox x:Name="Editor" AutomationProperties.Name="Unicode editor" Text="Initial text" Margin="0,8"/>
      <TextBox x:Name="ReadOnly" AutomationProperties.Name="Read-only editor" Text="Read-only text" IsReadOnly="True"/>
      <PasswordBox x:Name="Protected" AutomationProperties.Name="Protected editor" Password="fixture-secret" Margin="0,8"/>
      <CheckBox x:Name="Toggle" Content="Enable feature" AutomationProperties.Name="Enable feature"/>
      <Slider x:Name="Numeric" AutomationProperties.Name="Numeric value" Minimum="0" Maximum="100" Value="20"/>
      <Expander x:Name="Expand" Header="Details" AutomationProperties.Name="Details">
        <TextBlock Text="Expanded detail"/>
      </Expander>
      <ListBox x:Name="Items" AutomationProperties.Name="Options" SelectionMode="Multiple" Height="90">
        <ListBoxItem Content="Alpha" AutomationProperties.Name="Alpha"/>
        <ListBoxItem Content="Beta" AutomationProperties.Name="Beta"/>
        <ListBoxItem Content="Gamma" AutomationProperties.Name="Gamma"/>
      </ListBox>
      <RichTextBox x:Name="Document" AutomationProperties.Name="Native document" Height="120"/>
      <Button x:Name="Disabled" Content="Disabled action" AutomationProperties.Name="Disabled action" IsEnabled="False"/>
      <Border Height="600"><TextBlock Text="Scrollable content"/></Border>
    </StackPanel>
  </ScrollViewer>
</Window>
'@
$reader = New-Object System.Xml.XmlNodeReader $xaml
$window = [Windows.Markup.XamlReader]::Load($reader)
$editor = $window.FindName('Editor')
$document = $window.FindName('Document')
$paragraph = New-Object Windows.Documents.Paragraph
$unicode = 'Hello ' + [char]0x4e16 + [char]0x754c + [char]::ConvertFromUtf32(0x1f600)
$paragraph.Inlines.Add((New-Object Windows.Documents.Run($unicode)))
$document.Document.Blocks.Clear()
$document.Document.Blocks.Add($paragraph)
$script:clicks = 0
$window.FindName('Apply').Add_Click({ $script:clicks++ })
$timer = New-Object Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(50)
$timer.Add_Tick({
    $selection = @($window.FindName('Items').SelectedItems | ForEach-Object { $_.Content })
    $state = @{
        ready = $window.IsVisible; pid = $PID; clicks = $script:clicks; text = $editor.Text
        checked = $window.FindName('Toggle').IsChecked; numeric = $window.FindName('Numeric').Value
        expanded = $window.FindName('Expand').IsExpanded; selection = $selection
        document_selection = $document.Selection.Text
    } | ConvertTo-Json -Compress
    try { [IO.File]::WriteAllText($StatePath, $state, (New-Object Text.UTF8Encoding($false))) }
    catch [IO.IOException] { } # A concurrent EIP reader can briefly own the file.
})
$window.Add_Closed({ $timer.Stop() })
$timer.Start()
[void]$window.ShowDialog()
