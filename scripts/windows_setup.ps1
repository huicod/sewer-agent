#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('setup', 'start', 'stop', 'status')]
    [string]$Action = 'setup',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    [string]$Distro = 'Ubuntu-24.04',
    [string]$InstallDir = 'sewer-agent',
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [ValidatePattern('^[a-z0-9][a-z0-9_-]{0,45}$')]
    [string]$ProjectName = 'sewer-agent-wsl'
)

$ErrorActionPreference = 'Stop'

function ConvertFrom-WslText([object[]]$Lines) {
    return (($Lines -join "`n") -replace "`0", '' -replace '^\uFEFF', '').Trim()
}

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-PortablePath([string]$Value) {
    # Arguments travel through Windows' native command line to WSL, then as argv to Bash.
    if ($Value -match '[\x00-\x1F"`]' -or $Value.StartsWith('-')) {
        throw 'The path must not contain control characters, double quotes or backticks, or begin with a dash.'
    }
}

function ConvertTo-NativeArgument([string]$Value) {
    # Used only by Start-Process, whose ArgumentList is joined into one command line in PS 5.1.
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + (($Value -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Invoke-WindowsSetup {
    Assert-PortablePath $InstallDir
    $source = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    Assert-PortablePath $source
    $wslCommand = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wslCommand) {
        throw 'WSL is unavailable. Install current Windows 11 updates, then run wsl --install -d Ubuntu-24.04 in administrator PowerShell.'
    }
    $wsl = $wslCommand.Source
    $installedText = & $wsl --list --quiet 2>$null
    $listed = $LASTEXITCODE -eq 0
    $installed = (ConvertFrom-WslText $installedText) -split "`n" | ForEach-Object { $_.Trim() }
    if (-not $listed -or $Distro -notin $installed) {
        if ($Action -ne 'setup') { throw "Distribution $Distro is not installed. Run -Action setup first." }
        if (-not (Test-Administrator)) {
            throw 'Open PowerShell as administrator under your usual Windows account and run this setup command again.'
        }
        & $wsl --install --distribution $Distro --no-launch --web-download
        if ($LASTEXITCODE -notin @(0, 3010)) { throw "WSL installation stopped (exit $LASTEXITCODE). Resolve the Windows message and repeat setup." }
        Write-Host 'WSL installation was requested. Restart Windows if requested by the installer.'
        Write-Host "Then run: wsl -d $Distro"
        Write-Host 'Complete Ubuntu username/password creation, exit Ubuntu, and repeat the same setup command.'
        return 10
    }

    $kernel = ConvertFrom-WslText (& $wsl -d $Distro --exec uname -r)
    if ($LASTEXITCODE -ne 0) { throw "Open wsl -d $Distro, finish Ubuntu initialization, then repeat this command." }
    if ($kernel -notmatch '(?i)(wsl2|microsoft-standard)') {
        throw "This distribution is not WSL 2. In administrator PowerShell run: wsl --set-version $Distro 2; then repeat setup."
    }
    $user = ConvertFrom-WslText (& $wsl -d $Distro --exec id -un)
    if ($LASTEXITCODE -ne 0 -or $user -eq 'root') {
        throw "Open wsl -d $Distro and finish creating a normal Ubuntu user. Its default user must not be root."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $source 'scripts\wsl_setup.sh'))) {
        throw 'Use the complete extracted source package; keep this script in its scripts folder.'
    }
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('sewer-wsl-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        $utf8 = New-Object Text.UTF8Encoding($false)
        foreach ($name in @('wsl_setup.sh', 'wsl_source.py')) {
            $content = [IO.File]::ReadAllText((Join-Path $PSScriptRoot $name)) -replace "`r`n", "`n"
            [IO.File]::WriteAllText((Join-Path $temporary $name), $content, $utf8)
        }
        $linuxTemporary = ConvertFrom-WslText (& $wsl -d $Distro --exec wslpath -a -u $temporary)
        if ($LASTEXITCODE -ne 0) { throw 'Unable to map the Windows temporary directory into WSL.' }
        $arguments = @('-d', $Distro, '--exec', 'bash', "$linuxTemporary/wsl_setup.sh",
            '--action', $Action, '--directory', $InstallDir)
        if ($Action -eq 'setup') {
            $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
            if (-not $tar) { throw 'Windows tar.exe is unavailable. Install current Windows updates and retry.' }
            $archive = Join-Path $temporary 'source.tar.gz'
            $paths = @('Dockerfile', 'compose.yaml', 'pyproject.toml', '.dockerignore', '.env.docker.example',
                'README.md', 'DOCKER.md', 'WINDOWS.md', 'backend', 'frontend', 'scripts', 'deploy', 'example')
            & $tar.Source -czf $archive -C $source --exclude=node_modules --exclude=dist --exclude=__pycache__ --exclude=.venv --exclude=.git @paths
            if ($LASTEXITCODE -ne 0) { throw 'Source packaging failed; the existing Ubuntu installation has not been changed.' }
            $arguments += @('--archive', "$linuxTemporary/source.tar.gz", '--port', "$Port", '--project', $ProjectName)
        }
        & $wsl @arguments
        $result = $LASTEXITCODE
        if ($result -eq 20) {
            Write-Host "After closing work in $Distro, run: wsl --terminate $Distro"
            Write-Host 'Then repeat the same setup command. Other distributions do not need to stop.'
            return 20
        }
        if ($result -ne 0) { throw "Deployment step failed (exit $result). Fix the message above and repeat the same command." }
        if ($Action -in @('setup', 'start')) {
            # Keep one Windows WSL client alive. The Linux flock makes repeated starts harmless.
            # Use the installed script, not the temporary source that is removed on exit.
            $linuxRoot = ConvertFrom-WslText (& $wsl -d $Distro --exec python3 "$linuxTemporary/wsl_source.py" path --directory $InstallDir)
            if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the installed directory for the WSL background client.' }
            $keep = @('-d', $Distro, '--exec', 'bash', "$linuxRoot/scripts/wsl_setup.sh", '--action', 'keepalive', '--directory', $InstallDir)
            $commandLine = ($keep | ForEach-Object { ConvertTo-NativeArgument $_ }) -join ' '
            Start-Process -WindowStyle Hidden -FilePath $wsl -ArgumentList $commandLine | Out-Null
            $held = $false
            for ($attempt = 0; $attempt -lt 15; $attempt++) {
                & $wsl -d $Distro --exec flock -n -E 7 "$linuxRoot/.wsl-control/keepalive.lock" true
                if ($LASTEXITCODE -eq 7) { $held = $true; break }
                if ($LASTEXITCODE -ne 0) { break }
                Start-Sleep -Milliseconds 200
            }
            if (-not $held) { throw 'Services started, but the WSL background client did not become ready. Repeat -Action start.' }
            Write-Host 'The WSL application is running. Use -Action stop to stop this project.'
        }
        return 0
    }
    finally {
        # This is the exact unique directory created above, never a computed installation target.
        $resolvedTemporary = [IO.Path]::GetFullPath($temporary)
        $temporaryParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        if ($resolvedTemporary.StartsWith($temporaryParent, [StringComparison]::OrdinalIgnoreCase) -and
            [IO.Path]::GetFileName($resolvedTemporary) -match '^sewer-wsl-[a-f0-9]{32}$') {
            Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
        }
    }
}

# Dot sourcing exposes the argument/validation helpers to offline tests only.
if ($MyInvocation.InvocationName -ne '.') {
    try { exit (Invoke-WindowsSetup) }
    catch { Write-Error $_; exit 1 }
}
