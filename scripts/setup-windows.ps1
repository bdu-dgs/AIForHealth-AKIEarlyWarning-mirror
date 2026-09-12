param([string]$PythonExecutable = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
function Invoke-Checked { param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed: $Command" }
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($PythonExecutable) {
        Invoke-Checked $PythonExecutable @('-m', 'venv', '.venv')
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Checked 'py' @('-3', '-m', 'venv', '.venv')
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        Invoke-Checked 'python' @('-m', 'venv', '.venv')
    } else { throw 'Source setup needs Python 3.11+ and Node 22.13+ installed and available on PATH.' }
}
Invoke-Checked '.\.venv\Scripts\python.exe' @('-c', 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"')
Invoke-Checked '.\.venv\Scripts\python.exe' @('-m', 'pip', 'install', '-r', 'dashboard/requirements.txt')
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22.13+ to build frontend source.' }
Push-Location -LiteralPath 'dashboard\web'
try {
    $installedPnpm = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    if ($installedPnpm -and ((& $installedPnpm.Source --version) -eq '11.19.0')) {
        Invoke-Checked $installedPnpm.Source @('install', '--frozen-lockfile')
        Invoke-Checked $installedPnpm.Source @('run', 'build')
    } else {
        # npm exec downloads only the pinned pnpm CLI, without changing global installation.
        Invoke-Checked 'npx.cmd' @('--yes', 'pnpm@11.19.0', 'install', '--frozen-lockfile')
        Invoke-Checked 'npx.cmd' @('--yes', 'pnpm@11.19.0', 'run', 'build')
    }
} finally { Pop-Location }
Write-Host 'Setup complete. Run Start-AKI.cmd. Subsequent runs are local and offline.'
