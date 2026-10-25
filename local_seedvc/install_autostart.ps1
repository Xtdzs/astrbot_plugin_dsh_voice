param(
    [Parameter(Mandatory=$true)][string]$Runtime,
    [int]$Port = 17860
)
$ErrorActionPreference = 'Stop'
$VoiceRuntime = (Resolve-Path -LiteralPath $Runtime).Path
$VoicePython = Join-Path $VoiceRuntime '.venv\Scripts\pythonw.exe'
$VoiceWatchdog = Join-Path $PSScriptRoot 'watchdog.py'
if (!(Test-Path -LiteralPath $VoicePython) -or !(Test-Path -LiteralPath $VoiceWatchdog)) {
    throw 'Prepare the separate runtime before installing auto-start.'
}
$VoiceTaskName = 'AstrBot-SeedVC-Local'
$VoiceArguments = '"{0}" --runtime "{1}" --port {2} --device NPU' -f $VoiceWatchdog, $VoiceRuntime, $Port
$VoiceExisting = Get-ScheduledTask -TaskName $VoiceTaskName -ErrorAction SilentlyContinue
if ($VoiceExisting -and (($VoiceExisting.Actions.Execute -ne $VoicePython) -or ($VoiceExisting.Actions.Arguments -ne $VoiceArguments))) {
    throw 'A different task already uses this name; it has been left unchanged.'
}
$VoiceSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$VoiceAction = New-ScheduledTaskAction -Execute $VoicePython -Argument $VoiceArguments -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$VoiceTrigger = New-ScheduledTaskTrigger -AtLogOn -User $VoiceSid
$VoicePrincipal = New-ScheduledTaskPrincipal -UserId $VoiceSid -LogonType Interactive -RunLevel Limited
$VoiceSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $VoiceTaskName -Action $VoiceAction -Trigger $VoiceTrigger -Principal $VoicePrincipal -Settings $VoiceSettings -Description 'Start the local AstrBot Seed-VC service after Windows login, warm resident NPU models and recover owned processes.' -Force | Out-Null
Start-ScheduledTask -TaskName $VoiceTaskName
Get-ScheduledTask -TaskName $VoiceTaskName | Select-Object TaskName, State
