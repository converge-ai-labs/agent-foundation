# Test setup only: simulate one independently held key, outside computer-use.
param([int]$ScanCode, [switch]$Release)
$ErrorActionPreference = 'Stop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class ExternalKey {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, IntPtr process);
    [DllImport("user32.dll")] public static extern IntPtr GetKeyboardLayout(uint thread);
    [DllImport("user32.dll")] public static extern uint MapVirtualKeyEx(uint code, uint type, IntPtr layout);
    [DllImport("user32.dll")] public static extern void keybd_event(byte key, byte scan, uint flags, UIntPtr extra);
}
'@
$thread = [ExternalKey]::GetWindowThreadProcessId([ExternalKey]::GetForegroundWindow(), [IntPtr]::Zero)
$layout = [ExternalKey]::GetKeyboardLayout($thread)
$key = [ExternalKey]::MapVirtualKeyEx($ScanCode, 3, $layout)
$flags = 0
if ($Release) { $flags = 2 }
[ExternalKey]::keybd_event([byte]$key, [byte]$ScanCode, $flags, [UIntPtr]::Zero)
