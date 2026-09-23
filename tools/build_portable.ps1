param([switch]$SkipSnapshotGeneration)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
$snapshots = Join-Path $project 'build\bootstrap'
$distribution = Join-Path $project 'dist\BooruFlow'

if (-not $SkipSnapshotGeneration) {
    & $python (Join-Path $project 'tools\build_portable_bootstrap.py') `
        --settings (Join-Path $project 'config\booruflow_settings.json') `
        --output $snapshots
    if ($LASTEXITCODE -ne 0) { throw 'Snapshot generation failed' }
}

& $python -m PyInstaller --clean --noconfirm `
    --distpath (Join-Path $project 'dist') `
    --workpath (Join-Path $project 'build') `
    (Join-Path $project 'tools\booruflow.spec')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }

$bootstrap = Join-Path $distribution 'bootstrap'
New-Item -ItemType Directory -Path $bootstrap -Force | Out-Null
foreach ($name in @('gelbooru-tags.zip', 'gelbooru-aliases.zip', 'manifest.json')) {
    Copy-Item -LiteralPath (Join-Path $snapshots $name) `
        -Destination (Join-Path $bootstrap $name) -Force
}
if (Test-Path -LiteralPath (Join-Path $project 'dist\BooruFlow.exe')) {
    throw 'Unexpected parallel EXE in dist'
}
Write-Output 'Portable onedir bundle assembled.'
