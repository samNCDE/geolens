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
