# Offline PowerShell 5.1-compatible argument checks. Does not invoke WSL or change the machine.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'windows_setup.ps1')

function Assert-Equal($Actual, $Expected) {
    if ($Actual -cne $Expected) { throw "Expected [$Expected], received [$Actual]." }
}
Assert-Equal (ConvertFrom-WslText @("U`0b`0u`0n`0t`0u`0-24.04`r`n")) 'Ubuntu-24.04'
Assert-Equal (ConvertTo-NativeArgument '/home/user/project') '/home/user/project'
Assert-Equal (ConvertTo-NativeArgument '/home/user/project demo') '"/home/user/project demo"'
Assert-PortablePath 'sewer agent'
Assert-PortablePath '/home/user/sewer-agent'
foreach ($invalid in @("bad`npath", 'bad"path', '-option', 'bad`path')) {
    $rejected = $false
    try { Assert-PortablePath $invalid } catch { $rejected = $true }
    if (-not $rejected) { throw 'Unsafe path was accepted.' }
}
$tokens = $null
$parseErrors = $null
[Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $PSScriptRoot 'windows_setup.ps1'), [ref]$tokens, [ref]$parseErrors) | Out-Null
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }

# Replace process discovery with a function: no Windows feature is touched by these branches.
$script:FakeScenario = 'missing'
$script:FakeCalls = @()
function Get-Command {
    param($Name, $ErrorAction)
    if ($Name -eq 'wsl.exe') { return [pscustomobject]@{ Source = 'Invoke-FakeWsl' } }
    throw "Unexpected process lookup: $Name"
}
function Test-Administrator { return $script:FakeScenario -ne 'not-admin' }
function Invoke-FakeWsl {
    $script:FakeCalls += ($args -join ' ')
    $global:LASTEXITCODE = 0
    if ($args[0] -eq '--list') {
        if ($script:FakeScenario -in @('wsl1', 'root-user')) { return 'Ubuntu-24.04' }
    } elseif ($args[0] -eq '--install') {
        if ($script:FakeScenario -eq 'install-failed') { $global:LASTEXITCODE = 17 }
    } elseif ('uname' -in $args) {
        if ($script:FakeScenario -eq 'wsl1') { return '4.4.0-Microsoft' }
        return '6.6.0-microsoft-standard-WSL2'
    } elseif ('id' -in $args) {
        return 'root'
    } else { throw 'Unexpected WSL command in offline test.' }
}
$Action = 'setup'
Assert-Equal (Invoke-WindowsSetup) 10
if (-not ($script:FakeCalls -contains '--install --distribution Ubuntu-24.04 --no-launch --web-download')) {
    throw 'Missing distribution did not use the documented install command.'
}
foreach ($scenario in @('not-admin', 'install-failed', 'wsl1', 'root-user')) {
    $script:FakeScenario = $scenario
    $script:FakeCalls = @()
    $failed = $false
    try { Invoke-WindowsSetup | Out-Null } catch { $failed = $true }
    if (-not $failed) { throw "The $scenario branch must stop with a useful message." }
    if ($scenario -eq 'not-admin' -and ($script:FakeCalls -match '--install')) {
        throw 'Installer ran despite missing administrator access.'
    }
}
$Action = 'status'
$script:FakeScenario = 'missing'
$script:FakeCalls = @()
$failed = $false
try { Invoke-WindowsSetup | Out-Null } catch { $failed = $true }
if (-not $failed -or ($script:FakeCalls -match '--install')) { throw 'Status attempted installation.' }
Write-Output 'PowerShell syntax, quoting, WSL initialization, failure and read-only status checks passed.'
