$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$url = 'http://127.0.0.1:8788/'
function Test-Workbench {
    try {
        $health = Invoke-RestMethod -Uri ($url + 'api/health') -TimeoutSec 2
        return $health.status -eq 'ok' -and $health.mode -eq 'local-single-user'
    } catch { return $false }
}
try {
    if (-not (Test-Workbench)) {
        $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $python)) { throw '找不到工作台的 Python 环境。' }
        Start-Process -FilePath $python -ArgumentList '-m','backend' -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $PSScriptRoot 'server.out.log') -RedirectStandardError (Join-Path $PSScriptRoot 'server.err.log') | Out-Null
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep -Milliseconds 500
            if (Test-Workbench) { break }
        }
        if (-not (Test-Workbench)) { throw '工作台未能启动。请检查项目目录中的 server.err.log。' }
    }
    Start-Process $url
} catch {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show($_.Exception.Message, 'Blue 产品经理工作台') | Out-Null
}
