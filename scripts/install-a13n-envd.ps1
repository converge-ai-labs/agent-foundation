#Requires -Version 5.1
# Standalone installer. Dot-source to load functions without installing anything.

function Show-EnvdInstallerHelp {
    @'
Usage: install-a13n-envd.ps1 [--version X.Y.Z[-rc.N]] [--install-dir ABSOLUTE_PATH]
                             [--add-to-path | --no-add-to-path] [--help]

Install a verified a13n-envd release from converge-ai-labs/agent-foundation.
Without --version, select the newest stable a13n-envd GitHub Release.
Defaults: A13N_ENVD_VERSION, A13N_ENVD_INSTALL_DIR, A13N_ENVD_ADD_TO_PATH (1/0).
Flags override environment values. The default directory is %LOCALAPPDATA%\A13N\bin.
PATH is unchanged by default; --add-to-path updates only the Windows user PATH.
The installer does not run the binary, register a service, or start a daemon.
'@
}

function Test-EnvdVersion {
    param([string]$Version, [switch]$StableOnly)
    $pattern = '\A(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)'
    if (-not $StableOnly) { $pattern += '(?:-rc\.[1-9][0-9]*)?' }
    return $Version -cmatch ($pattern + '\z')
}

function Resolve-EnvdInstallDirectory {
    param([string]$Directory)
    # IsPathRooted alone also accepts drive-relative C:bin and root-relative \bin.
    if ($Directory -notmatch '\A(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))' -or
        $Directory -match '\A\\\\[?.][\\/]') {
        throw 'The install directory must be an absolute Windows drive or UNC path.'
    }
    return [IO.Path]::GetFullPath($Directory)
}

function Read-EnvdInstallerOptions {
    param([string[]]$Arguments)
    $version = $env:A13N_ENVD_VERSION
    $directory = $env:A13N_ENVD_INSTALL_DIR
    $pathFlag = $null
    $help = $false
    for ($i = 0; $i -lt $Arguments.Count; $i++) {
        $arg = $Arguments[$i]
        switch -CaseSensitive ($arg) {
            '--version' {
                if (++$i -ge $Arguments.Count -or [string]::IsNullOrEmpty($Arguments[$i])) {
                    throw '--version requires a canonical version.'
                }
                $version = $Arguments[$i]
            }
            '--install-dir' {
                if (++$i -ge $Arguments.Count -or [string]::IsNullOrEmpty($Arguments[$i])) {
                    throw '--install-dir requires an absolute directory.'
                }
                $directory = $Arguments[$i]
            }
            '--add-to-path' {
                if ($pathFlag -eq $false) { throw 'PATH flags are mutually exclusive.' }
                $pathFlag = $true
            }
            '--no-add-to-path' {
                if ($pathFlag -eq $true) { throw 'PATH flags are mutually exclusive.' }
                $pathFlag = $false
            }
            '--help' { $help = $true }
            default { throw "Unknown argument: $arg" }
        }
    }
    if ($help) { return @{ Help = $true } }
    if ($version -and -not (Test-EnvdVersion $version)) {
        throw 'Version must be canonical X.Y.Z or X.Y.Z-rc.N (N >= 1); latest is not accepted.'
    }
    if ([string]::IsNullOrEmpty($directory)) {
        if ([string]::IsNullOrEmpty($env:LOCALAPPDATA)) {
            throw 'LOCALAPPDATA is unavailable; supply --install-dir.'
        }
        $directory = [IO.Path]::Combine($env:LOCALAPPDATA, 'A13N', 'bin')
    }
    $directory = Resolve-EnvdInstallDirectory $directory
    if ($null -eq $pathFlag) {
        switch -CaseSensitive ($env:A13N_ENVD_ADD_TO_PATH) {
            '1' { $pathFlag = $true }
            '0' { $pathFlag = $false }
            { [string]::IsNullOrEmpty($_) } { $pathFlag = $false }
            default { throw 'A13N_ENVD_ADD_TO_PATH must be 1 or 0.' }
        }
    }
    if ($pathFlag -and $directory -match '[;\r\n]') {
        throw 'The install directory cannot contain a semicolon or newline when adding to PATH.'
    }
    return @{ Help = $false; Version = $version; InstallDirectory = $directory; AddToPath = $pathFlag }
}

function Get-EnvdNativeArchitecture {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This installer supports native Windows only.'
    }
    # WOW64 supplies the native architecture separately for an emulated process.
    if ($env:PROCESSOR_ARCHITEW6432) { return $env:PROCESSOR_ARCHITEW6432 }
    return $env:PROCESSOR_ARCHITECTURE
}

function Get-EnvdTarget {
    switch (Get-EnvdNativeArchitecture) {
        'AMD64' { return 'x86_64-pc-windows-msvc' }
        'ARM64' { return 'aarch64-pc-windows-msvc' }
        default { throw 'Unsupported Windows architecture; only x86_64 and ARM64 are supported.' }
    }
}

function Get-EnvdReleasePage {
    param([int]$Page)
    $releases = Invoke-RestMethod -Uri "https://api.github.com/repos/converge-ai-labs/agent-foundation/releases?per_page=100&page=$Page" `
        -Headers @{ Accept = 'application/vnd.github+json'; 'User-Agent' = 'a13n-envd-installer' } `
        -TimeoutSec 60 -ErrorAction Stop
    foreach ($release in $releases) { $release }
}

function Get-EnvdLatestStableVersion {
    for ($page = 1; ; $page++) {
        $releases = @(Get-EnvdReleasePage $page)
        if ($releases.Count -eq 0) { throw 'No stable a13n-envd GitHub Release was found.' }
        foreach ($release in $releases) {
            if ($release.draft -or $release.prerelease) { continue }
            $prefix = 'release/a13n-envd-v'
            if ($release.tag_name -and $release.tag_name.StartsWith($prefix, [StringComparison]::Ordinal)) {
                $version = $release.tag_name.Substring($prefix.Length)
                if (Test-EnvdVersion $version -StableOnly) { return $version }
            }
        }
    }
}

function Save-EnvdDownload {
    param([string]$Uri, [string]$Destination)
    Invoke-WebRequest -Uri $Uri -OutFile $Destination -UseBasicParsing `
        -Headers @{ 'User-Agent' = 'a13n-envd-installer' } -TimeoutSec 120 -ErrorAction Stop
}

function Assert-EnvdArchiveHash {
    param([string]$Archive, [string]$Checksums, [string]$AssetName)
    $hashes = @()
    foreach ($line in [IO.File]::ReadAllLines($Checksums)) {
        # Standard sha256sum text and binary markers; require the exact asset basename.
        if ($line -cmatch '\A([0-9a-fA-F]{64}) [ *](.+)\z' -and $Matches[2] -ceq $AssetName) {
            $hashes += $Matches[1]
        } elseif ($line -cmatch ('(?:\s|\*)' + [regex]::Escape($AssetName) + '\z')) {
            throw "Malformed checksum entry for $AssetName."
        }
    }
    if ($hashes.Count -ne 1) { throw "Expected exactly one SHA256SUMS entry for $AssetName." }
    $actual = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256 -ErrorAction Stop).Hash
    if ($actual -ine $hashes[0]) { throw "SHA256 mismatch for $AssetName." }
}

function Expand-EnvdBinary {
    param([string]$Archive, [string]$Candidate)
    Add-Type -AssemblyName System.IO.Compression
    $inputFile = [IO.File]::OpenRead($Archive)
    $zip = $null
    try {
        $zip = [IO.Compression.ZipArchive]::new($inputFile, [IO.Compression.ZipArchiveMode]::Read)
        if ($zip.Entries.Count -ne 2) { throw 'Archive must contain exactly a13n-envd.exe and LICENSE.' }
        $seen = @{}
        $binary = $null
        foreach ($entry in $zip.Entries) {
            $name = $entry.FullName
            # ZIP attributes carry both Unix file types and DOS directory/reparse flags.
            $unixType = ($entry.ExternalAttributes -shr 16) -band 0xF000
            $unsafeDos = $entry.ExternalAttributes -band 0x410
            if (($name -cne 'a13n-envd.exe' -and $name -cne 'LICENSE') -or $seen.ContainsKey($name) -or
                ($unixType -ne 0 -and $unixType -ne 0x8000) -or $unsafeDos -ne 0) {
                throw "Unsafe or duplicate archive entry: $name"
            }
            $seen[$name] = $true
            if ($name -ceq 'a13n-envd.exe') { $binary = $entry }
        }
        if ($null -eq $binary -or $binary.Length -eq 0) { throw 'Archive binary is empty or missing.' }
        $source = $binary.Open()
        try {
            $output = [IO.File]::Open($Candidate, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try { $source.CopyTo($output); $output.Flush($true) } finally { $output.Dispose() }
        } finally { $source.Dispose() }
    } finally {
        if ($null -ne $zip) { $zip.Dispose() }
        $inputFile.Dispose()
    }
}

function Publish-EnvdBinary {
    param([string]$Candidate, [string]$Destination)
    $existing = Get-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
    if ($null -ne $existing) {
        if (($existing.Attributes -band ([IO.FileAttributes]::Directory -bor [IO.FileAttributes]::ReparsePoint)) -ne 0) {
            throw 'The destination executable must not be a directory or reparse point.'
        }
        [IO.File]::Replace($Candidate, $Destination, [NullString]::Value)
    } else {
        # Move does not overwrite a destination that appears after the check.
        [IO.File]::Move($Candidate, $Destination)
    }
}

function Get-EnvdUserPath {
    return [Environment]::GetEnvironmentVariable('Path', [EnvironmentVariableTarget]::User)
}

function Set-EnvdUserPath {
    param([string]$Value)
    [Environment]::SetEnvironmentVariable('Path', $Value, [EnvironmentVariableTarget]::User)
}

function Add-EnvdToUserPath {
    param([string]$Directory)
    $current = Get-EnvdUserPath
    $normalized = $Directory.TrimEnd('\', '/')
    foreach ($entry in ($current -split ';')) {
        $expanded = [Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"')).TrimEnd('\', '/')
        if ([string]::Equals($expanded, $normalized, [StringComparison]::OrdinalIgnoreCase)) { return }
    }
    if ([string]::IsNullOrEmpty($current)) { $updated = $Directory }
    elseif ($current.EndsWith(';')) { $updated = $current + $Directory }
    else { $updated = $current + ';' + $Directory }
    Set-EnvdUserPath $updated
    Write-Host 'Updated Windows user PATH. Open a new terminal to use it.'
}

function Install-Envd {
    param([string[]]$Arguments)
    $ErrorActionPreference = 'Stop'
    $options = Read-EnvdInstallerOptions $Arguments
    if ($options.Help) { Show-EnvdInstallerHelp; return }
    $target = Get-EnvdTarget
    $staging = $null
    $oldTls = [Net.ServicePointManager]::SecurityProtocol
    try {
        # Windows PowerShell 5.1 can otherwise default to protocols GitHub rejects.
        [Net.ServicePointManager]::SecurityProtocol = $oldTls -bor [Net.SecurityProtocolType]::Tls12
        $version = $options.Version
        if ([string]::IsNullOrEmpty($version)) { $version = Get-EnvdLatestStableVersion }
        $asset = "a13n-envd-$version-$target.zip"
        $baseUrl = "https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v$version"
        $directory = $options.InstallDirectory
        [IO.Directory]::CreateDirectory($directory) | Out-Null
        $staging = [IO.Path]::Combine($directory, '.a13n-envd-install-' + [guid]::NewGuid().ToString('N'))
        [IO.Directory]::CreateDirectory($staging) | Out-Null
        $archive = [IO.Path]::Combine($staging, $asset)
        $checksums = [IO.Path]::Combine($staging, 'SHA256SUMS')
        $candidate = [IO.Path]::Combine($staging, 'a13n-envd.exe')
        Save-EnvdDownload "$baseUrl/$asset" $archive
        Save-EnvdDownload "$baseUrl/SHA256SUMS" $checksums
        Assert-EnvdArchiveHash $archive $checksums $asset
        Expand-EnvdBinary $archive $candidate
        $destination = [IO.Path]::Combine($directory, 'a13n-envd.exe')
        Publish-EnvdBinary $candidate $destination
        Write-Host "Installed a13n-envd $version to $destination"
        if ($options.AddToPath) { Add-EnvdToUserPath $directory }
    } finally {
        if ($null -ne $staging -and [IO.Directory]::Exists($staging)) {
            Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction Stop
        }
        [Net.ServicePointManager]::SecurityProtocol = $oldTls
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    try { Install-Envd -Arguments $args }
    catch { Write-Error -Message $_.Exception.Message -ErrorAction Continue; exit 1 }
}
