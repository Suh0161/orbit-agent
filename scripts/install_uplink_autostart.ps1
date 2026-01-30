param(
  [string]$ProjectDir = "$(Resolve-Path .)",
  [string]$TaskName = "OrbitUplink",
  [string]$PythonExe = "python"
)

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument "-m orbit_agent.uplink.main" -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn

# Run only when user is logged on (keeps UI automation workable).
$principal = New-ScheduledTaskPrincipal -UserId "$env:UserName" -LogonType Interactive -RunLevel LeastPrivilege

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Installed autostart Scheduled Task: $TaskName"
Write-Host "ProjectDir: $ProjectDir"
Write-Host "To remove: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"

