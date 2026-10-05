[CmdletBinding()]
param(
    [switch]$SkipSnapshotGeneration,
    [ValidateSet('CPU', 'CUDA')][string]$RuntimeProfile = 'CPU'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$FailureMessage,
        [Parameter(Mandatory = $true)][scriptblock]$Command
    )
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (exit code $LASTEXITCODE)"
    }
}

function Require-File([string]$Path, [string]$Description) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Description not found: $Path"
    }
}

$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $project '.venv\Scripts\python.exe'
$spec = Join-Path $project 'tools\booruflow.spec'
$snapshotBuilder = Join-Path $project 'tools\build_portable_bootstrap.py'
$settings = Join-Path $project 'config\booruflow_settings.json'
$distRoot = Join-Path $project 'dist'
$buildRoot = Join-Path $project 'build'
$temporaryRoot = Join-Path $buildRoot ("portable-build-" + [guid]::NewGuid().ToString('N'))
$temporaryDist = Join-Path $temporaryRoot 'dist'
$temporaryWork = Join-Path $temporaryRoot 'work'
$temporarySnapshots = Join-Path $temporaryRoot 'bootstrap'
$published = $false
$destination = $null
$RuntimeProfile = $RuntimeProfile.ToUpperInvariant()

try {
    Set-Location -LiteralPath $project
    Write-Step 'Checking build prerequisites'
    Require-File $python 'Project Python interpreter'
    Require-File $spec 'PyInstaller spec'
    Require-File $snapshotBuilder 'Bootstrap snapshot builder'
    if (-not $SkipSnapshotGeneration) {
        Require-File $settings 'BooruFlow settings file required for database snapshots'
    }
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
        throw 'Git was not found in PATH.'
    }

    $gitRoot = (& git.exe rev-parse --show-toplevel 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $gitRoot) {
        throw 'The project directory is not a readable Git working tree.'
    }
    if ((Resolve-Path -LiteralPath $gitRoot).Path -ne $project) {
        throw "Git root does not match the project root: $gitRoot"
    }

    $pythonVersion = (& $python --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read the Python version.' }
    $pyInstallerVersion = (& $python -m PyInstaller --version 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller is not installed in the project virtual environment.'
    }
    if ($RuntimeProfile -eq 'CUDA') {
        $cudaProviderAvailable = (& $python -c `
            "import onnxruntime as ort; print('CUDAExecutionProvider' in ort.get_available_providers())" `
            2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0 -or $cudaProviderAvailable -ne 'True') {
            throw 'CUDAExecutionProvider is not available in the project Python environment.'
        }
    }

    Write-Step 'Reading Git build identity'
    $branch = (& git.exe branch --show-current 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read the current Git branch.' }
    if (-not $branch) { $branch = 'DETACHED' }
    $commit = (& git.exe rev-parse HEAD 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $commit) { throw 'Unable to read the Git commit.' }
    $commitShort = (& git.exe rev-parse --short=7 HEAD 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $commitShort) { throw 'Unable to read the short Git commit.' }
    $statusOutput = (& git.exe status --porcelain --untracked-files=normal 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read the Git working-tree status.' }
    $workingTree = if ([string]::IsNullOrWhiteSpace($statusOutput)) { 'CLEAN' } else { 'DIRTY' }

    $builtAt = Get-Date
    $dirtySegment = if ($workingTree -eq 'DIRTY') { '-dirty' } else { '' }
    $profileSegment = if ($RuntimeProfile -eq 'CUDA') { '-gpu' } else { '' }
    $baseName = 'BooruFlow-{0}-g{1}{2}-{3}{4}' -f `
        $builtAt.ToString('yyyy.MM.dd'), $commitShort, $dirtySegment, `
        $builtAt.ToString('HHmm'), $profileSegment
    $destination = Join-Path $distRoot $baseName
    $suffix = 2
    while (Test-Path -LiteralPath $destination) {
        $destination = Join-Path $distRoot ("$baseName-$suffix")
        $suffix++
    }

    Write-Host "    Branch:       $branch"
    Write-Host "    Commit:       $commitShort"
    Write-Host "    Working tree: $workingTree"
    Write-Host "    Runtime:      $RuntimeProfile"
    Write-Host "    Output name:  $([System.IO.Path]::GetFileName($destination))"

    New-Item -ItemType Directory -Path $temporaryDist, $temporaryWork, $temporarySnapshots -Force | Out-Null

    if (-not $SkipSnapshotGeneration) {
        Write-Step 'Creating read-only public database snapshots'
        Invoke-CheckedNative 'Snapshot generation failed' {
            & $python $snapshotBuilder --settings $settings --output $temporarySnapshots
        }
    }
    else {
        $existingSnapshots = Join-Path $buildRoot 'bootstrap'
        Write-Step 'Using existing bootstrap snapshots (explicitly requested)'
        foreach ($name in @('gelbooru-tags.zip', 'gelbooru-aliases.zip', 'manifest.json')) {
            Require-File (Join-Path $existingSnapshots $name) "Existing bootstrap file '$name'"
            Copy-Item -LiteralPath (Join-Path $existingSnapshots $name) `
                -Destination (Join-Path $temporarySnapshots $name)
        }
    }

    Write-Step 'Running the existing PyInstaller onedir build'
    $env:BOORUFLOW_PORTABLE_RUNTIME_PROFILE = $RuntimeProfile
    Invoke-CheckedNative 'PyInstaller build failed' {
        & $python -m PyInstaller --clean --noconfirm `
            --distpath $temporaryDist `
            --workpath $temporaryWork `
            $spec
    }

    Write-Step 'Assembling and validating the portable bundle'
    $distribution = Join-Path $temporaryDist 'BooruFlow'
    $executable = Join-Path $distribution 'BooruFlow.exe'
    Require-File $executable 'Expected portable executable'
    if (Test-Path -LiteralPath (Join-Path $temporaryDist 'BooruFlow.exe')) {
        throw 'Unexpected parallel EXE was produced beside the onedir bundle.'
    }
    $cudaProvider = Join-Path $distribution `
        '_internal\onnxruntime\capi\onnxruntime_providers_cuda.dll'
    $tensorRtProvider = Join-Path $distribution `
        '_internal\onnxruntime\capi\onnxruntime_providers_tensorrt.dll'
    if ($RuntimeProfile -eq 'CUDA') {
        Require-File $cudaProvider 'Packaged CUDA execution provider'
        foreach ($relative in @(
            '_internal\nvidia\cu13\bin\x86_64\cublasLt64_13.dll',
            '_internal\nvidia\cu13\bin\x86_64\cublas64_13.dll',
            '_internal\nvidia\cu13\bin\x86_64\cufft64_12.dll',
            '_internal\nvidia\cu13\bin\x86_64\cudart64_13.dll',
            '_internal\nvidia\cudnn\bin\cudnn_engines_runtime_compiled64_9.dll',
            '_internal\nvidia\cudnn\bin\cudnn_engines_precompiled64_9.dll',
            '_internal\nvidia\cudnn\bin\cudnn_heuristic64_9.dll',
            '_internal\nvidia\cudnn\bin\cudnn_ops64_9.dll',
            '_internal\nvidia\cudnn\bin\cudnn_adv64_9.dll',
            '_internal\nvidia\cudnn\bin\cudnn_graph64_9.dll',
            '_internal\nvidia\cudnn\bin\cudnn64_9.dll',
            '_internal\nvidia\cudnn\bin\cudnn_engines_tensor_ir64_9.dll'
        )) {
            Require-File (Join-Path $distribution $relative) "Packaged CUDA dependency '$relative'"
        }
    }
    elseif (Test-Path -LiteralPath $cudaProvider) {
        throw 'The CPU profile unexpectedly contains the CUDA execution provider.'
    }
    if (Test-Path -LiteralPath $tensorRtProvider) {
        throw 'The portable bundle unexpectedly contains the unused TensorRT provider.'
    }

    $bootstrap = Join-Path $distribution 'bootstrap'
    New-Item -ItemType Directory -Path $bootstrap -Force | Out-Null
    foreach ($name in @('gelbooru-tags.zip', 'gelbooru-aliases.zip', 'manifest.json')) {
        Require-File (Join-Path $temporarySnapshots $name) "Generated bootstrap file '$name'"
        Copy-Item -LiteralPath (Join-Path $temporarySnapshots $name) `
            -Destination (Join-Path $bootstrap $name)
    }

    $buildInfo = @(
        'BooruFlow portable build'
        ''
        "Built: $($builtAt.ToString('yyyy-MM-dd HH:mm:ss zzz'))"
        "Branch: $branch"
        "Commit: $commit"
        "Commit short: $commitShort"
        "Working tree: $workingTree"
        "Runtime profile: $RuntimeProfile"
        "Python: $pythonVersion"
        "PyInstaller: $pyInstallerVersion"
    ) -join [Environment]::NewLine
    [System.IO.File]::WriteAllText(
        (Join-Path $distribution 'BUILD_INFO.txt'),
        $buildInfo + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )

    Write-Step 'Publishing the completed portable build'
    New-Item -ItemType Directory -Path $distRoot -Force | Out-Null
    if (Test-Path -LiteralPath $destination) {
        throw "The selected destination appeared during the build and will not be overwritten: $destination"
    }
    Move-Item -LiteralPath $distribution -Destination $destination
    $published = $true

    Write-Host ""
    Write-Host 'SUCCESS: portable build created.' -ForegroundColor Green
    Write-Host "Output: $destination" -ForegroundColor Green
}
catch {
    Write-Host ""
    Write-Host "BUILD FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'No incomplete portable build was published to dist.' -ForegroundColor Yellow
    exit 1
}
finally {
    if (Test-Path -LiteralPath $temporaryRoot) {
        $resolvedBuildRoot = [System.IO.Path]::GetFullPath($buildRoot).TrimEnd('\') + '\'
        $resolvedTemporaryRoot = [System.IO.Path]::GetFullPath($temporaryRoot)
        $safeLeaf = [System.IO.Path]::GetFileName($resolvedTemporaryRoot) -like 'portable-build-*'
        if ($safeLeaf -and $resolvedTemporaryRoot.StartsWith($resolvedBuildRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedTemporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if (-not $published) { exit 1 }
exit 0
