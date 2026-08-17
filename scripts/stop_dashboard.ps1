# Stop the local dashboard. By default also quit Docker Desktop.

$ErrorActionPreference = "Continue"
$ContainerName = if ($env:DASHBOARD_CONTAINER_NAME) { $env:DASHBOARD_CONTAINER_NAME } else { "okx-dashboard" }
$ComposeService = "dashboard"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

$dockerBin = Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"
if (Test-Path $dockerBin) {
    $env:Path = "$dockerBin;$env:Path"
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker compose stop $ComposeService 2>$null | Out-Null
    docker stop $ContainerName 2>$null | Out-Null
}

if ($env:KEEP_DOCKER -eq "1") {
    exit 0
}

Get-Process "Docker Desktop" -ErrorAction SilentlyContinue | Stop-Process -ErrorAction SilentlyContinue
Get-Process "com.docker.backend" -ErrorAction SilentlyContinue | Stop-Process -ErrorAction SilentlyContinue
exit 0
