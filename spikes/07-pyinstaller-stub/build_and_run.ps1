# Build the brain stub with PyInstaller (one-folder) and run the Electron supervisor against it.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = "..\.venv\Scripts\python.exe"
$t0 = Get-Date
& $py -m PyInstaller --noconfirm --clean --onedir --name brain_stub --console `
    --collect-all fastapi --collect-all uvicorn --collect-all starlette --collect-all anyio `
    --collect-all websockets --hidden-import pydantic `
    brain_stub.py
"build time: $(((Get-Date) - $t0).TotalSeconds) s"
$size = (Get-ChildItem dist\brain_stub -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
"dist size: {0:N1} MB, files: {1}" -f $size, (Get-ChildItem dist\brain_stub -Recurse -File).Count
# Electron from spike 1
& ..\01-overlay-vrm\node_modules\electron\dist\electron.exe supervisor.js
