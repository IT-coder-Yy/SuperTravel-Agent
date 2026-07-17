[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$SkipBackend,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$demoRoot = Join-Path $projectRoot "examples\fastapi_react_demo"
$frontendRoot = Join-Path $demoRoot "frontend"
$pytestTemp = Join-Path $demoRoot "outputs\pytest-baseline"

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipBackend) {
    Write-Host "[1/4] Running backend tests (325 baseline tests)..."
    Push-Location $demoRoot
    try {
        & $Python -m pytest backend/tests -q -p no:cacheprovider --basetemp $pytestTemp
        Assert-LastExitCode "Backend tests"
    } finally {
        Pop-Location
    }
} else {
    Write-Host "[1/4] Backend tests skipped."
}

Push-Location $frontendRoot
try {
    Write-Host "[2/4] Running TypeScript type check..."
    & npm run type-check
    Assert-LastExitCode "TypeScript type check"

    Write-Host "[3/4] Running frontend unit tests (52 baseline tests)..."
    & npm test
    Assert-LastExitCode "Frontend unit tests"

    if (-not $SkipBuild) {
        Write-Host "[4/4] Running frontend production build..."
        & npm run build
        Assert-LastExitCode "Frontend production build"
    } else {
        Write-Host "[4/4] Frontend production build skipped."
    }
} finally {
    Pop-Location
}

Write-Host "Stage 0 baseline verification passed."
