param(
    [string]$Distro = 'Ubuntu-24.04',
    [string]$LinuxRoot = '/home/polybot/liquidity-bot',
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
$taskName = 'LiquidityBot Host Recovery'
if ($Remove) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    return
}
if ($Distro -notmatch '^[A-Za-z0-9_.-]+$' -or
    $LinuxRoot -notmatch '^/[A-Za-z0-9_./-]+$' -or $LinuxRoot.Contains('..')) {
    throw 'Invalid runtime location'
}
$script = Join-Path $PSScriptRoot 'watch_host.ps1'
$user = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$argsText = '-NoProfile -NonInteractive -WindowStyle Hidden -File "' + $script +
    '" -Distro ' + $Distro + ' -LinuxRoot ' + $LinuxRoot
$action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument $argsText
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output 'Installed host watchdog. Missing/disarmed runtime state never authorizes recovery.'
