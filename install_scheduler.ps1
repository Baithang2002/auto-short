$ErrorActionPreference = 'Stop'
$here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$bat      = Join-Path $here 'run_daily.bat'
$taskName = 'AutoShortDaily'

Write-Host "Registering '$taskName' -> $bat"

$action = New-ScheduledTaskAction -Execute $bat -WorkingDirectory $here

# Weekdays at 6:00 PM local time (matches the old 6pm IST Cowork schedule).
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At 6:00PM

# Run only when this user is logged on (no stored password, no admin needed).
# Interactive logon is required so the Chrome upload session can use the GUI.
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Registered. Writing status to output\scheduler_status.txt"

if (-not (Test-Path (Join-Path $here 'output'))) {
    New-Item -ItemType Directory -Path (Join-Path $here 'output') | Out-Null
}
$statusPath = Join-Path $here 'output\scheduler_status.txt'
"Registered at $(Get-Date -Format 's')" | Out-File -FilePath $statusPath -Encoding utf8
schtasks /query /tn $taskName /v /fo LIST 2>&1 |
    Out-File -FilePath $statusPath -Encoding utf8 -Append

Write-Host "Done."
