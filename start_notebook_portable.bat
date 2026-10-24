@echo off
rem Start the geolens notebook from a portable release (run setup_portable.bat
rem first). Everything it writes stays on this drive.
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
if not exist "%ROOT%\env\python.exe" (
  echo Run setup_portable.bat first.
  pause
  exit /b 1
)
rem the environment's own DLLs first, and no other program's Python, GDAL or
rem PROJ settings (ArcGIS Pro's or QGIS's): the environment's own GDAL and
rem PROJ data instead
set "ENVDIR=%ROOT%\env"
set "PATH=%ENVDIR%;%ENVDIR%\Library\mingw-w64\bin;%ENVDIR%\Library\usr\bin;%ENVDIR%\Library\bin;%ENVDIR%\Scripts;%ENVDIR%\bin;%PATH%"
for %%V in (PYTHONHOME PYTHONSTARTUP PYTHONUSERBASE GDAL_DATA GDAL_DRIVER_PATH PROJ_LIB PROJ_DATA GEOTIFF_CSV QT_PLUGIN_PATH CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_SHLVL CONDA_PROMPT_MODIFIER CONDA_PYTHON_EXE CONDA_EXE) do set "%%V="
if exist "%ENVDIR%\Library\share\gdal\" set "GDAL_DATA=%ENVDIR%\Library\share\gdal"
if exist "%ENVDIR%\Library\share\proj\" set "PROJ_DATA=%ENVDIR%\Library\share\proj"
if exist "%ENVDIR%\Library\share\proj\" set "PROJ_LIB=%ENVDIR%\Library\share\proj"
set "PYTHONPATH=%ROOT%\src"
set "PYTHONNOUSERSITE=1"
set "TMP=%ROOT%\tmp"
set "TEMP=%ROOT%\tmp"
set "LOCALAPPDATA=%ROOT%\home\cache"
set "MPLCONFIGDIR=%ROOT%\home\matplotlib"
set "NUMBA_CACHE_DIR=%ROOT%\home\numba"
set "GEOLENS_HOME=%ROOT%\home"
set "GEOLENS_NETRC=%ROOT%\home\_netrc"
set "JUPYTER_CONFIG_DIR=%ROOT%\home\jupyter"
set "JUPYTER_DATA_DIR=%ROOT%\home\jupyter\data"
set "JUPYTER_RUNTIME_DIR=%ROOT%\home\jupyter\runtime"
set "IPYTHONDIR=%ROOT%\home\ipython"
if not exist "%TMP%" mkdir "%TMP%"
"%ROOT%\env\python.exe" -m jupyter lab --notebook-dir="%ROOT%\notebooks"
