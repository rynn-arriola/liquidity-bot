param(
    [Parameter(Mandatory=$true)][string]$Distro,
    [Parameter(Mandatory=$true)][string]$LinuxRoot,
    # Test only: print what a Windows shutdown would do, without stopping anything.
    [switch]$SimulateShutdown
)
# Shutdown guard (operator, 2026-09-26): when Windows shuts down or restarts on
# purpose (Windows Update, Start > Restart), hold the shutdown with a visible
# reason while the bot runs its ordinary graceful stop (cancel, flatten,
# reconcile), then let Windows continue. The stop is recorded in the same
# marker as the power guard, so the watchdog resumes trading after the
# restart once the laptop is on AC. A crash, power cut or a user choosing
# "Shut down anyway" cannot be caught; exchange-side entry expiry covers them.
$ErrorActionPreference = 'Stop'
if ($Distro -notmatch '^[A-Za-z0-9_.-]+$' -or
    $LinuxRoot -notmatch '^/[A-Za-z0-9_./-]+$' -or $LinuxRoot.Contains('..')) {
    throw 'Invalid shutdown guard runtime location'
}
$dataDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'data'
$marker = Join-Path $dataDir 'power-guard-stop.json'
$logPath = Join-Path $dataDir 'shutdown-guard.log'

function Write-GuardLog([string]$Text) {
    try {
        [void][System.IO.Directory]::CreateDirectory($dataDir)
        Add-Content -LiteralPath $logPath -Value "$([DateTime]::UtcNow.ToString('o')) $Text" -Encoding UTF8
    } catch { }
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

function Stop-BotForShutdown([bool]$Dry) {
    $state = Invoke-Wsl 'cat data/host_recovery.json' 15000
    $settings = $null
    try { $settings = $state.Out | ConvertFrom-Json } catch { }
    if ($null -eq $settings -or $settings.status -notin @('armed', 'recovering')) {
        return 'not_running'
    }
    foreach ($value in @($settings.target, $settings.size, $settings.slots, $settings.source)) {
        if ("$value" -notmatch '^[A-Za-z0-9_.-]+$') { return 'invalid_settings' }
    }
    if ($Dry) { return "would_stop_and_resume_after_restart ($($settings.target) $($settings.size) $($settings.slots) $($settings.source))" }
    if (-not (Test-Path $marker)) {
        @{ stopped_at = [DateTime]::UtcNow.ToString('o'); reason = 'windows_shutdown';
           target = $settings.target; size = $settings.size; slots = $settings.slots; source = $settings.source } |
            ConvertTo-Json -Compress | Set-Content -LiteralPath $marker -Encoding UTF8
    }
    $stop = Invoke-Wsl './scripts/stop_commissioning.sh' 240000
    return "stopped exit=$($stop.Code)"
}

if ($SimulateShutdown) { Write-Output (Stop-BotForShutdown $true); exit 0 }

$identity = $LinuxRoot.Replace('/', '_')
$mutex = New-Object System.Threading.Mutex($false, "Local\LiquidityBotShutdownGuard_$identity")
$owned = $false
try { $owned = $mutex.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $owned = $true }
if (-not $owned) { exit 0 }
try {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class LiquidityShutdownBlock {
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern bool ShutdownBlockReasonCreate(IntPtr hWnd, string reason);
    [DllImport("user32.dll")]
    public static extern bool ShutdownBlockReasonDestroy(IntPtr hWnd);
    [DllImport("kernel32.dll")]
    public static extern bool SetProcessShutdownParameters(uint level, uint flags);
}
"@
    # Ask Windows to notify this process early among applications.
    [void][LiquidityShutdownBlock]::SetProcessShutdownParameters(0x3FF, 0)
    $form = New-Object System.Windows.Forms.Form
    $form.ShowInTaskbar = $false
    $form.FormBorderStyle = 'None'
    $form.Opacity = 0
    $form.Size = New-Object System.Drawing.Size(1, 1)
    $form.StartPosition = 'Manual'
    $form.Location = New-Object System.Drawing.Point(-2000, -2000)
    $script:guardDone = $false
    $form.Add_FormClosing({
        param($sender, $e)
        if ($e.CloseReason -ne [System.Windows.Forms.CloseReason]::WindowsShutDown -or $script:guardDone) { return }
        $script:guardDone = $true
        $e.Cancel = $true
        [void][LiquidityShutdownBlock]::ShutdownBlockReasonCreate(
            $form.Handle, 'Liquidity bot is cancelling its orders before shutdown')
        Write-GuardLog 'shutdown detected; graceful stop starting'
        try { $result = Stop-BotForShutdown $false } catch { $result = "error: $($_.Exception.Message)" }
        Write-GuardLog "graceful stop result: $result"
        [void][LiquidityShutdownBlock]::ShutdownBlockReasonDestroy($form.Handle)
        $form.Close()
    })
    Write-GuardLog 'shutdown guard started'
    [System.Windows.Forms.Application]::Run($form)
} finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
