$ErrorActionPreference = 'Stop'
$sourceDir = $PSScriptRoot
$outputPath = Join-Path (Split-Path -Parent $sourceDir) 'RentalDesk-Server-Next-Install.zip'
$items = @(
    'README.md','app.py','requirements.txt','contract_template.md','run_server.py','backup_db.py',
    'restore_db.py','install_server.ps1','restore_backup.ps1','service_manager.ps1','update_server.ps1',
    'server_manager.py','open_server_manager.ps1','templates','static'
) | ForEach-Object { Join-Path $sourceDir $_ }
foreach ($item in $items) {
    if (-not (Test-Path -LiteralPath $item)) { throw "Не знайдено файл пакета: $item" }
}
Compress-Archive -Path $items -DestinationPath $outputPath -Force
Write-Host "Пакет для перенесення на сервер створено: $outputPath" -ForegroundColor Green
