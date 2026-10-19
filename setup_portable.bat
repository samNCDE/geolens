@echo off
rem ===========================================================================
rem  geolens, portable: build everything on THIS drive, nothing on the computer
rem
rem  Double-click this file in the geolens folder on an external drive. It
rem  builds geolens's own Python environment in "env" beside it, from
rem  conda-forge, using micromamba (one downloaded program, no installer):
rem    - nothing is installed into Windows, the registry or your user profile;
rem    - downloads, caches and temporary files stay on this drive;
rem    - the drive letter may change: everything is found relative to here.
rem
rem  Afterwards: ArcGIS Pro > Catalog > Toolboxes > Add Toolbox >
rem  arcgis_toolbox\geolens.pyt in this folder. It uses this env by itself.
rem  Needs internet once, and about 3 GB on this drive.
rem ===========================================================================
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PORT=%ROOT%\.portable"
set "MAMBA=%PORT%\micromamba.exe"
set "TMP=%ROOT%\tmp"
set "TEMP=%ROOT%\tmp"
set "PIP_CACHE_DIR=%PORT%\pip"
set "PYTHONNOUSERSITE=1"
set "MAMBA_ROOT_PREFIX=%PORT%\mamba"
set "CONDA_PKGS_DIRS=%PORT%\mamba\pkgs"
if not exist "%PORT%" mkdir "%PORT%"
if not exist "%TMP%" mkdir "%TMP%"

echo.
echo  geolens portable setup in %ROOT%
echo.

if exist "%ROOT%\env\python.exe" (
  echo  [1/4] env already exists: updating packages in place
) else (
  echo  [1/4] building env
)

if not exist "%MAMBA%" (
  echo        downloading micromamba ...
  curl.exe -L --fail --retry 3 -o "%MAMBA%" "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64"
  if errorlevel 1 goto :no_micromamba
)

echo  [2/4] conda-forge packages (5-15 minutes the first time)
"%MAMBA%" create -y -p "%ROOT%\env" -r "%PORT%\mamba" -c conda-forge --override-channels ^
  python=3.11 pip "geopandas>=1.0" "rasterio>=1.3" "shapely>=2.0" "pyproj>=3.4" ^
  "fiona>=1.9" "pandas>=2.0" "matplotlib-base>=3.7" "tqdm>=4.65" ^
  "scikit-image>=0.21" "scipy>=1.10" "psutil>=5.9" "numba>=0.57,<0.62" ^
  jupyterlab ipykernel
if errorlevel 1 goto :failed

echo  [3/4] pip packages
"%ROOT%\env\python.exe" -m pip install --no-deps --disable-pip-version-check "pysheds>=0.4" geojson looseversion affine
if errorlevel 1 goto :failed
"%ROOT%\env\python.exe" -m pip install --disable-pip-version-check "topotoolbox>=0.0.10"
if errorlevel 1 echo        (topotoolbox did not install: only the headcuts tool needs it)

echo  [4/4] testing on a synthetic valley
set "PYTHONPATH=%ROOT%\src"
set "MPLCONFIGDIR=%ROOT%\home\matplotlib"
set "NUMBA_CACHE_DIR=%ROOT%\home\numba"
"%ROOT%\env\python.exe" "%ROOT%\install.py" --smoke
if errorlevel 1 goto :failed

rem The package cache is only needed to build: free the space.
if exist "%PORT%\mamba\pkgs" rmdir /s /q "%PORT%\mamba\pkgs"

echo.
echo  ======================================================================
echo   geolens is ready on this drive: %ROOT%\env
echo   ArcGIS Pro: Catalog ^> Toolboxes ^> Add Toolbox ^> %ROOT%\arcgis_toolbox\geolens.pyt
echo   Notebook:   double-click start_notebook_portable.bat
echo  ======================================================================
echo.
pause
exit /b 0

:no_micromamba
echo.
echo  Could not download micromamba. Check the internet connection, or put
echo  micromamba.exe in %PORT% by hand and run this again.
echo.
pause
exit /b 1

:failed
echo.
echo  Setup stopped with an error (above). Nothing was installed on this
echo  computer; delete "%ROOT%\env" and run this again, or send the window's
echo  text to whoever maintains geolens.
echo.
pause
exit /b 1
