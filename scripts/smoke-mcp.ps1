$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..')
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "Missing $python. Create the venv and run: .\.venv\Scripts\python.exe -m pip install -e `".[dev]`""
}
& $python (Join-Path $PSScriptRoot 'smoke_mcp.py')
if ($LASTEXITCODE -ne 0) {
    throw "smoke failed with exit $LASTEXITCODE"
}
