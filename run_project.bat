@echo off
rem ===========================================================================
rem  geolens: a whole project from one boundary, left to run on its own.
rem
rem  Drag a boundary (shapefile, GeoPackage, GeoJSON or KML) onto this file.
rem  Or double-click it and paste a boundary path, or a map service layer URL
rem  and a where clause (a BLM field office, a forest district).
rem
rem  It maps the valleys of every HUC12 watershed the boundary touches, and
rem  those draining into them, on the 10 m USGS DEM; fetches SSURGO soils;
rem  runs a pilot of HUC12s on 1 m lidar; and writes it all to a folder
rem  beside the boundary. RESULTS.md there says what ran and how far to trust
rem  it. Run this again on the same boundary to carry on where it stopped.
rem  Needs internet, and can take hours on a large area.
rem
rem  Uses geolens's own Python on this drive (setup_portable.bat builds it),
rem  or else the environment install_windows.bat made.
rem ===========================================================================
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

set "PY="
if exist "%ROOT%\env\python.exe" (
  set "PY=%ROOT%\env\python.exe"
  set "TMP=%ROOT%\tmp"
  set "TEMP=%ROOT%\tmp"
  set "LOCALAPPDATA=%ROOT%\home\cache"
  set "MPLCONFIGDIR=%ROOT%\home\matplotlib"
  set "NUMBA_CACHE_DIR=%ROOT%\home\numba"
  set "GEOLENS_HOME=%ROOT%\home"
  set "GEOLENS_NETRC=%ROOT%\home\_netrc"
  set "PYTHONNOUSERSITE=1"
  if not exist "%ROOT%\tmp" mkdir "%ROOT%\tmp"
)
if not defined PY if defined GEOLENS_PYTHON if exist "%GEOLENS_PYTHON%" set "PY=%GEOLENS_PYTHON%"
if not defined PY for %%B in ("%USERPROFILE%\miniforge3" "%USERPROFILE%\mambaforge" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniforge3" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\anaconda3" "C:\ProgramData\miniforge3" "C:\ProgramData\miniconda3" "C:\ProgramData\anaconda3") do if not defined PY if exist "%%~B\envs\geolens\python.exe" set "PY=%%~B\envs\geolens\python.exe"
if not defined PY if exist "%USERPROFILE%\.conda\envs\geolens\python.exe" set "PY=%USERPROFILE%\.conda\envs\geolens\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\ESRI\conda\envs\geolens\python.exe" set "PY=%LOCALAPPDATA%\ESRI\conda\envs\geolens\python.exe"
if not defined PY (
  echo.
  echo  No geolens environment found. Double-click setup_portable.bat first
  echo  ^(it builds one on this drive^), then run this again.
  echo.
  pause
  exit /b 1
)

set "BOUNDARY=%~1"
if not defined BOUNDARY (
  echo.
  echo  Drag a boundary file onto run_project.bat, or paste one here.
  echo  A map service layer URL works too; a where clause is asked next.
  echo.
  set /p "BOUNDARY=Boundary: "
)
if not defined BOUNDARY exit /b 1
set "BOUNDARY=%BOUNDARY:"=%"

set "WHERE="
set "OUT="
if /i "%BOUNDARY:~0,4%"=="http" (
  set /p "WHERE=Which features (where clause, blank for all): "
  set /p "OUT=Project folder (blank: %ROOT%\..\geolens_project): "
)
if /i "%BOUNDARY:~0,4%"=="http" if not defined OUT set "OUT=%ROOT%\..\geolens_project"

set "PYTHONPATH=%ROOT%\src"
set "ARGS="
if defined WHERE set ARGS=--where "%WHERE%"
if defined OUT set ARGS=%ARGS% --out "%OUT%"

echo.
echo  geolens project: "%BOUNDARY%"
echo  Python: %PY%
echo.
"%PY%" -m geolens.cli project "%BOUNDARY%" %ARGS%
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (
  echo  Done. RESULTS.md in the project folder says what ran.
) else (
  echo  Stopped with an error ^(above^). Run this again to carry on where it
  echo  stopped: finished steps are kept.
)
echo.
pause
exit /b %RC%
