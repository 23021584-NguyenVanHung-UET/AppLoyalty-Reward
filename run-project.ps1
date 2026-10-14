[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Status', 'Check')]
    [string]$Action = 'Start',
    [switch]$BuildFrontend,
    [switch]$SkipWorker,
    [switch]$SkipTunnel,
    [string]$TunnelConfig = (Join-Path $env:USERPROFILE '.cloudflared\config.yml'),
    [string]$TunnelId = '4151c9cf-0d7e-4cc8-b50a-bfebfc7a6763'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$pythonPath = Join-Path $backendDir '.venv\Scripts\python.exe'
$managePath = Join-Path $backendDir 'manage.py'
$runtimeDir = Join-Path $projectRoot '.localrun'
$statePath = Join-Path $runtimeDir 'processes.json'

function Read-ProjectState {
    if (Test-Path -LiteralPath $statePath) {
        $saved = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
        if ($saved.root -ne $projectRoot -or $saved.version -ne 1) {
            throw 'Runtime state does not match this workspace.'
        }
        return $saved
    }
    return [pscustomobject]@{version = 1; root = $projectRoot; services = @()}
}

function Save-ProjectState($saved) {
    if (-not (Test-Path -LiteralPath $runtimeDir)) {
        New-Item -ItemType Directory -Path $runtimeDir | Out-Null
    }
    $saved | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
}

function Get-OwnedProcess($service) {
    $process = Get-Process -Id $service.pid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    try {
        if ($process.StartTime.ToUniversalTime().ToString('o') -ne $service.startedAt -or
            $process.Path -ne $service.executable) { return $null }
        $details = Get-CimInstance Win32_Process -Filter "ProcessId = $($service.pid)"
        $marker = if ($service.name -eq 'tunnel') { $service.config } else { $managePath }
        if ($null -eq $details -or $details.CommandLine -notlike "*$marker*") { return $null }
        return $process
    } catch { return $null }
}

function Stop-OwnedService($service) {
    $process = Get-OwnedProcess $service
    if ($null -eq $process) {
        Write-Host "$($service.name): already stopped (or PID no longer belongs to this script)."
        return
    }
    # Kill only the verified parent and its children, including django-q workers.
    & "$env:SystemRoot\System32\taskkill.exe" /PID $service.pid /T /F | Out-Null
    if ($LASTEXITCODE -ne 0 -and (Get-OwnedProcess $service)) {
        throw "Could not stop $($service.name), PID $($service.pid)."
    }
    Write-Host "$($service.name): stopped."
}

function Test-BackendPort {
    $socket = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $socket.BeginConnect('127.0.0.1', 8000, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(300)) { return $false }
        $socket.EndConnect($pending)
        return $true
    } catch { return $false }
    finally { $socket.Dispose() }
}

function Invoke-ProjectCommand([string]$Executable, [string[]]$Arguments, [string]$Directory) {
    Push-Location -LiteralPath $Directory
    try {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $($Arguments -join ' ')" }
    } finally { Pop-Location }
}

function Start-OwnedService([string]$Name, [string]$Executable, [string[]]$Arguments, [string]$Directory) {
    $suffix = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 6)
    $stdout = Join-Path $runtimeDir "$Name-$suffix.out.log"
    $stderr = Join-Path $runtimeDir "$Name-$suffix.err.log"
    $quoted = @($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' })
    $process = Start-Process -FilePath $Executable -ArgumentList $quoted -WorkingDirectory $Directory `
        -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    Start-Sleep -Milliseconds 300
    $process.Refresh()
    if ($process.HasExited) { throw "$Name exited. See $stderr" }
    $live = Get-Process -Id $process.Id
    return [pscustomobject]@{name = $Name; pid = $process.Id; executable = $live.Path;
        startedAt = $live.StartTime.ToUniversalTime().ToString('o'); config = $TunnelConfig;
        stdout = $stdout; stderr = $stderr}
}

$state = Read-ProjectState
if ($Action -eq 'Stop') {
    $services = @($state.services)
    [array]::Reverse($services)
    foreach ($service in $services) { Stop-OwnedService $service }
    $state.services = @()
    Save-ProjectState $state
    return
}
if ($Action -eq 'Status') {
    if (@($state.services).Count -eq 0) { Write-Host 'No services tracked by this script.' }
    foreach ($service in @($state.services)) {
        $label = if (Get-OwnedProcess $service) { 'RUNNING' } else { 'STOPPED' }
        Write-Host "$($service.name): $label (PID $($service.pid))"
        Write-Host "  stdout: $($service.stdout)"
        Write-Host "  stderr: $($service.stderr)"
    }
    Write-Host 'Local admin: http://127.0.0.1:8000/admin/'
    Write-Host 'Public admin: https://loyalty-dev.hungvandomain.dpdns.org/admin/'
    return
}

if (-not (Test-Path -LiteralPath $pythonPath)) { throw "Missing virtual environment: $pythonPath" }
if (-not (Test-Path -LiteralPath (Join-Path $backendDir '.env'))) { throw 'Missing backend/.env. Configure the existing database and Shopify credentials first.' }
$cloudflared = $null
if (-not $SkipTunnel) {
    $cloudflared = Get-Command cloudflared.exe -ErrorAction SilentlyContinue
    if ($null -eq $cloudflared) { throw 'cloudflared.exe is not on PATH. Use -SkipTunnel for local-only development.' }
    if (-not (Test-Path -LiteralPath $TunnelConfig)) { throw "Missing tunnel configuration: $TunnelConfig" }
}

Invoke-ProjectCommand $pythonPath @('-X', 'utf8', $managePath, 'check') $backendDir
Invoke-ProjectCommand $pythonPath @('-X', 'utf8', $managePath, 'migrate', '--check') $backendDir

$manifest = Join-Path $frontendDir 'dist\.vite\manifest.json'
$needsBuild = $BuildFrontend -or -not (Test-Path -LiteralPath $manifest)
if (-not $needsBuild) {
    $builtAt = (Get-Item -LiteralPath $manifest).LastWriteTimeUtc
    $inputs = @(Get-ChildItem -LiteralPath (Join-Path $frontendDir 'src') -Recurse -File |
        Where-Object { $_.FullName -notmatch '[\\/]tests[\\/]' })
    $inputs += @(Get-ChildItem -LiteralPath $frontendDir -File -Force | Where-Object {
        $_.Name -match '^(package.*\.json|vite\.config\..*|tsconfig.*\.json|\.env.*)$' })
    $needsBuild = @($inputs | Where-Object { $_.LastWriteTimeUtc -gt $builtAt }).Count -gt 0
}
if ($needsBuild) {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npm) { throw 'npm.cmd is missing. Install Node.js to build the frontend.' }
    if ($Action -eq 'Check') { Write-Host 'Frontend needs a build. Start will run npm run build.' }
    else { Invoke-ProjectCommand $npm.Source @('run', 'build') $frontendDir }
}
if ($Action -eq 'Check') {
    Write-Host 'Preflight OK. No services started; no migrations applied.'
    return
}

$state.services = @($state.services | Where-Object { $null -ne (Get-OwnedProcess $_) })
$django = @($state.services | Where-Object { $_.name -eq 'django' })
if ($django.Count -eq 0 -and (Test-BackendPort)) {
    throw 'Port 8000 is occupied by a server outside this script. Stop that runserver first, then retry.'
}
if (-not $SkipWorker -and @($state.services | Where-Object { $_.name -eq 'worker' }).Count -eq 0) {
    $otherWorker = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -like 'python*' -and $_.CommandLine -match 'manage\.py["\s]+qcluster' })
    if ($otherWorker.Count -gt 0) { throw 'An untracked qcluster is already running. Stop it manually or use -SkipWorker.' }
}
Save-ProjectState $state
$startedNow = @()
try {
    if ($django.Count -eq 0) {
        $service = Start-OwnedService 'django' $pythonPath @('-u', '-X', 'utf8', $managePath, 'runserver', '127.0.0.1:8000', '--noreload') $backendDir
        $state.services += $service; $startedNow += $service; Save-ProjectState $state
        $ready = $false
        for ($attempt = 0; $attempt -lt 40; $attempt++) {
            if (Test-BackendPort) { $ready = $true; break }
            Start-Sleep -Milliseconds 250
        }
        if (-not $ready) { throw "Django did not open port 8000. See $($service.stderr)" }
    }
    if (-not $SkipWorker -and @($state.services | Where-Object { $_.name -eq 'worker' }).Count -eq 0) {
        $service = Start-OwnedService 'worker' $pythonPath @('-u', '-X', 'utf8', $managePath, 'qcluster') $backendDir
        $state.services += $service; $startedNow += $service; Save-ProjectState $state
    }
    if (-not $SkipTunnel -and @($state.services | Where-Object { $_.name -eq 'tunnel' }).Count -eq 0) {
        $service = Start-OwnedService 'tunnel' $cloudflared.Source @('tunnel', '--config', $TunnelConfig, 'run', $TunnelId) $projectRoot
        $state.services += $service; $startedNow += $service; Save-ProjectState $state
    }
    Write-Host 'Project started. Django serves the built Vue frontend; a separate Vite server is not needed.'
    Write-Host 'Django admin: http://127.0.0.1:8000/admin/'
    Write-Host 'Open the installed app from Shopify Admin for the merchant interface.'
    Write-Host 'Processes run hidden. Logs and PIDs are in .localrun/.'
    Write-Host 'Status: .\run-project.ps1 -Action Status'
    Write-Host 'Stop:   .\run-project.ps1 -Action Stop'
} catch {
    [array]::Reverse($startedNow)
    foreach ($service in $startedNow) { Stop-OwnedService $service }
    $state.services = @($state.services | Where-Object { $null -ne (Get-OwnedProcess $_) })
    Save-ProjectState $state
    throw
}
