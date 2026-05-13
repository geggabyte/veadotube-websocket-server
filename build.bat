@echo off
echo Building config.exe...
python3 -m PyInstaller config.py -D -F --distpath ./bin
echo.
echo Build process completed.
echo Copying python files to bin directory...
copy ".\client.py" ".\bin\client.py"
copy ".\config_manager.py" ".\bin\config_manager.py"
copy ".\client.bat" ".\bin\client.bat"
copy ".\proxy.py" ".\bin\proxy.py"
copy ".\proxy.bat" ".\bin\proxy.bat"
echo.
echo The executables are located in ./bin/
pause