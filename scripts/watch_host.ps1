param(
    [Parameter(Mandatory=$true)][string]$Distro,
    [Parameter(Mandatory=$true)][string]$LinuxRoot,
    [switch]$Once
)
$ErrorActionPreference = 'Stop'
# Validated values become arguments, never a dynamically built shell program.
if ($Distro -notmatch '^[A-Za-z0-9_.-]+$' -or
    $LinuxRoot -notmatch '^/[A-Za-z0-9_./-]+$' -or $LinuxRoot.Contains('..')) {
    throw 'Invalid host watchdog runtime location'
}
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
        if (-not $Once) { Start-Sleep -Seconds 15 }
    } while (-not $Once)
} finally {
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
