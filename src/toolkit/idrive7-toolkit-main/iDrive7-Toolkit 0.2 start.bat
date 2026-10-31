@echo off
rem Starts the iDrive 7&8 Toolkit without a console window.
rem If the Python package customtkinter is missing, it is installed on the first start.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto :nopy
%PY% -c "import customtkinter, PIL" >nul 2>nul && goto :run
echo Installing CustomTkinter (one time only) ...
%PY% -m pip install --user --upgrade customtkinter pillow || goto :piperr
:run
where pyw >nul 2>nul && (start "" pyw -3 "%~dp0idrive7_toolkit_v0.2.py" & exit /b 0)
where pythonw >nul 2>nul && (start "" pythonw "%~dp0idrive7_toolkit_v0.2.py" & exit /b 0)
start "" %PY% "%~dp0idrive7_toolkit_v0.2.py"
exit /b 0

:nopy
echo Python 3 was not found.
echo Please install it from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" during setup), then start this file again.
pause
exit /b 1

:piperr
echo.
echo Installing customtkinter failed (see above).
echo To install it by hand:  py -m pip install customtkinter
pause
exit /b 1
