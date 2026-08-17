# Native Windows control panel for the observation dashboard.

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LaunchScript = Join-Path $ScriptDir "launch_dashboard.ps1"
$StopScript = Join-Path $ScriptDir "stop_dashboard.ps1"
$HealthzUrl = "http://127.0.0.1:8787/healthz"
$Busy = $false

function Test-Healthy {
    try {
        $response = Invoke-WebRequest -Uri $HealthzUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Invoke-DashboardScript {
    param([string]$Path)
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = "powershell.exe"
    $info.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Path`""
    $info.WorkingDirectory = $ProjectRoot
    $info.UseShellExecute = $false
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($info)
    $stderr = $process.StandardError.ReadToEnd()
    $stdout = $process.StandardOutput.ReadToEnd()
    $process.WaitForExit()
    return @{
        Code = $process.ExitCode
        Output = (($stderr + "`n" + $stdout).Trim())
    }
}

$form = New-Object System.Windows.Forms.Form
$form.Text = "观察台"
$form.Size = New-Object System.Drawing.Size(420, 250)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false
$form.TopMost = $true

$title = New-Object System.Windows.Forms.Label
$title.Text = "观察台"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 18, [System.Drawing.FontStyle]::Bold)
$title.Location = New-Object System.Drawing.Point(24, 18)
$title.AutoSize = $true
$form.Controls.Add($title)

$status = New-Object System.Windows.Forms.Label
$status.Text = "状态：检查中…"
$status.Font = New-Object System.Drawing.Font("Segoe UI", 11)
$status.Location = New-Object System.Drawing.Point(24, 62)
$status.Size = New-Object System.Drawing.Size(360, 24)
$form.Controls.Add($status)

$runButton = New-Object System.Windows.Forms.Button
$runButton.Text = "运行"
$runButton.Font = New-Object System.Drawing.Font("Segoe UI", 11)
$runButton.Location = New-Object System.Drawing.Point(24, 104)
$runButton.Size = New-Object System.Drawing.Size(170, 40)
$form.Controls.Add($runButton)

$stopButton = New-Object System.Windows.Forms.Button
$stopButton.Text = "停止"
$stopButton.Font = New-Object System.Drawing.Font("Segoe UI", 11)
$stopButton.Location = New-Object System.Drawing.Point(210, 104)
$stopButton.Size = New-Object System.Drawing.Size(170, 40)
$form.Controls.Add($stopButton)

$note = New-Object System.Windows.Forms.Label
$note.Text = "仅作筛选 · 不提供投资建议 · 不自动下单"
$note.ForeColor = [System.Drawing.Color]::DimGray
$note.Location = New-Object System.Drawing.Point(24, 164)
$note.Size = New-Object System.Drawing.Size(360, 24)
$form.Controls.Add($note)

function Set-Busy([bool]$Value) {
    $script:Busy = $Value
    $runButton.Enabled = -not $Value
    $stopButton.Enabled = $true
}

function Update-Status {
    if ($script:Busy) { return }
    if (Test-Healthy) {
        $status.Text = "状态：运行中"
    } else {
        $status.Text = "状态：未启动"
    }
}

function Start-Action([string]$Path, [string]$Pending, [bool]$IsStop) {
    if ($script:Busy -and -not $IsStop) { return }
    Set-Busy $true
    $status.Text = $Pending
    $worker = New-Object System.ComponentModel.BackgroundWorker
    $worker.Add_DoWork({
        param($sender, $event)
        $payload = $event.Argument
        $scriptResult = Invoke-DashboardScript -Path $payload.Path
        $event.Result = @{
            Code = $scriptResult.Code
            Output = $scriptResult.Output
            IsStop = $payload.IsStop
        }
    })
    $worker.Add_RunWorkerCompleted({
        param($sender, $event)
        $result = $event.Result
        if ($result.Code -eq 0) {
            if ($result.IsStop) {
                $status.Text = "状态：已停止"
            } elseif (Test-Healthy) {
                $status.Text = "状态：运行中"
            } else {
                $status.Text = "状态：未启动"
            }
        } else {
            $line = (($result.Output -split "`r?`n") | Where-Object { $_ } | Select-Object -Last 1)
            if (-not $line) { $line = "启动失败" }
            if ($line.Length -gt 42) { $line = $line.Substring(0, 42) }
            $status.Text = "状态：$line"
        }
        Set-Busy $false
    })
    $worker.RunWorkerAsync(@{ Path = $Path; IsStop = $IsStop })
}

$runButton.Add_Click({ Start-Action $LaunchScript "状态：正在打开页面…" $false })
$stopButton.Add_Click({ Start-Action $StopScript "状态：停止中…" $true })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({ Update-Status })
$timer.Start()

$form.Add_Shown({
    Update-Status
    Start-Action $LaunchScript "状态：正在打开页面…" $false
})
$form.Add_FormClosed({ $timer.Stop() })

[void]$form.ShowDialog()
