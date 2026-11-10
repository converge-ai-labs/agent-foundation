param([Parameter(Mandatory=$true)][string]$StatePath, [switch]$Extended)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName System.Xaml
Add-Type -AssemblyName UIAutomationProvider
Add-Type -AssemblyName UIAutomationTypes
Add-Type -ReferencedAssemblies @(
    [Windows.Controls.GroupBox].Assembly.Location,
    [Windows.Media.Visual].Assembly.Location,
    [Windows.DependencyObject].Assembly.Location,
    [Windows.Markup.IQueryAmbient].Assembly.Location,
    [Windows.Automation.Provider.IInvokeProvider].Assembly.Location,
    [Windows.Automation.AutomationElementIdentifiers].Assembly.Location
) -TypeDefinition @'
using System.Windows;
using System.Windows.Controls;
using System.Windows.Automation.Peers;
using System.Windows.Automation.Provider;
using System.IO;
using System.Threading;
public sealed class A13nRawOnlyGroup : GroupBox {
    protected override AutomationPeer OnCreateAutomationPeer() {
        return new A13nRawOnlyGroupPeer(this);
    }
}
public sealed class A13nRawOnlyGroupPeer : GroupBoxAutomationPeer {
    public A13nRawOnlyGroupPeer(GroupBox owner) : base(owner) { }
    protected override bool IsControlElementCore() { return false; }
    protected override bool IsContentElementCore() { return false; }
}
public sealed class A13nGatedButton : Button {
    public string GatePath;
    public bool BlockName;
    public int Invocations;
    protected override AutomationPeer OnCreateAutomationPeer() {
        return new A13nGatedButtonPeer(this);
    }
}
public sealed class A13nGatedButtonPeer : ButtonAutomationPeer, IInvokeProvider {
    private readonly A13nGatedButton button;
    public A13nGatedButtonPeer(A13nGatedButton owner) : base(owner) { button = owner; }
    public override object GetPattern(PatternInterface pattern) {
        return pattern == PatternInterface.Invoke ? this : base.GetPattern(pattern);
    }
    public void Invoke() {
        button.Invocations++;
        button.BlockName = true;
        File.WriteAllText(button.GatePath + ".invoked", button.Invocations.ToString());
    }
    protected override string GetNameCore() {
        if (button.BlockName || File.Exists(button.GatePath + ".arm")) {
            File.WriteAllText(button.GatePath + ".entered", "true");
            // A real provider call remains blocked until the independent test
            // driver releases it. The safety cap only prevents a stranded GUI.
            var deadline = System.DateTime.UtcNow.AddSeconds(20);
            while (!File.Exists(button.GatePath + ".release")) {
                if (System.DateTime.UtcNow >= deadline) throw new System.TimeoutException("Fixture gate was not released");
                Thread.Sleep(10);
            }
            button.BlockName = false;
        }
        return base.GetNameCore();
    }
}
'@
[xml]$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Envd accessibility fixture" Width="900" Height="700"
        AutomationProperties.Name="Envd accessibility fixture">
  <ScrollViewer x:Name="Scroll" VerticalScrollBarVisibility="Auto" AutomationProperties.Name="Fixture scroll area">
    <StackPanel x:Name="FixtureLayout" Margin="24">
      <Button x:Name="Marker" AutomationProperties.Name="Projection marker" Height="40" Background="#17B96A" BorderThickness="0">
        <Button.Template><ControlTemplate TargetType="Button"><Border Background="{TemplateBinding Background}"/></ControlTemplate></Button.Template>
      </Button>
      <Button x:Name="Apply" Content="Apply changes" AutomationProperties.Name="Apply changes" AutomationProperties.AutomationId="apply-changes" Height="36"/>
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
$rawGroup = New-Object A13nRawOnlyGroup
[Windows.Automation.AutomationProperties]::SetName($rawGroup, 'Raw-only boundary')
$scopedChild = New-Object Windows.Controls.Button
$scopedChild.Content = 'Scoped child'
[Windows.Automation.AutomationProperties]::SetName($scopedChild, 'Scoped child')
$rawGroup.Content = $scopedChild
$window.FindName('FixtureLayout').Children.Insert(0, $rawGroup)
$editor = $window.FindName('Editor')
$document = $window.FindName('Document')
$paragraph = New-Object Windows.Documents.Paragraph
$unicode = 'Hello ' + [char]0x4e16 + [char]0x754c + [char]::ConvertFromUtf32(0x1f600)
$paragraph.Inlines.Add((New-Object Windows.Documents.Run($unicode)))
$document.Document.Blocks.Clear()
$document.Document.Blocks.Add($paragraph)
$script:markerClicks = 0
$window.FindName('Marker').Add_Click({ $script:markerClicks++ })
$unicodeName = 'Stra' + [char]0xdf + 'e ' + [char]0x3a3 + [char]0x3c2 + ' ' + $unicode
[Windows.Automation.AutomationProperties]::SetHelpText($editor, $unicodeName)
$dynamic = New-Object Windows.Controls.StackPanel
$outside = New-Object Windows.Controls.StackPanel
$script:moved = $false
$script:removed = $false
$script:moveCount = 0
$script:removeCount = 0
if ($Extended) {
    $layout = $window.FindName('FixtureLayout')
    $unicodeButton = New-Object Windows.Controls.Button
    $unicodeButton.Content = $unicodeName
    [Windows.Automation.AutomationProperties]::SetName($unicodeButton, $unicodeName)
    [Windows.Automation.AutomationProperties]::SetAutomationId($unicodeButton, $unicodeName)
    [void]$layout.Children.Add($unicodeButton)
    $move = New-Object Windows.Controls.Button
    $move.Content = 'Move dynamic anchor'
    [Windows.Automation.AutomationProperties]::SetName($move, $move.Content)
    $move.Add_Click({
        $anchor = $dynamic.Children[1]
        $dynamic.Children.Remove($anchor)
        [void]$outside.Children.Add($anchor)
        $script:moved = $true
        $script:moveCount++
    })
    [void]$layout.Children.Add($move)
    $remove = New-Object Windows.Controls.Button
    $remove.Content = 'Remove dynamic anchor'
    [Windows.Automation.AutomationProperties]::SetName($remove, $remove.Content)
    $remove.Add_Click({ $dynamic.Children.RemoveAt(1); $script:removed = $true; $script:removeCount++ })
    [void]$layout.Children.Add($remove)
    $gate = New-Object A13nGatedButton
    $gate.Content = 'Gated action'
    $gate.GatePath = $StatePath + '.gate'
    [Windows.Automation.AutomationProperties]::SetName($gate, 'Gated action')
    [void]$layout.Children.Add($gate)
    $group = New-Object Windows.Controls.GroupBox
    [Windows.Automation.AutomationProperties]::SetName($group, 'Dynamic tree')
    $group.Content = $dynamic
    for ($i = 0; $i -lt 280; $i++) {
        $child = New-Object Windows.Controls.Button
        $child.Content = ('Dynamic item {0:d3}' -f $i)
        [Windows.Automation.AutomationProperties]::SetName($child, $child.Content)
        [void]$dynamic.Children.Add($child)
    }
    [void]$layout.Children.Add($group)
    $outsideGroup = New-Object Windows.Controls.GroupBox
    [Windows.Automation.AutomationProperties]::SetName($outsideGroup, 'Outside tree')
    $outsideGroup.Content = $outside
    [void]$layout.Children.Add($outsideGroup)
}
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
        marker_clicks = $script:markerClicks; moved = $script:moved; removed = $script:removed
        dynamic_count = $dynamic.Children.Count; outside_count = $outside.Children.Count
        move_count = $script:moveCount; remove_count = $script:removeCount
        scroll_offset = $window.FindName('Scroll').VerticalOffset
        scrollable_height = $window.FindName('Scroll').ScrollableHeight
        focused_editor = $editor.IsKeyboardFocused; window_state = $window.WindowState.ToString()
        window_left = $window.Left; window_top = $window.Top; window_width = $window.ActualWidth; window_height = $window.ActualHeight
        dpi_x = [Windows.Media.VisualTreeHelper]::GetDpi($window).DpiScaleX
        marker_x = $window.FindName('Marker').PointToScreen((New-Object Windows.Point(0,0))).X
        marker_y = $window.FindName('Marker').PointToScreen((New-Object Windows.Point(0,0))).Y
        marker_width = $window.FindName('Marker').ActualWidth; marker_height = $window.FindName('Marker').ActualHeight
    } | ConvertTo-Json -Compress
    try { [IO.File]::WriteAllText($StatePath, $state, (New-Object Text.UTF8Encoding($false))) }
    catch [IO.IOException] { } # A concurrent EIP reader can briefly own the file.
})
$window.Add_Closed({ $timer.Stop() })
$timer.Start()
[void]$window.ShowDialog()
