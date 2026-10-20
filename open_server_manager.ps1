$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $script = $MyInvocation.MyCommand.Path
    Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$script`"")
    exit
}
$installDir = $PSScriptRoot
$python = Join-Path $installDir '.venv\Scripts\python.exe'
$config = Join-Path $env:ProgramData 'RentalDesk\config.json'
 $manager = Join-Path $installDir 'server_manager.py'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $config) -or -not (Test-Path -LiteralPath $manager)) {
    throw 'RentalDesk ще не встановлено. Запустіть install_server.ps1.'
}
$logDir = Join-Path $env:ProgramData 'RentalDesk\logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stdoutLog = Join-Path $logDir 'server-manager.stdout.log'
$stderrLog = Join-Path $logDir 'server-manager.stderr.log'
$arguments = '"{0}" --config "{1}"' -f $manager, $config
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $installDir -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
Start-Sleep -Milliseconds 1200
$process.Refresh()
if ($process.HasExited) {
    $details = if (Test-Path -LiteralPath $stderrLog) { Get-Content -LiteralPath $stderrLog -Raw } else { '' }
    if ([string]::IsNullOrWhiteSpace($details)) { $details = "Панель завершилась з кодом $($process.ExitCode)." }
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show("Не вдалося відкрити панель сервера.`r`n`r`n$details`r`nЖурнал: $stderrLog", 'RentalDesk', 'OK', 'Error') | Out-Null
}
