# Launch the DWG-native pipeline UI (FastAPI) on http://127.0.0.1:8095
# Reuses the webapp venv (SolidWorks COM + ezdxf already installed there).
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $here "webapp\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { Write-Error "venv python not found at $py — run webapp\run.ps1 once to create it."; exit 1 }
$env:PYTHONPATH = $here
Write-Host ""
Write-Host "  DWG -> SLDPRT (native import)  ->  http://127.0.0.1:8095"
Write-Host "  (Ctrl+C to stop)"
Write-Host ""
& $py -m uvicorn dwg_native.api.app:app --host 127.0.0.1 --port 8095
