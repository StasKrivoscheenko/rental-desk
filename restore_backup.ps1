$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principalCheck = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Запустіть PowerShell від імені адміністратора.'
}

$configPath = Join-Path $env:ProgramData 'RentalDesk\config.json'
if (-not (Test-Path -LiteralPath $configPath)) { throw "Не знайдено конфігурацію: $configPath" }
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$backupDir = $config.backup_dir
$backups = @(Get-ChildItem -LiteralPath $backupDir -Filter 'rentaldb_*.sqlite3' -File | Sort-Object LastWriteTime -Descending)
if ($backups.Count -eq 0) { throw "У папці немає резервних копій: $backupDir" }

Write-Host 'Доступні резервні копії (від найновішої):' -ForegroundColor Cyan
for ($i = 0; $i -lt $backups.Count; $i++) {
    Write-Host "[$($i + 1)] $($backups[$i].LastWriteTime)  $($backups[$i].Name)"
}
$choiceText = Read-Host 'Введіть номер копії для відновлення (0 — скасувати)'
$choice = 0
if (-not [int]::TryParse($choiceText, [ref]$choice) -or $choice -lt 0 -or $choice -gt $backups.Count) { throw 'Неправильний номер резервної копії.' }
if ($choice -eq 0) { Write-Host 'Відновлення скасовано.'; exit 0 }
$selected = $backups[$choice - 1]
Write-Host "Базу буде замінено копією $($selected.Name). Перед заміною поточну базу буде збережено." -ForegroundColor Yellow
$confirm = Read-Host 'Для підтвердження введіть RESTORE'
if ($confirm -cne 'RESTORE') { Write-Host 'Відновлення скасовано.'; exit 0 }

$installDir = $PSScriptRoot
$venvPython = Join-Path $installDir '.venv\Scripts\python.exe'
$serverTask = 'RentalDesk Server'
Stop-ScheduledTask -TaskName $serverTask -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2
try {
    & $venvPython (Join-Path $installDir 'backup_db.py') --config $configPath
    if ($LASTEXITCODE -ne 0) { throw 'Не вдалося створити копію поточної бази; відновлення зупинено.' }
    & $venvPython (Join-Path $installDir 'restore_db.py') --config $configPath --backup $selected.FullName
    if ($LASTEXITCODE -ne 0) { throw 'Відновлення не завершилося. Поточна база та її резервна копія збережені.' }
    Write-Host 'Базу відновлено. Перевірте оренди у програмі.' -ForegroundColor Green
} finally {
    Start-ScheduledTask -TaskName $serverTask -ErrorAction SilentlyContinue
}
