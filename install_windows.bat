@echo off
REM ====================================================================
REM  geolens - Windows installer.  Double-click this file.
REM
REM  With conda on this computer (Miniforge, Anaconda or Miniconda), it
REM  hands over to install.py, which builds or updates a permanent conda
REM  environment named geolens (and installs nothing into conda itself).
REM
REM  Without conda -- a new computer, one with only python.org's Python or
REM  ArcGIS Pro's, or only the Microsoft Store's "python" shortcut, which is
REM  not a Python at all -- it builds geolens's own Python instead, in
REM  "env" beside this file: setup_portable.bat downloads one program
REM  (micromamba) and the conda-forge packages. No admin rights, nothing
REM  installed into Windows. It does the same when this folder has its own
REM  "env" already (it updates it), and when asked to:
REM
REM    install_windows.bat --portable   geolens's own Python, in env here
REM    install_windows.bat --which      only say what it would use
REM ====================================================================
setlocal EnableExtensions
cd /d "%~dp0"
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "NOPAUSE=%GEOLENS_NO_PAUSE%"
set "MODE="
if /i "%~1"=="--portable" set "MODE=portable"
if /i "%~1"=="--which" set "MODE=which"
set "PY="
set "RC=1"

if "%MODE%"=="portable" (
  set "WHY=asked for with --portable"
  goto :own_python
)
if exist "%ROOT%\env\python.exe" (
  set "WHY=this folder has its own Python already, in env: updating it"
  goto :own_python
)

REM 1. Miniforge, Anaconda or Miniconda in the usual places.
for %%P in (
  "%USERPROFILE%\anaconda3\python.exe"
  "%USERPROFILE%\miniconda3\python.exe"
  "%USERPROFILE%\miniforge3\python.exe"
  "%USERPROFILE%\mambaforge\python.exe"
  "%LOCALAPPDATA%\miniforge3\python.exe"
  "%LOCALAPPDATA%\miniconda3\python.exe"
  "%LOCALAPPDATA%\anaconda3\python.exe"
  "C:\ProgramData\miniforge3\python.exe"
  "C:\ProgramData\anaconda3\python.exe"
  "C:\ProgramData\miniconda3\python.exe"
) do if not defined PY call :try "%%~P"

REM 2. A conda somewhere else, found by its conda command on PATH
REM    (condabin\conda.bat or Scripts\conda.exe: its python is one up).
if not defined PY for /f "delims=" %%C in ('where conda 2^>nul') do if not defined PY call :try "%%~dpC..\python.exe"

REM 3. A conda python on PATH. python.org's and ArcGIS Pro's are not used
REM    to build (install.py would make a pip venv from them, or use ArcGIS
REM    Pro's conda); the Microsoft Store's shortcut never runs.
if not defined PY for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY call :try "%%P"

if "%MODE%"=="which" if defined PY (
  echo.
  echo  install_windows.bat would use conda's Python: "%PY%"
  echo  and install.py would build or update the conda environment geolens.
  exit /b 0
)
if not defined PY (
  set "WHY=no conda on this computer"
  goto :own_python
)

echo Using Python: "%PY%"
echo.
"%PY%" "%~dp0install.py" %*
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto :finish
echo.
echo  install.py stopped with problems ^(above^). To build geolens's own
echo  Python in this folder instead ^(no conda needed^), run:
echo    install_windows.bat --portable
goto :finish

:own_python
if "%MODE%"=="which" (
  echo.
  echo  install_windows.bat would build geolens's own Python in "%ROOT%\env"
  echo  with setup_portable.bat: %WHY%.
  exit /b 0
)
echo.
echo  geolens's own Python: %WHY%.
echo  setup_portable.bat builds it in "%ROOT%\env" beside this file:
echo  micromamba, one program, and the conda-forge packages. About 3 GB and
echo  5-15 minutes the first time; no admin rights, and nothing is installed
echo  into Windows.
echo.
set "GEOLENS_NO_PAUSE=1"
call "%ROOT%\setup_portable.bat"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" (
  echo.
  echo  A project: drag a boundary file onto run_project.bat in this folder.
)
goto :finish

:finish
echo.
if "%RC%"=="0" echo Finished.
if not "%RC%"=="0" echo Finished with problems - scroll up for the details.
echo.
if not defined NOPAUSE pause
exit /b %RC%

:try
REM %1: a python.exe to try. It is used only if it exists, sits in a conda
REM install (a conda-meta folder beside it), is not under WindowsApps (the
REM Microsoft Store's shortcut only offers to install Python), and runs.
set "CAND=%~f1"
if not exist "%CAND%" exit /b 1
if not "%CAND:\WindowsApps\=%"=="%CAND%" (
  if "%MODE%"=="which" echo  not used: "%CAND%" - the Microsoft Store's shortcut
  exit /b 1
)
for %%D in ("%CAND%") do set "CDIR=%%~dpD"
if not exist "%CDIR%conda-meta\" (
  if "%MODE%"=="which" echo  not used: "%CAND%" - not a conda Python
  exit /b 1
)
"%CAND%" -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" >nul 2>&1
if errorlevel 1 (
  if "%MODE%"=="which" echo  not used: "%CAND%" - does not run, or older than Python 3.8
  exit /b 1
)
set "PY=%CAND%"
exit /b 0
