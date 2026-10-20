$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Запустіть PowerShell від імені адміністратора.'
}

$sourceDir = $PSScriptRoot
$configPath = Join-Path $env:ProgramData 'RentalDesk\config.json'
$taskName = 'RentalDesk Server'
if (-not (Test-Path -LiteralPath $configPath)) { throw "Не знайдено конфігурацію сервера: $configPath" }
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) { throw "Не знайдено завдання '$taskName'. Перевірте, чи встановлено RentalDesk." }
$backupTaskState = Get-ScheduledTask -TaskName 'RentalDesk Daily Backup' -ErrorAction SilentlyContinue
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$config.auto_start = ($task.State -ne 'Disabled')
$config.backup_enabled = ($backupTaskState -and $backupTaskState.State -ne 'Disabled')
[System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
$pythonPath = $task.Actions[0].Execute
$installDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $pythonPath))
$venvPython = Join-Path $installDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) { throw "Не знайдено середовище програми: $venvPython" }

$required = @('app.py','backup_db.py','run_server.py','restore_db.py','contract_template.md','requirements.txt','server_manager.py','open_server_manager.ps1','templates','static')
foreach ($item in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceDir $item))) { throw "У пакеті бракує $item. Розпакуйте повний RentalDesk-Server-Install.zip." }
}

Write-Host "Оновлення: $installDir" -ForegroundColor Cyan
Write-Host 'База даних і конфігурація залишаться на місці. Перед оновленням буде створено копію бази.'
$confirm = Read-Host 'Для продовження введіть UPDATE'
if ($confirm -cne 'UPDATE') { Write-Host 'Оновлення скасовано.'; exit 0 }

Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
try {
    & $venvPython (Join-Path $installDir 'backup_db.py') --config $configPath
    if ($LASTEXITCODE -ne 0) { throw 'Не вдалося створити копію. Файли програми не оновлено.' }
    foreach ($name in @('app.py','backup_db.py','run_server.py','restore_db.py','contract_template.md','requirements.txt','restore_backup.ps1','service_manager.ps1','server_manager.py','open_server_manager.ps1')) {
        $from = Join-Path $sourceDir $name
        if (Test-Path -LiteralPath $from) { Copy-Item -LiteralPath $from -Destination $installDir -Force }
    }
    foreach ($folder in @('templates','static')) {
        Copy-Item -LiteralPath (Join-Path $sourceDir $folder) -Destination $installDir -Recurse -Force
    }
    $managerShortcut = Join-Path $env:Public 'Desktop\RentalDesk - сервер.lnk'
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($managerShortcut)
    $shortcut.TargetPath = (Get-Command powershell.exe).Source
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $installDir 'open_server_manager.ps1')`""
    $shortcut.WorkingDirectory = $installDir
    $shortcut.Description = 'Керування сервером RentalDesk'
    $shortcut.Save()
    Remove-Item -LiteralPath (Join-Path $env:Public 'Desktop\RentalDesk - керування сервером.lnk') -ErrorAction SilentlyContinue
    Write-Host 'Файли оновлено. Запускаю сервер...' -ForegroundColor Green
} finally {
    Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}
Write-Host 'Оновлення завершено. Налаштування, база та користувачі збережені.' -ForegroundColor Green
