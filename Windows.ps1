param([ValidateSet('Setup','Start','Test')][string]$Action = 'Start')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = '1'
function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe exited with code $LASTEXITCODE" }
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if ($Action -eq 'Setup') {
    if (!(Test-Path 'integrations\agent-workshop\blueprint\web\server.py') -or !(Test-Path 'integrations\ai-product-starter-kit\README.md')) {
        if (!(Test-Path '.git')) { throw 'Original tools are missing. Clone this repository with --recurse-submodules.' }
        Invoke-Checked git @('submodule','update','--init','--recursive')
    }
    if (!(Test-Path $python)) { Invoke-Checked py @('-3.12','-m','venv','.venv') }
    Invoke-Checked $python @('-m','pip','install','-r','requirements-workbench.lock.txt')

    Invoke-Checked npm.cmd @('exec', '--yes', '--package=pnpm@11.25.0', '--', 'pnpm', '--dir', 'workbench-ui', 'install', '--frozen-lockfile')
    Invoke-Checked npm.cmd @('exec', '--yes', '--package=pnpm@11.25.0', '--', 'pnpm', '--dir', 'workbench-ui', 'run', 'build')
    if (!(Test-Path '.env')) { Copy-Item '.env.workbench.example' '.env' }

    Write-Host 'Setup completed. Run Windows.ps1 Start to launch.'
    exit 0
}
if (!(Test-Path $python)) { throw 'Run Windows.ps1 Setup first.' }
if ($Action -eq 'Test') {
    Invoke-Checked $python @('scripts/check_harness_runtime.py')
    Invoke-Checked $python @('-m','pytest','tests/workbench','-q')
    exit 0
}
Write-Host 'Open http://127.0.0.1:8788/ in Edge or Chrome. Press Ctrl+C to stop.'
Invoke-Checked $python @('-m','backend')
