@echo off
rem cgui-studio: avvio rapido (Windows). Il server gira sul TUO PC.
rem Uso: double click. Per la demo senza login: CGUI_MOCK=1 (vedi README).

cd /d "%~dp0"

where uv >nul 2>nul || (
  echo Manca 'uv' gestore Python^. Installa con:
  echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  echo poi rilancia questo file.
  pause
  exit /b 1
)

uv sync --quiet || (echo uv sync fallito: guarda il messaggio sopra. & pause & exit /b 1)

if not exist "web\dist\index.html" (
  rem La GUI buildata non e' nel repo: serve Node una volta sola.
  where node >nul 2>nul || where npm >nul 2>nul || (
    echo Manca Node/npm servono UNA volta sola per costruire la GUI.
    echo Installa Node LTS da https://nodejs.org e rilancia run.bat
    pause
    exit /b 1
  )
  echo Prima esecuzione: costruisco la GUI con npm...
  pushd web
  call npm install --no-audit --no-fund
  call npm run build
  popd
)

set "PORT=8756"
if defined CGUI_PORT set "PORT=%CGUI_PORT%"
set "URL=http://127.0.0.1:%PORT%"
echo cgui-studio su %URL%  (CTRL+C per fermare)

start "" /b cmd /c "timeout /t 5 /nobreak >nul & start %URL%"

uv run cgui serve --port %PORT%
pause
