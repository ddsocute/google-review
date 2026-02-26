<# Google Maps Restaurant Review AI - Simple launcher script
   Double-click start.bat will call this file:
   - Create venv if needed
   - Install requirements.txt
   - Run app.py (Flask)
   - Open browser to http://localhost:5000
#>

$ErrorActionPreference = 'Stop'

Set-Location -LiteralPath $PSScriptRoot

Write-Host "============================================"
Write-Host "  Google Maps Restaurant Review AI"
Write-Host "============================================"
Write-Host ""

function Fail {
    param(
        [string]$Message
    )

    Write-Host ""
    Write-Host "[ERROR] $Message"
    Write-Host ""
    Write-Host "Press any key to close . . ."
    $null = $Host.UI.RawUI.ReadKey('NoEcho,IncludeKeyDown')
    exit 1
}

# Check Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Fail "Python not found. Please install Python 3.9+ and add it to PATH."
}

# Create / check venv
$venvPython = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "[SETUP] Creating Python virtual environment (venv) ..."
    try {
        & python -m venv venv
    } catch {
        Fail "Failed to create virtual environment: $($_.Exception.Message)"
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Fail "venv\Scripts\python.exe not found. Virtual environment may not have been created correctly."
}

# Install dependencies
$requirements = Join-Path $PSScriptRoot 'requirements.txt'
if (Test-Path -LiteralPath $requirements) {
    Write-Host "[SETUP] Installing dependencies from requirements.txt ..."
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        Fail "Dependency installation failed (exit=$LASTEXITCODE)."
    }
} else {
    Write-Host "[WARN] requirements.txt not found. Skipping dependency installation."
}

# Check app.py
$appPath = Join-Path $PSScriptRoot 'app.py'
if (-not (Test-Path -LiteralPath $appPath)) {
    Fail "app.py not found in folder: $((Get-Location).Path)"
}

Write-Host ""
Write-Host "============================================"
Write-Host "  Starting Flask server ..."
Write-Host "  Opening browser: http://localhost:5000"
Write-Host "  Press Ctrl+C to stop the server"
Write-Host "============================================"
Write-Host ""

try {
    Start-Process "http://localhost:5000/?v=20260228" | Out-Null
} catch {
    # Browser open failure should not stop the server
}

# Run Flask server
& $venvPython $appPath

