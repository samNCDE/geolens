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
rem  Python: geolens's own in "env" beside this file, or the environment
rem  install_windows.bat made. With neither on this computer, it builds
rem  geolens's own first (setup_portable.bat: once, 5-15 minutes, nothing
rem  installed into Windows), then runs the project.
rem ===========================================================================
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "BOUNDARY=%~1"
title geolens project - leave this window open

call :find_python
if defined PY goto :have_python
echo.
echo  geolens has no Python environment on this computer yet, so it builds
echo  its own now, in "%ROOT%\env" beside this file: about 3 GB of downloads,
echo  5-15 minutes, once. No admin rights; nothing is installed into Windows.
echo  The project starts when it is ready.
echo.
if not defined GEOLENS_NO_PAUSE echo  Press a key to start, or close this window to stop.
if not defined GEOLENS_NO_PAUSE pause >nul
set "KEEP_NO_PAUSE=%GEOLENS_NO_PAUSE%"
set "GEOLENS_NO_PAUSE=1"
call "%ROOT%\setup_portable.bat"
set "SETUP_RC=%ERRORLEVEL%"
set "GEOLENS_NO_PAUSE=%KEEP_NO_PAUSE%"
if not "%SETUP_RC%"=="0" goto :no_python
call :find_python
if not defined PY goto :no_python

:have_python
rem the environment's own DLLs first, as conda activate would put them, and
rem no other program's Python, GDAL or PROJ settings (ArcGIS Pro's or QGIS's):
rem the environment's own GDAL and PROJ data instead
for %%P in ("%PY%") do set "ENVDIR=%%~dpP"
set "ENVDIR=%ENVDIR:~0,-1%"
set "PATH=%ENVDIR%;%ENVDIR%\Library\mingw-w64\bin;%ENVDIR%\Library\usr\bin;%ENVDIR%\Library\bin;%ENVDIR%\Scripts;%ENVDIR%\bin;%PATH%"
for %%V in (PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE GDAL_DATA GDAL_DRIVER_PATH PROJ_LIB PROJ_DATA GEOTIFF_CSV QT_PLUGIN_PATH CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_SHLVL CONDA_PROMPT_MODIFIER CONDA_PYTHON_EXE CONDA_EXE) do set "%%V="
if exist "%ENVDIR%\Library\share\gdal\" set "GDAL_DATA=%ENVDIR%\Library\share\gdal"
if exist "%ENVDIR%\Library\share\proj\" set "PROJ_DATA=%ENVDIR%\Library\share\proj"
if exist "%ENVDIR%\Library\share\proj\" set "PROJ_LIB=%ENVDIR%\Library\share\proj"
set "PYTHONPATH=%ROOT%\src"
set "PYTHONNOUSERSITE=1"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
if not defined PORTABLE goto :env_ready
rem geolens's own Python: its temporary files, caches and settings stay on
rem this drive too
set "TMP=%ROOT%\tmp"
set "TEMP=%ROOT%\tmp"
set "LOCALAPPDATA=%ROOT%\home\cache"
set "MPLCONFIGDIR=%ROOT%\home\matplotlib"
set "NUMBA_CACHE_DIR=%ROOT%\home\numba"
set "GEOLENS_HOME=%ROOT%\home"
set "GEOLENS_NETRC=%ROOT%\home\_netrc"
if not exist "%ROOT%\tmp" mkdir "%ROOT%\tmp"
:env_ready

echo.
echo  Python: %PY%
"%PY%" -c "import sys, geopandas, rasterio, pyproj, geolens; print('  geolens ' + geolens.__version__ + ' on Python ' + sys.version.split()[0])"
if not errorlevel 1 goto :python_ok
rem geolens's own Python, left half built by a setup that stopped: mended
rem once by setup_portable.bat, then checked again
if not defined PORTABLE goto :broken_python
if defined MENDED goto :broken_python
set "MENDED=1"
echo.
echo  geolens's own Python here is not complete ^(a setup that stopped part
echo  way^): setup_portable.bat mends it now, then the project starts.
set "KEEP_NO_PAUSE=%GEOLENS_NO_PAUSE%"
set "GEOLENS_NO_PAUSE=1"
call "%ROOT%\setup_portable.bat"
set "GEOLENS_NO_PAUSE=%KEEP_NO_PAUSE%"
call :find_python
if not defined PY goto :no_python
goto :have_python
:python_ok

if defined BOUNDARY goto :have_boundary
echo.
echo  Drag a boundary file onto run_project.bat, or paste one here.
echo  A map service layer URL works too; a where clause is asked next.
echo.
set /p "BOUNDARY=Boundary: "
:have_boundary
if not defined BOUNDARY goto :no_boundary
set "BOUNDARY=%BOUNDARY:"=%"
if not defined BOUNDARY goto :no_boundary

set "WHERE="
set "OUT="
if /i "%BOUNDARY:~0,4%"=="http" goto :service
rem a full path (a relative one is read from where this was started); a
rem shapefile's other files (.dbf, .shx, .prj ...) stand for its .shp
for %%F in ("%BOUNDARY%") do set "BOUNDARY=%%~fF"
for %%F in ("%BOUNDARY%") do set "BEXT=%%~xF"
for %%F in ("%BOUNDARY%") do set "BSHP=%%~dpnF.shp"
for %%X in (.dbf .shx .prj .cpg .sbn .sbx .qix .qmd) do if /i "%BEXT%"=="%%X" if exist "%BSHP%" set "BOUNDARY=%BSHP%"
if not exist "%BOUNDARY%" goto :no_boundary
rem the project folder geolens makes beside it: the boundary's name + _geolens
for %%F in ("%BOUNDARY%") do set "PROJ_DIR=%%~dpnF_geolens"
goto :run

:service
set /p "WHERE=Which features (where clause, blank for all): "
set /p "OUT=Project folder (blank: %ROOT%\..\geolens_project): "
if not defined OUT set "OUT=%ROOT%\..\geolens_project"
set "OUT=%OUT:"=%"
for %%F in ("%OUT%") do set "OUT=%%~fF"
set "PROJ_DIR=%OUT%"

:run
cd /d "%ROOT%"
set "ARGS="
if defined WHERE set ARGS=--where "%WHERE%"
if defined OUT set ARGS=%ARGS% --out "%OUT%"

echo.
echo  geolens project: "%BOUNDARY%"
echo.
set /a TRIES=0
:attempt
set /a TRIES+=1
"%PY%" -m geolens.cli project "%BOUNDARY%" %ARGS%
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto :done
if "%RC%"=="3" goto :busy
rem no project.json yet: it stopped before it began (the boundary could
rem not be read, say), and trying again would not help
if not exist "%PROJ_DIR%\project.json" goto :stopped
if %TRIES% GEQ 5 goto :stopped
echo.
echo  geolens stopped with an error (code %RC%, attempt %TRIES% of 5). It carries
echo  on where it stopped in 60 seconds; close this window to stop instead.
timeout /t 60 /nobreak >nul
goto :attempt

:done
echo.
echo  Done. RESULTS.md in the project folder says what ran.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 0

:stopped
echo.
echo  Stopped with an error (above). Run this again to carry on where it
echo  stopped: finished steps are kept.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b %RC%

:busy
echo.
echo  Another geolens run is working in that project folder, so nothing was
echo  started. Let it finish, or close its window, then run this again.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 3

:no_boundary
echo.
echo  No boundary file found: "%BOUNDARY%"
echo  Drag a shapefile ^(.shp^), GeoPackage, GeoJSON or KML onto run_project.bat.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 1

:no_python
echo.
echo  geolens's Python could not be built ^(see above^), so nothing was run.
echo  Check the internet connection and run this again: what was downloaded
echo  is kept.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 1

:broken_python
echo.
echo  This Python cannot load geolens's packages ^(above^): %PY%
echo  Double-click setup_portable.bat ^(geolens's own Python, on this drive^)
echo  or install_windows.bat to mend it, then run this again.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 1

:find_python
rem geolens's own Python beside this file first, then what install.py made:
rem a venv here, the one it recorded, or a conda environment named geolens
set "PY="
set "PORTABLE="
if exist "%ROOT%\env\python.exe" set "PY=%ROOT%\env\python.exe"
if defined PY set "PORTABLE=1"
if defined PY exit /b 0
if exist "%ROOT%\.venv\Scripts\python.exe" set "PY=%ROOT%\.venv\Scripts\python.exe"
if defined PY exit /b 0
if defined GEOLENS_PYTHON if exist "%GEOLENS_PYTHON%" set "PY=%GEOLENS_PYTHON%"
if defined PY exit /b 0
if exist "%USERPROFILE%\.geolens\python.txt" for /f "usebackq delims=" %%L in ("%USERPROFILE%\.geolens\python.txt") do if not defined PY if exist "%%~L" set "PY=%%~L"
if defined PY exit /b 0
for %%B in ("%USERPROFILE%\miniforge3" "%USERPROFILE%\mambaforge" "%USERPROFILE%\miniconda3" "%USERPROFILE%\anaconda3" "%LOCALAPPDATA%\miniforge3" "%LOCALAPPDATA%\miniconda3" "%LOCALAPPDATA%\anaconda3" "C:\ProgramData\miniforge3" "C:\ProgramData\miniconda3" "C:\ProgramData\anaconda3") do if not defined PY if exist "%%~B\envs\geolens\python.exe" set "PY=%%~B\envs\geolens\python.exe"
if not defined PY if exist "%USERPROFILE%\.conda\envs\geolens\python.exe" set "PY=%USERPROFILE%\.conda\envs\geolens\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\ESRI\conda\envs\geolens\python.exe" set "PY=%LOCALAPPDATA%\ESRI\conda\envs\geolens\python.exe"
exit /b 0
