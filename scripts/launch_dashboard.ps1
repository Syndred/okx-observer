# Start the local dashboard once. If it is already healthy, only open the page.

$ErrorActionPreference = "Continue"
$DashboardUrl = if ($env:DASHBOARD_URL) { $env:DASHBOARD_URL } else { "http://127.0.0.1:8787" }
$HealthzUrl = ($DashboardUrl.TrimEnd("/") + "/healthz")
$ContainerName = if ($env:DASHBOARD_CONTAINER_NAME) { $env:DASHBOARD_CONTAINER_NAME } else { "okx-dashboard" }
$ComposeService = "dashboard"
$LockDir = if ($env:LAUNCHER_LOCK_DIR) { $env:LAUNCHER_LOCK_DIR } else { Join-Path $env:TEMP "okx-dashboard-launcher.lock" }
$LockStaleSeconds = 90
$WaitAttempts = 30
$DockerWaitAttempts = if ($env:DOCKER_WAIT_ATTEMPTS) { [int]$env:DOCKER_WAIT_ATTEMPTS } else { 90 }

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location $ProjectRoot

function Add-DockerPath {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Docker\Docker\resources\bin"),
        (Join-Path $env:LOCALAPPDATA "Docker\bin")
    )
    foreach ($item in $candidates) {
        if (Test-Path $item) {
            $env:Path = "$item;$env:Path"
        }
    }
}

function Test-Healthy {
    try {
        $response = Invoke-WebRequest -Uri $HealthzUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Open-Page {
    Start-Process $DashboardUrl | Out-Null
}

function Test-DockerReady {
    docker info *> $null
    return $LASTEXITCODE -eq 0
}

function Start-DockerDesktop {
    $app = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $app) {
        Start-Process $app | Out-Null
        return
    }
    Start-Process "Docker Desktop" -ErrorAction SilentlyContinue | Out-Null
}

function Wait-Docker {
    if (Test-DockerReady) { return $true }
    Write-Host "正在打开 Docker Desktop…"
    Start-DockerDesktop
    for ($attempt = 1; $attempt -le $DockerWaitAttempts; $attempt++) {
        if (Test-DockerReady) { return $true }
        Start-Sleep -Seconds 1
    }
    Write-Error "Docker 还没就绪，请确认 Docker Desktop 已安装并完成启动。"
    return $false
}

function Get-ContainerState {
    $state = docker inspect -f "{{.State.Status}}" $ContainerName 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return "$state".Trim()
}

function Test-PortInUse {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect("127.0.0.1", 8787)
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

function Wait-UntilHealthy {
    for ($attempt = 1; $attempt -le $WaitAttempts; $attempt++) {
        if (Test-Healthy) { return $true }
        if ($attempt -lt $WaitAttempts) { Start-Sleep -Seconds 1 }
    }
    return $false
}

function Show-Logs {
    docker compose logs --tail 80 $ComposeService 2>$null | Out-Host
    docker logs --tail 80 $ContainerName 2>$null | Out-Host
}

function Get-LockAgeSeconds {
    if (-not (Test-Path $LockDir)) { return 0 }
    $created = (Get-Item $LockDir).LastWriteTimeUtc
    return [int]((Get-Date).ToUniversalTime() - $created).TotalSeconds
}

function Unlock-Launcher {
    Remove-Item -Recurse -Force $LockDir -ErrorAction SilentlyContinue
}

function Lock-Launcher {
    try {
        New-Item -ItemType Directory -Path $LockDir -ErrorAction Stop | Out-Null
        Set-Content -Path (Join-Path $LockDir "pid") -Value $PID
        return $true
    } catch {
        if ((Get-LockAgeSeconds) -ge $LockStaleSeconds) {
            Unlock-Launcher
            try {
                New-Item -ItemType Directory -Path $LockDir -ErrorAction Stop | Out-Null
                Set-Content -Path (Join-Path $LockDir "pid") -Value $PID
                return $true
            } catch {
                return $false
            }
        }
        return $false
    }
}

Add-DockerPath

if (Test-Healthy) {
    Open-Page
    exit 0
}

if (-not (Lock-Launcher)) {
    if (Wait-UntilHealthy) {
        Open-Page
        exit 0
    }
    Write-Error "观察台正在启动，请稍后再点一次。"
    exit 1
}

try {
    if (Test-Healthy) {
        Open-Page
        exit 0
    }

    if (-not (Wait-Docker)) {
        exit 1
    }

    $state = Get-ContainerState
    if ($state -eq "running") {
        if (Wait-UntilHealthy) {
            Open-Page
            exit 0
        }
        Write-Error "观察台容器已在运行，但页面还没就绪。"
        Show-Logs
        exit 1
    }

    if ($state -eq "created" -or $state -eq "exited" -or $state -eq "paused") {
        if ($state -eq "paused") {
            docker unpause $ContainerName | Out-Null
        }
        docker start $ContainerName | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Error "无法恢复已有观察台容器。"
            Show-Logs
            exit 1
        }
        if (Wait-UntilHealthy) {
            Open-Page
            exit 0
        }
        Write-Error "已有观察台容器已启动，但页面还没就绪。"
        Show-Logs
        exit 1
    }

    if (Test-PortInUse) {
        if (Wait-UntilHealthy) {
            Open-Page
            exit 0
        }
        Write-Error "8787 已被占用，不再重复启动。打开已有页面失败。"
        exit 1
    }

    docker compose up -d --no-deps $ComposeService
    if ($LASTEXITCODE -ne 0) {
        Show-Logs
        exit 1
    }

    if (Wait-UntilHealthy) {
        Open-Page
        exit 0
    }

    Show-Logs
    exit 1
} finally {
    Unlock-Launcher
}
