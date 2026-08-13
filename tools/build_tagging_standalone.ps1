$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name "Gelbooru-Tagging-Helper" `
    --add-data "resources/i18n;resources/i18n" `
    "src/booruflow/presentation/pyside6/tagging_standalone.py"

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$readme = Join-Path $projectRoot "docs\gelbooru-tagging-helper.md"
$destination = Join-Path $projectRoot "dist\Gelbooru-Tagging-Helper\README.md"
Copy-Item -LiteralPath $readme -Destination $destination -Force
Write-Host "Portable build: $($destination | Split-Path -Parent)"
