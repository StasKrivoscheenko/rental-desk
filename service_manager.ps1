$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $scriptPath = $MyInvocation.MyCommand.Path
    Start-Process powershell.exe -Verb RunAs -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',"`"$scriptPath`"")
    exit
}

$installDir = $PSScriptRoot
$configPath = Join-Path $env:ProgramData 'RentalDesk\config.json'
$serverTask = 'RentalDesk Server'
$backupTask = 'RentalDesk Daily Backup'
$python = Join-Path $installDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $configPath) -or -not (Test-Path -LiteralPath $python)) {
    throw 'Не знайдено встановлення RentalDesk. Перевірте папку програми та C:\ProgramData\RentalDesk\config.json.'
}

function Show-Menu {
    Clear-Host
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $task = Get-ScheduledTask -TaskName $serverTask -ErrorAction SilentlyContinue
    $state = if ($task) { $task.State } else { 'не знайдено' }
    Write-Host 'RentalDesk — керування сервером' -ForegroundColor Cyan
    Write-Host "Стан: $state | Адреса: $($config.public_url) | Автокопія: щодня о $($config.backup_time)"
    Write-Host ''
    Write-Host '[1] Запустити сервер'
    Write-Host '[2] Перезапустити сервер'
    Write-Host '[3] Зупинити сервер'
    Write-Host '[4] Створити резервну копію зараз'
    Write-Host '[5] Змінити час щоденної копії'
    Write-Host '[6] Відкрити програму в браузері'
    Write-Host '[7] Відновити базу з резервної копії'
    Write-Host '[0] Вийти'
}

do {
    Show-Menu
    $choice = Read-Host 'Оберіть дію'
    switch ($choice) {
        '1' { Start-ScheduledTask -TaskName $serverTask; Write-Host 'Команду запуску надіслано.' -ForegroundColor Green; Start-Sleep -Seconds 1 }
        '2' { Stop-ScheduledTask -TaskName $serverTask -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; Start-ScheduledTask -TaskName $serverTask; Write-Host 'Сервер перезапускається.' -ForegroundColor Green; Start-Sleep -Seconds 1 }
        '3' { Stop-ScheduledTask -TaskName $serverTask -ErrorAction SilentlyContinue; Write-Host 'Сервер зупинено.' -ForegroundColor Yellow; Start-Sleep -Seconds 1 }
        '4' {
            & $python (Join-Path $installDir 'backup_db.py') --config $configPath --label manual
            if ($LASTEXITCODE -ne 0) { Write-Host 'Не вдалося створити копію. Перевірте logs\backup.log.' -ForegroundColor Red }
            Read-Host 'Натисніть Enter'
        }
        '5' {
            $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
            $time = Read-Host "Новий час у форматі HH:mm [$($config.backup_time)]"
            if ([string]::IsNullOrWhiteSpace($time)) { $time = $config.backup_time }
            $parsed = [TimeSpan]::Zero
            if (-not [TimeSpan]::TryParseExact($time, 'hh\:mm', [Globalization.CultureInfo]::InvariantCulture, [ref]$parsed)) {
                Write-Host 'Неправильний формат часу. Приклад: 06:00 або 21:30.' -ForegroundColor Red; Start-Sleep -Seconds 2; break
            }
            $config.backup_time = $parsed.ToString('hh\:mm')
            [System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json), [System.Text.UTF8Encoding]::new($false))
            $action = Get-ScheduledTask -TaskName $backupTask | Select-Object -ExpandProperty Actions
            $trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::Today.Add($parsed))
            $principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
            $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
            Register-ScheduledTask -TaskName $backupTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Daily SQLite backup of RentalDesk at $($config.backup_time)" -Force | Out-Null
            $addressFile = Join-Path $config.data_dir 'SERVER-ADDRESS.txt'
            @("Адреса для працівників: $($config.public_url)", "Початкове налаштування адміністратора: $($config.public_url)/setup", "Планова резервна копія: щодня о $($config.backup_time)", "Папка резервних копій: $($config.backup_dir)") | Set-Content -LiteralPath $addressFile -Encoding UTF8
            Stop-ScheduledTask -TaskName $serverTask -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2; Start-ScheduledTask -TaskName $serverTask
            Write-Host "Розклад оновлено: щодня о $($config.backup_time)." -ForegroundColor Green; Start-Sleep -Seconds 2
        }
        '6' { $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json; Start-Process $config.public_url }
        '7' { & (Join-Path $installDir 'restore_backup.ps1'); Read-Host 'Натисніть Enter для повернення до меню' }
        '0' { break }
        default { Write-Host 'Невідома команда.' -ForegroundColor Yellow; Start-Sleep -Seconds 1 }
    }
} while ($choice -ne '0')
