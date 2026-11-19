@echo off
rem Double-click to start ArcLayer.
cd /d "%~dp0"
python -c "import numpy, trimesh, shapely, scipy, webview, paho.mqtt" 2>nul
if errorlevel 1 (
  echo First start: installing the libraries the app needs...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Could not install the libraries. Check that Python 3.10+ is installed and on PATH.
    pause
    exit /b 1
  )
)
start "" pythonw -m app
