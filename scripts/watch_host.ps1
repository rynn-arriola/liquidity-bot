param(
    [Parameter(Mandatory=$true)][string]$Distro,
    [Parameter(Mandatory=$true)][string]$LinuxRoot,
    [switch]$Once,
    # Test only: 'battery:25' or 'ac:80' replaces the real reading and prints
    # the power-guard decision without stopping or starting anything.
    [string]$SimulatePower = ''
)
$ErrorActionPreference = 'Stop'
# Validated values become arguments, never a dynamically built shell program.
if ($Distro -notmatch '^[A-Za-z0-9_.-]+$' -or
    $LinuxRoot -notmatch '^/[A-Za-z0-9_./-]+$' -or $LinuxRoot.Contains('..')) {
    throw 'Invalid host watchdog runtime location'
}

# Power guard (operator, 2026-09-26): a laptop host must not die with quotes
# resting. On battery at or below the stop threshold, run the ordinary graceful
# stop (cancel, flatten, reconcile, disarm). Once back on AC above the resume
# threshold for two minutes, start again with the settings saved at the stop.
$PowerStopPercent = 30
$PowerResumePercent = 40
$PowerResumeStableChecks = 8
$powerMarker = Join-Path (Join-Path (Split-Path $PSScriptRoot -Parent) 'data') 'power-guard-stop.json'
$script:acStableChecks = 0

function Get-PowerReading {
    if ($SimulatePower -match '^(battery|ac):(\d{1,3})$') {
        return @{ OnBattery = ($Matches[1] -eq 'battery'); Percent = [int]$Matches[2] }
    }
    $battery = Get-CimInstance Win32_Battery -ErrorAction Stop | Select-Object -First 1
    if ($null -eq $battery) { return $null }  # desktop: nothing to guard
    # BatteryStatus 1 = discharging (on battery); every other value has AC.
    return @{ OnBattery = ($battery.BatteryStatus -eq 1); Percent = [int]$battery.EstimatedChargeRemaining }
}

function Invoke-Wsl([string]$Command, [int]$TimeoutMs) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = "$env:WINDIR\System32\wsl.exe"
    $info.Arguments = "-d $Distro --cd $LinuxRoot --exec $Command"
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($info)
    $out = $p.StandardOutput.ReadToEndAsync()
    [void]$p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit($TimeoutMs)) { $p.Kill(); return @{ Code = -1; Out = '' } }
    return @{ Code = $p.ExitCode; Out = $out.GetAwaiter().GetResult() }
}

function Invoke-PowerGuard {
    try { $power = Get-PowerReading } catch { Write-Warning 'Power reading unavailable'; return }
    if ($null -eq $power) { return }
    $dry = [bool]$SimulatePower
    if ($power.OnBattery -and $power.Percent -le $PowerStopPercent -and -not (Test-Path $powerMarker)) {
        $script:acStableChecks = 0
        # Save the launch settings before the stop disarms the watchdog record.
        $state = Invoke-Wsl 'cat data/host_recovery.json' 10000
        $settings = $null
        try { $settings = $state.Out | ConvertFrom-Json } catch { }
        if ($dry) { Write-Output "POWER_GUARD would stop (battery $($power.Percent)%)"; return }
        if ($null -eq $settings -or $settings.status -notin @('armed', 'recovering')) {
            Write-Output "power_guard=skip reason=bot_not_armed battery=$($power.Percent)"; return
        }
        @{ stopped_at = [DateTime]::UtcNow.ToString('o'); battery_percent = $power.Percent;
           target = $settings.target; size = $settings.size; slots = $settings.slots; source = $settings.source } |
            ConvertTo-Json -Compress | Set-Content -LiteralPath $powerMarker -Encoding UTF8
        $stop = Invoke-Wsl './scripts/stop_commissioning.sh' 330000
        Write-Output "power_guard=stopped battery=$($power.Percent) stop_exit=$($stop.Code)"
        return
    }
    if (-not $power.OnBattery -and $power.Percent -ge $PowerResumePercent -and (Test-Path $powerMarker)) {
        $script:acStableChecks += 1
        if (-not $dry -and $script:acStableChecks -lt $PowerResumeStableChecks) { return }
        $saved = Get-Content -LiteralPath $powerMarker -Raw | ConvertFrom-Json
        foreach ($value in @($saved.target, $saved.size, $saved.slots, $saved.source)) {
            if ("$value" -notmatch '^[A-Za-z0-9_.-]+$') { Write-Warning 'Power guard marker is invalid; not restarting'; return }
        }
        if ($dry) { Write-Output "POWER_GUARD would restart after $PowerResumeStableChecks stable AC checks ($($saved.target) $($saved.size) $($saved.slots) $($saved.source))"; return }
        $start = Invoke-Wsl "./scripts/start_commissioning.sh $($saved.target) $($saved.size) $($saved.slots) $($saved.source)" 120000
        if ($start.Code -eq 0) { Remove-Item -LiteralPath $powerMarker -Force }
        Write-Output "power_guard=restarted battery=$($power.Percent) start_exit=$($start.Code)"
        return
    }
    if ($power.OnBattery) { $script:acStableChecks = 0 }
    if ($dry) { Write-Output "POWER_GUARD no action (battery=$($power.OnBattery) $($power.Percent)%)" }
}

if ($SimulatePower) { Invoke-PowerGuard; exit 0 }
$identity = $LinuxRoot.Replace('/', '_')
$mutex = New-Object System.Threading.Mutex($false, "Local\LiquidityBotWatch_$identity")
$owned = $false
try {
    try { $owned = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $owned = $true }
    if (-not $owned) { exit 0 }
    do {
        $checkState = 'unknown'
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = "$env:WINDIR\System32\wsl.exe"
        $info.Arguments = "-d $Distro --cd $LinuxRoot --exec .venv/bin/python -B -m liquidity_bot.host_recovery tick"
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $info
        try {
            [void]$process.Start()
            $outTask = $process.StandardOutput.ReadToEndAsync()
            $errTask = $process.StandardError.ReadToEndAsync()
            if (-not $process.WaitForExit(20000)) {
                # Kill only this diagnostic wsl.exe client, never the distro or trader.
                $process.Kill()
                Write-Warning 'Host recovery check timed out; state is unknown'
            } elseif ($process.ExitCode -ne 0) {
                Write-Warning 'Host recovery check failed; automatic launch was not verified'
            } else {
                $resultText = $outTask.GetAwaiter().GetResult().Trim()
                if ($resultText -match '^(disarmed|armed|recovering|attention|recovered|process_present|backoff|recovery_started|control_busy)$') {
                    $checkState = $resultText
                    Write-Output $checkState
                }
            }
        } catch { Write-Warning 'Host recovery check unavailable' }
        finally { $process.Dispose() }
        # Local diagnostics only: no raw error text, secrets or venue calls.
        try {
            $statusDirectory = Join-Path (Split-Path $PSScriptRoot -Parent) 'data'
            [void][System.IO.Directory]::CreateDirectory($statusDirectory)
            $statusPath = Join-Path $statusDirectory 'host-watchdog-status.json'
            $tempPath = $statusPath + '.tmp'
            @{ observed_at = [DateTime]::UtcNow.ToString('o'); state = $checkState } |
                ConvertTo-Json -Compress | Set-Content -LiteralPath $tempPath -Encoding UTF8
            Move-Item -LiteralPath $tempPath -Destination $statusPath -Force
        } catch { Write-Warning 'Host recovery local status could not be saved' }
        Invoke-PowerGuard
        if (-not $Once) { Start-Sleep -Seconds 15 }
    } while (-not $Once)
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
