@echo off
cls
setlocal

set PY=python3
where %PY% >nul 2>nul || set PY=python

echo === Veadotube Bridge build ===
echo.

echo Checking dependencies...
%PY% -m pip install -r requirements.txt --quiet --disable-pip-version-check || goto :failed
%PY% -m pip install pyinstaller --quiet --disable-pip-version-check || goto :failed

echo Running the self-test...
%PY% smoketest.py || goto :failed
echo.

echo Cleaning bin (config.json is kept)...
if not exist ".\bin" mkdir ".\bin"
for %%f in (.\bin\*) do if /i not "%%~nxf"=="config.json" del "%%f"
if exist ".\bin\logs" rd /q /s ".\bin\logs"
if exist ".\bin\runtime" rd /q /s ".\bin\runtime"

echo Building VeadotubeBridge.exe...
%PY% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name VeadotubeBridge ^
    --distpath .\bin ^
    --workpath .\build ^
    --specpath .\build ^
    --collect-submodules websockets ^
    --hidden-import websocket ^
    main.py || goto :failed

echo.
echo Removing build artifacts...
if exist ".\build" rd /q /s ".\build"

echo.
echo Done. The app is .\bin\VeadotubeBridge.exe
echo Ship that single file - it writes config.json, logs\ and runtime\ next to itself.
echo.
pause
exit /b 0

:failed
echo.
echo BUILD FAILED - see the output above.
echo.
pause
exit /b 1
