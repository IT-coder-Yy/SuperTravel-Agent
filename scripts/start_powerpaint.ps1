Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$powerPaintDir = Join-Path $projectRoot "PowerPaint"

if (-not (Test-Path -LiteralPath (Join-Path $powerPaintDir "app.py"))) {
    throw "PowerPaint/app.py was not found. Make sure PowerPaint is cloned into the project root."
}

Set-Location -LiteralPath $powerPaintDir

$env:HF_HUB_DISABLE_XET = "1"
$env:HF_HUB_ENABLE_HF_TRANSFER = "0"
$env:HF_HUB_DOWNLOAD_TIMEOUT = "120"
$env:HF_HUB_ETAG_TIMEOUT = "120"
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"

$pptPython = "D:\Anaconda\anaconda3\envs\ppt\python.exe"
if (Test-Path -LiteralPath $pptPython) {
    & $pptPython -u app.py --port 7860 --local_files_only
} else {
    conda run -n ppt python app.py --port 7860 --local_files_only
}
