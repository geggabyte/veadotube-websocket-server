cls
@echo off
echo Building config.exe...
python3 -m PyInstaller --name VeadotubeProxyClientConfig config.py -D -F --distpath ./bin
python3 -m PyInstaller --onefile --windowed --name VeadotubeProxy proxy.py --distpath ./bin
echo.
echo Build process completed.
echo Copying python files to bin directory...
copy ".\client.py" ".\bin\client.py"
copy ".\config_manager.py" ".\bin\config_manager.py"
copy ".\client.bat" ".\bin\client.bat"
REM copy ".\proxy.py" ".\bin\proxy.py"
REM copy ".\proxy.bat" ".\bin\proxy.bat"
echo.
echo The executables are located in ./bin/
echo Removing build artifacts...
del *.spec
rd /q /s "./build/"
echo.
pause