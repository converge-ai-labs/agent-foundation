#Requires -Version 5.1
# No Pester, downloads, daemon execution, or persistent environment changes.
# Run: powershell -NoProfile -File scripts/tests/test_install_envd_windows.ps1
#      pwsh -NoProfile -File scripts/tests/test_install_envd_windows.ps1
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../install-a13n-envd.ps1')
Add-Type -AssemblyName System.IO.Compression

function Assert-True {
    param([bool]$Condition, [string]$Message = 'Assertion failed')
    if (-not $Condition) { throw $Message }
}
function Assert-Equal {
    param($Actual, $Expected)
    if ($Actual -cne $Expected) { throw "Expected [$Expected], got [$Actual]." }
}
function Assert-Throws {
    param([scriptblock]$Body, [string]$Pattern = '*')
    try { & $Body } catch {
        if ($_.Exception.Message -notlike $Pattern) { throw "Unexpected error: $_" }
        return
    }
    throw "Expected failure matching: $Pattern"
}
function Test-Case {
    param([string]$Name, [scriptblock]$Body)
    & $Body
    $script:Passed++
    Write-Host "PASS $Name"
}
function New-ZipFixture {
    param([string]$Path, [object[]]$Entries)
    $file = [IO.File]::Open($Path, [IO.FileMode]::Create)
    $zip = [IO.Compression.ZipArchive]::new($file, [IO.Compression.ZipArchiveMode]::Create)
    try {
        foreach ($item in $Entries) {
            $entry = $zip.CreateEntry($item.Name)
            if ($item.ContainsKey('Attributes')) { $entry.ExternalAttributes = $item.Attributes }
            $stream = $entry.Open()
            try {
                $bytes = [Text.Encoding]::UTF8.GetBytes($item.Content)
                $stream.Write($bytes, 0, $bytes.Length)
            } finally { $stream.Dispose() }
        }
    } finally { $zip.Dispose(); $file.Dispose() }
}
function Write-Checksums {
    param([string]$Text)
    [IO.File]::WriteAllText($script:Checksums, $Text)
}
function Assert-NoStaging {
    Assert-Equal @(Get-ChildItem -LiteralPath $script:InstallDirectory -Force -Filter '.a13n-envd-install-*').Count 0
}

# Fail closed if a test accidentally reaches the network or persistent user PATH.
function Invoke-WebRequest { throw 'Unexpected real download' }
function Invoke-RestMethod { throw 'Unexpected real release request' }
function Get-EnvdUserPath { throw 'Unexpected user PATH read' }
function Set-EnvdUserPath { throw 'Unexpected user PATH write' }

$script:Passed = 0
$root = [IO.Path]::Combine([IO.Path]::GetTempPath(), 'a13n-envd-tests-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
$script:Archive = [IO.Path]::Combine($root, 'fixture.zip')
$script:Checksums = [IO.Path]::Combine($root, 'SHA256SUMS')
$script:Candidate = [IO.Path]::Combine($root, 'candidate.exe')
$script:InstallDirectory = [IO.Path]::Combine($root, 'install space [literal]')
$asset = 'a13n-envd-1.2.3-x86_64-pc-windows-msvc.zip'
$validEntries = @(@{ Name = 'a13n-envd.exe'; Content = 'new binary' }, @{ Name = 'LICENSE'; Content = 'license' })
$environmentNames = @('A13N_ENVD_VERSION', 'A13N_ENVD_INSTALL_DIR', 'A13N_ENVD_ADD_TO_PATH', 'LOCALAPPDATA')
$savedEnvironment = @{}
foreach ($name in $environmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, $null, 'Process')
}
$originalProcessPath = $env:PATH
try {
    Test-Case 'canonical stable and RC versions only' {
        foreach ($version in @('0.0.0', '1.2.3', '12.30.41', '1.2.3-rc.1', '1.2.3-rc.12')) {
            Assert-True (Test-EnvdVersion $version) $version
        }
        foreach ($version in @('', 'latest', 'v1.2.3', '01.2.3', '1.02.3', '1.2.03', '1.2', '1.2.3rc1',
                '1.2.3-rc.0', '1.2.3-rc.01', '1.2.3-RC.1', '1.2.3+build', '1.2.3/evil', "1.2.3`n")) {
            Assert-True (-not (Test-EnvdVersion $version)) $version
        }
        Assert-True (-not (Test-EnvdVersion '1.2.3-rc.1' -StableOnly))
    }
    Test-Case 'absolute Windows paths reject relative and device paths' {
        foreach ($path in @('bin', '.\bin', 'C:bin', '\bin', '/bin', '\\?\C:\bin', '\\.\C:\bin')) {
            Assert-Throws { Resolve-EnvdInstallDirectory $path } '*absolute Windows*'
        }
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            Assert-Equal (Resolve-EnvdInstallDirectory 'C:\some directory\bin') 'C:\some directory\bin'
            Assert-Equal (Resolve-EnvdInstallDirectory '\\server\share\bin') '\\server\share\bin'
        }
    }
    # Only the path conversion is mocked for optional Linux pwsh validation. Native
    # Windows runs exercise the actual validator for every installation below.
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        function Resolve-EnvdInstallDirectory {
            param([string]$Directory)
            if (-not [IO.Path]::IsPathRooted($Directory)) { throw 'Expected an absolute test path' }
            return [IO.Path]::GetFullPath($Directory)
        }
    }
    $env:LOCALAPPDATA = $root
    Test-Case 'defaults, environment values, and overriding invalid unused environment' {
        $options = Read-EnvdInstallerOptions @()
        Assert-Equal $options.InstallDirectory ([IO.Path]::Combine($root, 'A13N', 'bin'))
        Assert-True ([string]::IsNullOrEmpty($options.Version))
        Assert-Equal $options.AddToPath $false
        $env:A13N_ENVD_VERSION = '1.2.3-rc.1'
        $env:A13N_ENVD_INSTALL_DIR = $root
        $env:A13N_ENVD_ADD_TO_PATH = '1'
        $options = Read-EnvdInstallerOptions @()
        Assert-Equal $options.Version '1.2.3-rc.1'
        Assert-Equal $options.InstallDirectory $root
        Assert-Equal $options.AddToPath $true
        $env:A13N_ENVD_VERSION = 'latest'
        $env:A13N_ENVD_INSTALL_DIR = 'relative'
        $env:A13N_ENVD_ADD_TO_PATH = 'invalid'
        $options = Read-EnvdInstallerOptions @('--version', '2.3.4', '--install-dir', $root, '--no-add-to-path')
        Assert-Equal $options.Version '2.3.4'
        Assert-Equal $options.InstallDirectory $root
        Assert-Equal $options.AddToPath $false
        Assert-Equal (Read-EnvdInstallerOptions @('--version', '2.3.4', '--install-dir', $root, '--add-to-path')).AddToPath $true
        Assert-Equal (Read-EnvdInstallerOptions @('--help')).Help $true
        $env:A13N_ENVD_VERSION = $null
        $env:A13N_ENVD_INSTALL_DIR = $null
        Assert-Throws { Read-EnvdInstallerOptions @() } '*must be 1 or 0*'
        $env:A13N_ENVD_ADD_TO_PATH = '0'
        Assert-Equal (Read-EnvdInstallerOptions @()).AddToPath $false
        $env:A13N_ENVD_ADD_TO_PATH = $null
    }
    Test-Case 'PATH separators are rejected only for opt-in' {
        $directory = [IO.Path]::Combine($root, 'semi;colon')
        Assert-Equal (Read-EnvdInstallerOptions @('--install-dir', $directory, '--no-add-to-path')).InstallDirectory $directory
        Assert-Throws { Read-EnvdInstallerOptions @('--install-dir', $directory, '--add-to-path') } '*semicolon or newline*'
        # Windows itself disallows newlines in paths. Keep this check independent
        # of the OS path normalizer to verify the PATH-specific restriction too.
        function Resolve-EnvdInstallDirectory { param($Directory); return $Directory }
        foreach ($directory in @("C:\bin`nother", "C:\bin`rother")) {
            Assert-Throws { Read-EnvdInstallerOptions @('--install-dir', $directory, '--add-to-path') } '*semicolon or newline*'
        }
    }
    Test-Case 'public flags reject unknown, incomplete, conflicting, or invalid inputs' {
        foreach ($arguments in @(@('--version'), @('--install-dir'), @('--version', ''), @('--install-dir', ''),
                @('--version', 'latest'), @('--version', '1.2.3-rc.0'), @('--install-dir', 'relative'),
                @('--add-to-path', '--no-add-to-path'), @('--no-add-to-path', '--add-to-path'),
                @('--Version', '1.2.3'), @('-Version', '1.2.3'), @('--version=1.2.3'), @('--unknown'))) {
            Assert-Throws { Read-EnvdInstallerOptions $arguments }
        }
    }
    Test-Case 'native Windows architecture, including emulated process' {
        function Get-EnvdNativeArchitecture { return 'AMD64' }
        Assert-Equal (Get-EnvdTarget) 'x86_64-pc-windows-msvc'
        function Get-EnvdNativeArchitecture { return 'ARM64' }
        Assert-Equal (Get-EnvdTarget) 'aarch64-pc-windows-msvc'
        function Get-EnvdNativeArchitecture { return 'x86' }
        Assert-Throws { Get-EnvdTarget } '*Unsupported Windows architecture*'
    }
    Test-Case 'actual Windows native architecture probe' {
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            Assert-True ((Get-EnvdNativeArchitecture) -in @('AMD64', 'ARM64'))
            $native = $env:PROCESSOR_ARCHITEW6432
            $process = $env:PROCESSOR_ARCHITECTURE
            try {
                $env:PROCESSOR_ARCHITEW6432 = 'ARM64'
                $env:PROCESSOR_ARCHITECTURE = 'x86'
                Assert-Equal (Get-EnvdTarget) 'aarch64-pc-windows-msvc'
                $env:PROCESSOR_ARCHITEW6432 = 'AMD64'
                Assert-Equal (Get-EnvdTarget) 'x86_64-pc-windows-msvc'
                $env:PROCESSOR_ARCHITEW6432 = $null
                $env:PROCESSOR_ARCHITECTURE = 'ARM64'
                Assert-Equal (Get-EnvdTarget) 'aarch64-pc-windows-msvc'
                $env:PROCESSOR_ARCHITECTURE = 'x86'
                Assert-Throws { Get-EnvdTarget } '*Unsupported Windows architecture*'
            } finally {
                $env:PROCESSOR_ARCHITEW6432 = $native
                $env:PROCESSOR_ARCHITECTURE = $process
            }
        } else {
            Assert-Throws { Get-EnvdNativeArchitecture } '*native Windows only*'
        }
    }
    Test-Case 'paginated monorepo releases skip draft, RC, malformed, and other channels' {
        $script:Pages = @()
        function Invoke-RestMethod {
            param($Uri, $Headers, $TimeoutSec, $ErrorAction)
            Assert-True ($Uri -cmatch '\Ahttps://api.github.com/repos/converge-ai-labs/agent-foundation/releases\?per_page=100&page=([0-9]+)\z')
            $page = [int]$Matches[1]
            $script:Pages += $page
            if ($page -eq 1) {
                return @(
                    @{ tag_name = 'release/a13n-harness-v99.0.0'; draft = $false; prerelease = $false },
                    @{ tag_name = 'release/a13n-envd-v9.0.0-rc.1'; draft = $false; prerelease = $false },
                    @{ tag_name = 'release/a13n-envd-v8.0.0'; draft = $false; prerelease = $true },
                    @{ tag_name = 'release/a13n-envd-v7.0.0'; draft = $true; prerelease = $false },
                    @{ tag_name = 'release/a13n-envd-v06.0.0'; draft = $false; prerelease = $false }
                )
            }
            return @(
                @{ tag_name = 'release/a13n-envd-v1.2.3'; draft = $false; prerelease = $false },
                @{ tag_name = 'release/a13n-envd-v1.2.2'; draft = $false; prerelease = $false }
            )
        }
        Assert-Equal (Get-EnvdLatestStableVersion) '1.2.3'
        Assert-Equal ($script:Pages -join ',') '1,2'
        function Invoke-RestMethod { return @() }
        Assert-Throws { Get-EnvdLatestStableVersion } '*No stable*'
    }
    Test-Case 'checksum exact filename, markers, missing, malformed, duplicate, mismatch' {
        New-ZipFixture $Archive $validEntries
        $hash = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash
        Write-Checksums "$hash  $asset`n"
        Assert-EnvdArchiveHash $Archive $Checksums $asset
        Write-Checksums "$($hash.ToLowerInvariant()) *$asset`n"
        Assert-EnvdArchiveHash $Archive $Checksums $asset
        foreach ($text in @("$hash  other.zip", "$hash  ./$asset", "$hash  $asset.bak", "$hash  $($asset.ToUpperInvariant())")) {
            Write-Checksums $text
            Assert-Throws { Assert-EnvdArchiveHash $Archive $Checksums $asset } '*exactly one*'
        }
        Write-Checksums "bad  $asset"
        Assert-Throws { Assert-EnvdArchiveHash $Archive $Checksums $asset } '*Malformed checksum*'
        Write-Checksums "$hash  $asset`n$hash *$asset"
        Assert-Throws { Assert-EnvdArchiveHash $Archive $Checksums $asset } '*exactly one*'
        Write-Checksums "$hash  $asset`nbad  $asset"
        Assert-Throws { Assert-EnvdArchiveHash $Archive $Checksums $asset } '*Malformed checksum*'
        Write-Checksums "$('0' * 64)  $asset"
        Assert-Throws { Assert-EnvdArchiveHash $Archive $Checksums $asset } '*SHA256 mismatch*'
    }
    Test-Case 'stream only the nonempty executable from a validated two-file zip' {
        New-ZipFixture $Archive $validEntries
        Expand-EnvdBinary $Archive $Candidate
        Assert-Equal ([IO.File]::ReadAllText($Candidate)) 'new binary'
        Assert-True (-not [IO.File]::Exists([IO.Path]::Combine($root, 'LICENSE')))
        Remove-Item -LiteralPath $Candidate
    }
    Test-Case 'archive entry traversal, links, duplicates, directories, extra and empty entries' {
        $badEntries = @(
            @(@{ Name = '../a13n-envd.exe'; Content = 'bad' }, $validEntries[1]),
            @(@{ Name = 'dir/a13n-envd.exe'; Content = 'bad' }, $validEntries[1]),
            @(@{ Name = 'dir\a13n-envd.exe'; Content = 'bad' }, $validEntries[1]),
            @(@{ Name = '/a13n-envd.exe'; Content = 'bad' }, $validEntries[1]),
            @(@{ Name = 'C:\a13n-envd.exe'; Content = 'bad' }, $validEntries[1]),
            @(@{ Name = 'A13N-ENVD.EXE'; Content = 'bad' }, $validEntries[1]),
            @($validEntries[0], $validEntries[0]),
            @($validEntries[0], @{ Name = 'LICENSE/'; Content = '' }),
            @($validEntries[0], @{ Name = 'LICENSE'; Content = 'link'; Attributes = (0xA000 -shl 16) }),
            @(@{ Name = 'a13n-envd.exe'; Content = 'link'; Attributes = (0xA000 -shl 16) }, $validEntries[1]),
            @(@{ Name = 'a13n-envd.exe'; Content = 'dir'; Attributes = 0x10 }, $validEntries[1]),
            @(@{ Name = 'a13n-envd.exe'; Content = 'link'; Attributes = 0x400 }, $validEntries[1]),
            @(@{ Name = 'a13n-envd.exe'; Content = 'fifo'; Attributes = (0x1000 -shl 16) }, $validEntries[1]),
            @(@{ Name = 'a13n-envd.exe'; Content = '' }, $validEntries[1]),
            @($validEntries[0]),
            @($validEntries[0], $validEntries[1], @{ Name = 'extra'; Content = 'bad' })
        )
        foreach ($entries in $badEntries) {
            New-ZipFixture $Archive $entries
            Assert-Throws { Expand-EnvdBinary $Archive $Candidate }
            Assert-True (-not [IO.File]::Exists($Candidate)) 'Unsafe archive created a candidate'
        }
        [IO.File]::WriteAllText($Archive, 'not a zip')
        Assert-Throws { Expand-EnvdBinary $Archive $Candidate }
        Assert-True (-not [IO.File]::Exists($Candidate))
    }
    Test-Case 'atomic publication creates, replaces, and rejects directory destination' {
        $destination = [IO.Path]::Combine($root, 'published.exe')
        [IO.File]::WriteAllText($Candidate, 'first')
        Publish-EnvdBinary $Candidate $destination
        Assert-Equal ([IO.File]::ReadAllText($destination)) 'first'
        Assert-True (-not [IO.File]::Exists($Candidate))
        [IO.File]::WriteAllText($Candidate, 'second')
        Publish-EnvdBinary $Candidate $destination
        Assert-Equal ([IO.File]::ReadAllText($destination)) 'second'
        [IO.File]::WriteAllText($Candidate, 'third')
        Assert-Throws { Publish-EnvdBinary $Candidate $root } '*directory or reparse point*'
        Assert-Equal ([IO.File]::ReadAllText($Candidate)) 'third'
        Remove-Item -LiteralPath $Candidate
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            [IO.Directory]::CreateDirectory($InstallDirectory) | Out-Null
            $junction = [IO.Path]::Combine($root, 'destination-junction')
            New-Item -ItemType Junction -Path $junction -Target $root -Force | Out-Null
            try {
                [IO.File]::WriteAllText($Candidate, 'third')
                Assert-Throws { Publish-EnvdBinary $Candidate $junction } '*directory or reparse point*'
            } finally {
                [IO.Directory]::Delete($junction)
                Remove-Item -LiteralPath $Candidate
            }
        }
    }

    # Installation tests use real checksum, ZIP validation, staging and atomic file
    # operations. Only release transport/architecture and persistent PATH are faked.
    function Get-EnvdNativeArchitecture { return 'AMD64' }
    function Save-EnvdDownload {
        param([string]$Uri, [string]$Destination)
        $script:DownloadUris += $Uri
        Assert-True ($Uri.StartsWith('https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v'))
        if ($Uri.EndsWith('/SHA256SUMS')) {
            $name = [IO.Path]::GetFileName($script:DownloadUris[-2])
            $hash = (Get-FileHash -LiteralPath $script:Archive -Algorithm SHA256).Hash
            [IO.File]::WriteAllText($Destination, "$hash  $name`n")
        } else {
            [IO.File]::Copy($script:Archive, $Destination)
        }
    }
    [IO.Directory]::CreateDirectory($InstallDirectory) | Out-Null
    $installed = [IO.Path]::Combine($InstallDirectory, 'a13n-envd.exe')
    Test-Case 'install and replace explicit stable/RC; no PATH by default; cleanup' {
        $script:DownloadUris = @()
        New-ZipFixture $Archive $validEntries
        Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory)
        Assert-Equal ([IO.File]::ReadAllText($installed)) 'new binary'
        Assert-Equal $script:DownloadUris[0] "https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v1.2.3/$asset"
        Assert-NoStaging
        Install-Envd @('--version', '1.2.4-rc.1', '--install-dir', $InstallDirectory, '--no-add-to-path')
        Assert-True ($script:DownloadUris[2].EndsWith('/a13n-envd-1.2.4-rc.1-x86_64-pc-windows-msvc.zip'))
        $env:A13N_ENVD_ADD_TO_PATH = '1'
        try { Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory, '--no-add-to-path') }
        finally { $env:A13N_ENVD_ADD_TO_PATH = $null }
        Assert-NoStaging
    }
    Test-Case 'default version uses stable resolution; ARM64 selects native asset' {
        $script:DownloadUris = @()
        function Get-EnvdLatestStableVersion { return '3.2.1' }
        function Get-EnvdNativeArchitecture { return 'ARM64' }
        Install-Envd @('--install-dir', $InstallDirectory)
        Assert-True ($script:DownloadUris[0].EndsWith('/a13n-envd-3.2.1-aarch64-pc-windows-msvc.zip'))
        Assert-NoStaging
    }
    Test-Case 'failed verification must not open ZIP or replace previous binary' {
        function Assert-EnvdArchiveHash { throw 'injected hash failure' }
        function Expand-EnvdBinary { throw 'ZIP opened before verification' }
        Assert-Throws { Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory) } '*injected hash failure*'
        Assert-Equal ([IO.File]::ReadAllText($installed)) 'new binary'
        Assert-NoStaging
    }
    Test-Case 'download, malformed ZIP, partial extraction and publication failures clean staging' {
        function Save-EnvdDownload { throw 'injected download failure' }
        Assert-Throws { Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory) } '*injected download failure*'
        Assert-NoStaging
        Remove-Item Function:\Save-EnvdDownload
        [IO.File]::WriteAllText($Archive, 'not a zip')
        Assert-Throws { Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory) }
        Assert-Equal ([IO.File]::ReadAllText($installed)) 'new binary'
        Assert-NoStaging
        New-ZipFixture $Archive $validEntries
        function Expand-EnvdBinary {
            param($Archive, $Candidate)
            [IO.File]::WriteAllText($Candidate, 'partial')
            throw 'injected extraction failure'
        }
        Assert-Throws { Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory) } '*injected extraction failure*'
        Assert-Equal ([IO.File]::ReadAllText($installed)) 'new binary'
        Assert-NoStaging
        Remove-Item Function:\Expand-EnvdBinary
        function Publish-EnvdBinary { throw 'injected publication failure' }
        Assert-Throws { Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory) } '*injected publication failure*'
        Assert-Equal ([IO.File]::ReadAllText($installed)) 'new binary'
        Assert-NoStaging
    }
    Test-Case 'locked Windows destination leaves prior binary and cleans staging' {
        if ([Environment]::OSVersion.Platform -eq [PlatformID]::Win32NT) {
            $lock = [IO.File]::Open($installed, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
            try { Assert-Throws { Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory) } }
            finally { $lock.Dispose() }
            Assert-Equal ([IO.File]::ReadAllText($installed)) 'new binary'
            Assert-NoStaging
        }
    }
    Test-Case 'PATH opt-in uses user-only boundary and is idempotent' {
        $script:UserPath = 'C:\other'
        $script:PathWrites = 0
        function Get-EnvdUserPath { return $script:UserPath }
        function Set-EnvdUserPath { param($Value); $script:UserPath = $Value; $script:PathWrites++ }
        Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory, '--add-to-path')
        Assert-Equal $script:UserPath "C:\other;$InstallDirectory"
        Assert-Equal $script:PathWrites 1
        Add-EnvdToUserPath $InstallDirectory
        Assert-Equal $script:PathWrites 1
        $script:UserPath = 'C:\other'
        $env:A13N_ENVD_ADD_TO_PATH = '1'
        try { Install-Envd @('--version', '1.2.3', '--install-dir', $InstallDirectory) }
        finally { $env:A13N_ENVD_ADD_TO_PATH = $null }
        Assert-Equal $script:UserPath "C:\other;$InstallDirectory"
        Assert-Equal $script:PathWrites 2
        $script:PathWrites = 1
        $script:UserPath = 'C:\other;"C:\TOOLS\bin\"'
        Add-EnvdToUserPath 'c:\tools\bin'
        Assert-Equal $script:PathWrites 1
        $script:UserPath = '%LOCALAPPDATA%\bin;C:\other'
        Add-EnvdToUserPath "$env:LOCALAPPDATA\bin"
        Assert-Equal $script:PathWrites 1
        $script:UserPath = ''
        Add-EnvdToUserPath $InstallDirectory
        Assert-Equal $script:UserPath $InstallDirectory
        $script:UserPath = 'C:\other;'
        Add-EnvdToUserPath $InstallDirectory
        Assert-Equal $script:UserPath "C:\other;$InstallDirectory"
        Assert-Equal $env:PATH $originalProcessPath
        Assert-NoStaging
    }
    Test-Case 'help never downloads or probes platform' {
        function Get-EnvdTarget { throw 'Unexpected architecture probe' }
        $help = Install-Envd @('--help')
        Assert-True ($help -like '*Usage:*')
    }
    Write-Host "All $script:Passed installer tests passed."
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    Remove-Item -LiteralPath $root -Recurse -Force
}
