@echo off
REM ====================================================================
REM  geolens - Windows installer.  Double-click this file.
REM
REM  All it does is find a Python and hand over to install.py, which
REM  builds its own environment.  Anaconda is checked first only because
REM  it is the Python most likely to exist on a GIS machine -- install.py
REM  picks a suitable version for the environment itself, and will not
REM  install anything into whichever interpreter is found here.
REM ====================================================================
setlocal
cd /d "%~dp0"

set "PY="

REM 1. Anaconda / Miniconda / Miniforge in the usual places.
for %%P in (
  "%USERPROFILE%\anaconda3\python.exe"
  "%USERPROFILE%\miniconda3\python.exe"
  "%USERPROFILE%\miniforge3\python.exe"
  "%USERPROFILE%\mambaforge\python.exe"
  "C:\ProgramData\anaconda3\python.exe"
  "C:\ProgramData\miniconda3\python.exe"
) do if not defined PY if exist %%P set "PY=%%~P"

REM 2. Whatever python is on PATH.
if not defined PY (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PY set "PY=%%P"
)

REM 3. The py launcher.  Resolve it to a real interpreter path rather than
REM    keeping "py -3" as the command: PY has to be quotable below, because
REM    ArcGIS lives under C:\Program Files and that space breaks an
REM    unquoted invocation.
if not defined PY (
  for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do if not defined PY set "PY=%%P"
)

REM 4. ArcGIS Pro's bundled Python, last resort.  Fine for RUNNING the
REM    installer; install.py deliberately will not install INTO it.
if not defined PY (
  for /d %%D in ("%LOCALAPPDATA%\ESRI\conda\envs\*") do (
    if not defined PY if exist "%%D\python.exe" set "PY=%%D\python.exe"
  )
)
if not defined PY (
  if exist "C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe" (
    set "PY=C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe"
  )
)

if not defined PY (
  echo.
  echo   No Python found on this machine.
  echo.
  echo   Install Miniforge ^(small, and it is the conda that works best with
  echo   the GDAL stack^): https://conda-forge.org/download/
  echo   Then double-click this file again.
  echo.
  pause
  exit /b 1
)

echo Using Python: "%PY%"
echo.
"%PY%" "%~dp0install.py" %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo Finished.
) else (
  echo Finished with problems - scroll up for the details.
)
echo.
pause
exit /b %RC%
