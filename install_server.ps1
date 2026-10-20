$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Запустіть PowerShell від імені адміністратора.'
}

$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$installDir = Join-Path $env:ProgramFiles 'RentalDesk'
$dataDir = Join-Path $env:ProgramData 'RentalDesk'
$configPath = Join-Path $dataDir 'config.json'
if (Test-Path -LiteralPath $configPath) {
    throw "RentalDesk уже налаштовано ($configPath). Для оновлення використовуйте update_server.ps1."
}

Write-Host 'Встановлення RentalDesk — сервер не буде запущено автоматично.' -ForegroundColor Cyan
$customDir = Read-Host "Папка програми [$installDir]"
if (-not [string]::IsNullOrWhiteSpace($customDir)) { $installDir = $customDir.Trim() }
New-Item -ItemType Directory -Path $installDir -Force | Out-Null

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if ($pythonLauncher) {
    & $pythonLauncher.Source -3 -c "import sys; print(sys.version); sys.exit(sys.version_info < (3, 11))"
    if ($LASTEXITCODE -ne 0) { throw 'Потрібен Python 3.11 або новіший.' }
    & $pythonLauncher.Source -3 -m venv (Join-Path $installDir '.venv')
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw 'Python 3.11 або новіший не знайдено. Встановіть Python та запустіть установлення знову.' }
    & $pythonCommand.Source -c "import sys; print(sys.version); sys.exit(sys.version_info < (3, 11))"
    if ($LASTEXITCODE -ne 0) { throw 'Потрібен Python 3.11 або новіший.' }
    & $pythonCommand.Source -m venv (Join-Path $installDir '.venv')
}
if ($LASTEXITCODE -ne 0) { throw 'Не вдалося створити віртуальне середовище Python.' }

$appFiles = @('app.py','requirements.txt','contract_template.md','run_server.py','backup_db.py','restore_db.py','restore_backup.ps1','server_manager.py','open_server_manager.ps1','service_manager.ps1','update_server.ps1')
foreach ($name in $appFiles) { Copy-Item -LiteralPath (Join-Path $sourceDir $name) -Destination $installDir -Force }
Copy-Item -LiteralPath (Join-Path $sourceDir 'templates') -Destination $installDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $sourceDir 'static') -Destination $installDir -Recurse -Force
New-Item -ItemType Directory -Path $dataDir -Force | Out-Null

$venvPython = Join-Path $installDir '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Не вдалося оновити pip. Перевірте інтернет.' }
& $venvPython -m pip install -r (Join-Path $installDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Не вдалося встановити залежності. Перевірте інтернет і запустіть установлення знову.' }
$secret = & $venvPython -c "import secrets; print(secrets.token_hex(32))"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($secret)) { throw 'Не вдалося створити ключ безпеки.' }

$addresses = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -ne '127.0.0.1' -and $_.IPAddress -notlike '169.254.*' -and $_.InterfaceAlias -notmatch 'Loopback' } |
    Select-Object -ExpandProperty IPAddress -Unique)
$defaultHost = if ($addresses.Count -gt 0) { $addresses[0] } else { '127.0.0.1' }
$backupDefault = if (Test-Path 'D:\') { 'D:\RentalDesk-Backups' } else { Join-Path $dataDir 'backups' }
$databasePath = Join-Path $dataDir 'rentals.sqlite3'
$config = [ordered]@{
    data_dir = $dataDir
    database = $databasePath
    backup_dir = $backupDefault
    backup_retention_days = 30
    backup_time = '06:00'
    backup_enabled = $false
    auto_start = $false
    bind_host = '0.0.0.0'
    advertised_host = $defaultHost
    port = 5000
    public_url = "http://${defaultHost}:5000"
    secret_key = $secret.Trim()
}
[System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
New-Item -ItemType Directory -Path $config.backup_dir -Force | Out-Null
$env:RENTAL_DB = $databasePath
$env:SECRET_KEY = $config.secret_key
& $venvPython -c "from app import app, init_db; ctx=app.app_context(); ctx.push(); init_db(); ctx.pop()"
if ($LASTEXITCODE -ne 0) { throw 'Не вдалося створити початкову базу даних.' }
Remove-Item Env:\RENTAL_DB,Env:\SECRET_KEY -ErrorAction SilentlyContinue
icacls $dataDir /inheritance:r /grant:r 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F' | Out-Null
if (-not ($config.backup_dir -like "$dataDir*")) { icacls $config.backup_dir /grant 'SYSTEM:(OI)(CI)F' 'BUILTIN\Administrators:(OI)(CI)F' | Out-Null }

$serverTask = 'RentalDesk Server'
$backupTask = 'RentalDesk Daily Backup'
$principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$serverAction = New-ScheduledTaskAction -Execute $venvPython -Argument "`"$(Join-Path $installDir 'run_server.py')`" --config `"$configPath`"" -WorkingDirectory $installDir
$serverTrigger = New-ScheduledTaskTrigger -AtStartup
$serverSettings = New-ScheduledTaskSettingsSet -RestartCount 20 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
Register-ScheduledTask -TaskName $serverTask -Action $serverAction -Trigger $serverTrigger -Principal $principal -Settings $serverSettings -Description 'RentalDesk application server' -Force | Out-Null
Disable-ScheduledTask -TaskName $serverTask | Out-Null

$backupAction = New-ScheduledTaskAction -Execute $venvPython -Argument "`"$(Join-Path $installDir 'backup_db.py')`" --config `"$configPath`"" -WorkingDirectory $installDir
$backupTrigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.Add([TimeSpan]::ParseExact('06:00','hh\:mm',[Globalization.CultureInfo]::InvariantCulture)))
$backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName $backupTask -Action $backupAction -Trigger $backupTrigger -Principal $principal -Settings $backupSettings -Description 'Daily RentalDesk database backup' -Force | Out-Null
Disable-ScheduledTask -TaskName $backupTask | Out-Null

$ruleName = 'RentalDesk TCP 5000'
Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000 -Profile Domain,Private | Out-Null

$urlFile = Join-Path $dataDir 'SERVER-ADDRESS.txt'
@('Відкрийте панель керування RentalDesk на робочому столі для налаштування IP, порту й резервного копіювання.', "Початкове налаштування після запуску сервера: $($config.public_url)/setup", "Папка резервних копій: $($config.backup_dir)") | Set-Content -LiteralPath $urlFile -Encoding UTF8
$managerPath = Join-Path $installDir 'open_server_manager.ps1'
$shortcutPath = Join-Path $env:Public 'Desktop\RentalDesk - сервер.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = (Get-Command powershell.exe).Source
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$managerPath`""
$shortcut.WorkingDirectory = $installDir
$shortcut.Description = 'Керування сервером RentalDesk'
$shortcut.Save()

Write-Host ''
Write-Host 'Установлення завершено. Сервер залишається зупиненим.' -ForegroundColor Green
Write-Host "Відкрийте ярлик на робочому столі: $shortcutPath"
Write-Host 'У панелі введіть адресу й порт, за потреби увімкніть автозапуск і резервне копіювання.'
