cls
@echo off

echo.
echo Cleaning bin directory (keeping config.json)...
for %%f in (.\bin\*) do if /i not "%%~nxf"=="config.json" del "%%f"

echo Building config.exe...
python3 -m PyInstaller --onefile --windowed --distpath ./bin --name VeadotubeProxyClientConfig config.py -D -F
python3 -m PyInstaller --onefile --windowed --distpath ./bin --name VeadotubeProxy proxy.py -D -F
echo.
echo Build process completed.

echo Copying python files to bin directory...
copy ".\client.py" ".\bin\client.py"
copy ".\config_manager.py" ".\bin\config_manager.py"
copy ".\client.bat" ".\bin\client.bat"
echo.

echo The executables are located in ./bin/
echo Removing build artifacts...
del *.spec
rd /q /s "./build/"
echo.
pause