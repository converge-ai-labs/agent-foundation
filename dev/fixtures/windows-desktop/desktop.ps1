# Disposable Windows GUI fixture. Observes application events; never injects input.
param([string]$StatePath = "$PSScriptRoot\desktop.json", [string]$KeyboardLayout = '')
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -ReferencedAssemblies System.Windows.Forms,System.Drawing -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Windows.Forms;
public class DesktopProbe : Form {
    public int VerticalWheel, HorizontalWheel;
    [DllImport("user32.dll")] public static extern short GetAsyncKeyState(int key);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern IntPtr LoadKeyboardLayout(string name, uint flags);
    [DllImport("user32.dll")] public static extern IntPtr GetKeyboardLayout(uint thread);
    [DllImport("user32.dll")] public static extern bool SetProcessDpiAwarenessContext(IntPtr context);
    protected override void WndProc(ref Message m) {
        if (m.Msg == 0x20A) VerticalWheel += (short)((long)m.WParam >> 16);
        if (m.Msg == 0x20E) HorizontalWheel += (short)((long)m.WParam >> 16);
        base.WndProc(ref m);
    }
}
'@
[void][DesktopProbe]::SetProcessDpiAwarenessContext([IntPtr](-4))
$form = New-Object DesktopProbe
$form.Text = 'Agent Foundation Windows Desktop Fixture'
$form.FormBorderStyle = 'None'
$form.StartPosition = 'Manual'
$form.Location = New-Object System.Drawing.Point(0, 0)
$form.Size = New-Object System.Drawing.Size(1000, 700)
$form.BackColor = [System.Drawing.Color]::White
$form.KeyPreview = $true
$script:clicks = 0
$script:buttons = @()
$script:keys = @()
$script:drag = @()
$script:dragging = $false
$label = New-Object System.Windows.Forms.Label
$label.Text = 'Click this red target, then type below. Drag on the white area.'
$label.Location = New-Object System.Drawing.Point(40, 60)
$label.Size = New-Object System.Drawing.Size(700, 90)
$label.BackColor = [System.Drawing.Color]::Red
$label.Add_MouseDown({ param($s,$e)
    $script:clicks++
    $script:buttons += $e.Button.ToString()
    $label.BackColor = [System.Drawing.Color]::Lime
    $text.Focus()
})
$text = New-Object System.Windows.Forms.TextBox
$text.Multiline = $true
$text.AcceptsTab = $true
$text.AcceptsReturn = $true
$text.Location = New-Object System.Drawing.Point(40, 180)
$text.Size = New-Object System.Drawing.Size(700, 130)
$text.Font = New-Object System.Drawing.Font('Segoe UI', 16)
$form.Controls.Add($label)
$form.Controls.Add($text)
$form.Add_KeyDown({ param($s,$e) $script:keys += $e.KeyData.ToString() })
$form.Add_MouseDown({ param($s,$e)
    $script:buttons += $e.Button.ToString()
    $script:dragging = $true
    $form.Capture = $true
    $form.Focus()
})
$form.Add_MouseMove({ param($s,$e)
    if ($script:dragging) { $script:drag += ,@($e.X, $e.Y) }
})
$form.Add_MouseUp({ param($s,$e)
    $script:dragging = $false
    $form.Capture = $false
})
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 100
$timer.Add_Tick({
    $held = @(1,2,4,16,17,18,65,81,91,92 | Where-Object { [DesktopProbe]::GetAsyncKeyState($_) -lt 0 })
    $state = @{
        ready = $true; clicks = $script:clicks; buttons = $script:buttons
        keys = $script:keys; text = $text.Text; drag = $script:drag
        dragging = $script:dragging; held = $held
        vertical_wheel = $form.VerticalWheel; horizontal_wheel = $form.HorizontalWheel
        keyboard_layout = [DesktopProbe]::GetKeyboardLayout(0).ToInt64()
        screen_width = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width
        screen_height = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Height
    }
    # A concurrent EIP reader can temporarily hold the Windows file share lock.
    # Publish the next timer snapshot instead of opening a modal exception dialog.
    try {
        [System.IO.File]::WriteAllText($StatePath, ($state | ConvertTo-Json -Depth 6), [System.Text.UTF8Encoding]::new($false))
    } catch [System.IO.IOException] {}
})
$form.Add_Shown({
    if ($KeyboardLayout) { [void][DesktopProbe]::LoadKeyboardLayout($KeyboardLayout, 1) }
    $form.Activate()
    $timer.Start()
})
try { [System.Windows.Forms.Application]::Run($form) }
finally { $timer.Dispose(); $form.Dispose() }
