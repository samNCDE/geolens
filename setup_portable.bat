@echo off
rem ===========================================================================
rem  geolens, portable: build everything on THIS drive, nothing on the computer
rem
rem  Double-click this file in the geolens folder (an external drive works).
rem  It builds geolens's own Python environment in "env" beside it, from
rem  conda-forge, using micromamba (one downloaded program, no installer):
rem    - no admin rights, and no Python needed on the computer beforehand;
rem    - nothing is installed into Windows, the registry or your user profile;
rem    - downloads, caches and temporary files stay on this drive;
rem    - the drive letter may change: everything is found relative to here.
rem  Run it again to update the packages of an "env" that is already here.
rem  install_windows.bat and run_project.bat call it when a computer has no
rem  Python of its own.
rem
rem  Afterwards: drag a boundary onto run_project.bat, or ArcGIS Pro >
rem  Catalog > Toolboxes > Add Toolbox > arcgis_toolbox\geolens.pyt in this
rem  folder (it uses this env by itself). Needs internet once, and about 3 GB
rem  on this drive.
rem ===========================================================================
setlocal EnableExtensions
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "PORT=%ROOT%\.portable"
set "MAMBA=%PORT%\micromamba.exe"
set "MM_URL=https://github.com/mamba-org/micromamba-releases/releases/download/2.9.0-0/micromamba-win-64"
set "MM_URL2=https://micro.mamba.pm/api/micromamba/win-64/latest"
set "TMP=%ROOT%\tmp"
set "TEMP=%ROOT%\tmp"
set "PIP_CACHE_DIR=%PORT%\pip"
set "PYTHONNOUSERSITE=1"
set "MAMBA_ROOT_PREFIX=%PORT%\mamba"
rem micromamba lists the environments it makes, and reads its settings, in
rem the user profile: keep both on this drive. And no other program's Python,
rem GDAL or PROJ settings (ArcGIS Pro's or QGIS's, say) in this window.
set "USERPROFILE=%ROOT%\home"
set "HOME=%ROOT%\home"
set "LOCALAPPDATA=%ROOT%\home\cache"
for %%V in (PYTHONHOME PYTHONPATH PYTHONSTARTUP PYTHONUSERBASE GDAL_DATA GDAL_DRIVER_PATH PROJ_LIB PROJ_DATA GEOTIFF_CSV QT_PLUGIN_PATH CONDA_PREFIX CONDA_DEFAULT_ENV CONDA_SHLVL CONDA_PROMPT_MODIFIER CONDA_PYTHON_EXE CONDA_EXE) do set "%%V="
if not exist "%PORT%" mkdir "%PORT%"
if not exist "%TMP%" mkdir "%TMP%"
if not exist "%ROOT%\home\cache" mkdir "%ROOT%\home\cache"

echo.
echo  geolens portable setup in %ROOT%
echo.

rem Windows allows 260 characters in a path, and the environment's deepest
rem files sit about 160 characters below "env": a longer folder path than
rem 90 characters would pass the limit part way through the setup.
if not "%ROOT:~90,1%"=="" goto :too_deep

rem micromamba 2.9 unpacks each package under
rem   (package cache)\https\conda.anaconda.org\conda-forge\(subdir)\(package)\
rem before linking it, and some packages (debugpy, jupyterlab) hold files 180
rem characters deep, which passes the 260-character limit under this folder
rem ("critical libmamba Package cache error", found 23 Oct 2026). So the
rem cache goes in a short folder at the top of this drive, else of the
rem system drive, and is removed when the setup has finished. An older
rem setup's cache under .portable is removed too: it is not used any more.
set "PKGS=%~d0\geolens_pkgs"
if not exist "%PKGS%\" mkdir "%PKGS%" >nul 2>&1
if not exist "%PKGS%\" set "PKGS=%SystemDrive%\geolens_pkgs"
if not exist "%PKGS%\" mkdir "%PKGS%" >nul 2>&1
if not exist "%PKGS%\" set "PKGS=%PORT%\mamba\pkgs"
set "CONDA_PKGS_DIRS=%PKGS%"
if /i not "%PKGS%"=="%PORT%\mamba\pkgs" if exist "%PORT%\mamba\pkgs\" rmdir /s /q "\\?\%PORT%\mamba\pkgs" >nul 2>&1
echo        package cache: %PKGS%

rem An "env" an earlier setup left unfinished (no python.exe, or no record of
rem a finished install in conda-meta\history) is removed and built again.
set "INCOMPLETE="
if exist "%ROOT%\env\" if not exist "%ROOT%\env\python.exe" set "INCOMPLETE=1"
if exist "%ROOT%\env\python.exe" if not exist "%ROOT%\env\conda-meta\history" set "INCOMPLETE=1"
if exist "%ROOT%\env\conda-meta\history" for %%H in ("%ROOT%\env\conda-meta\history") do if %%~zH EQU 0 set "INCOMPLETE=1"
if defined INCOMPLETE call :remove_incomplete
if exist "%ROOT%\env\python.exe" (
  set "ACTION=install"
  echo  [1/4] env already exists: updating its packages in place
) else (
  set "ACTION=create"
  echo  [1/4] building env
)

rem micromamba: kept from an earlier setup if it still runs, else downloaded
if exist "%MAMBA%" "%MAMBA%" --version >nul 2>&1
if exist "%MAMBA%" if errorlevel 1 del /q "%MAMBA%" >nul 2>&1
if not exist "%MAMBA%" call :get_micromamba
if not exist "%MAMBA%" goto :no_micromamba

echo  [2/4] conda-forge packages (5-15 minutes the first time)
"%MAMBA%" %ACTION% -y -p "%ROOT%\env" -r "%PORT%\mamba" -c conda-forge --override-channels ^
  python=3.11 pip "geopandas>=1.0" "rasterio>=1.3" "shapely>=2.0" "pyproj>=3.4" ^
  "fiona>=1.9" "pandas>=2.0" "matplotlib-base>=3.7" "tqdm>=4.65" ^
  "scikit-image>=0.21" "scipy>=1.10" "psutil>=5.9" "numba>=0.57,<0.62" ^
  jupyterlab ipykernel
if errorlevel 1 goto :failed
if not exist "%ROOT%\env\python.exe" goto :failed

rem the environment's own DLLs first, and its own GDAL and PROJ data, as
rem conda activate would set them
set "ENVDIR=%ROOT%\env"
set "PATH=%ENVDIR%;%ENVDIR%\Library\mingw-w64\bin;%ENVDIR%\Library\usr\bin;%ENVDIR%\Library\bin;%ENVDIR%\Scripts;%ENVDIR%\bin;%PATH%"
if exist "%ENVDIR%\Library\share\gdal\" set "GDAL_DATA=%ENVDIR%\Library\share\gdal"
if exist "%ENVDIR%\Library\share\proj\" set "PROJ_DATA=%ENVDIR%\Library\share\proj"
if exist "%ENVDIR%\Library\share\proj\" set "PROJ_LIB=%ENVDIR%\Library\share\proj"

echo  [3/4] pip packages
"%ROOT%\env\python.exe" -m pip install --no-deps --disable-pip-version-check --no-warn-script-location "pysheds>=0.4" geojson looseversion affine
if errorlevel 1 goto :failed
"%ROOT%\env\python.exe" -m pip install --disable-pip-version-check --no-warn-script-location "topotoolbox>=0.0.10"
if errorlevel 1 echo        (topotoolbox did not install: only the headcuts tool needs it)

echo  [4/4] testing on a synthetic valley
set "PYTHONPATH=%ROOT%\src"
set "MPLCONFIGDIR=%ROOT%\home\matplotlib"
set "NUMBA_CACHE_DIR=%ROOT%\home\numba"
"%ROOT%\env\python.exe" "%ROOT%\install.py" --smoke
if errorlevel 1 goto :failed

rem The package cache is only needed to build: free the space.
if exist "%PKGS%\" rmdir /s /q "\\?\%PKGS%" >nul 2>&1

echo.
echo  ======================================================================
echo   geolens is ready on this drive: %ROOT%\env
echo   A project:  drag a boundary file onto run_project.bat
echo   ArcGIS Pro: Catalog ^> Toolboxes ^> Add Toolbox ^> %ROOT%\arcgis_toolbox\geolens.pyt
echo   Notebook:   double-click start_notebook_portable.bat
echo  ======================================================================
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 0

:get_micromamba
rem Windows's own curl.exe first (Windows 10 and 11), then PowerShell, which
rem also uses a proxy set in Windows; the GitHub release, then micro.mamba.pm.
echo        downloading micromamba ...
if exist "%MAMBA%.part" del /q "%MAMBA%.part" >nul 2>&1
set "CURL=%SystemRoot%\System32\curl.exe"
if not exist "%CURL%" goto :get_with_powershell
"%CURL%" -L --fail --retry 3 --retry-delay 5 -o "%MAMBA%.part" "%MM_URL%"
if not errorlevel 1 goto :got_micromamba
echo        curl could not download it; trying PowerShell ...
:get_with_powershell
set "PS_GET=try { $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $env:GET_URL -OutFile $env:GET_TO; exit 0 } catch { Write-Host ('        ' + $_.Exception.Message); exit 1 }"
set "GET_URL=%MM_URL%"
set "GET_TO=%MAMBA%.part"
powershell -NoProfile -ExecutionPolicy Bypass -Command "%PS_GET%"
if not errorlevel 1 goto :got_micromamba
echo        trying micro.mamba.pm ...
if exist "%PORT%\mm" rmdir /s /q "%PORT%\mm"
mkdir "%PORT%\mm"
set "GET_URL=%MM_URL2%"
set "GET_TO=%PORT%\mm\micromamba.tar.bz2"
powershell -NoProfile -ExecutionPolicy Bypass -Command "%PS_GET%"
if errorlevel 1 exit /b 1
"%SystemRoot%\System32\tar.exe" -xf "%PORT%\mm\micromamba.tar.bz2" -C "%PORT%\mm"
if errorlevel 1 exit /b 1
if not exist "%PORT%\mm\Library\bin\micromamba.exe" exit /b 1
move /y "%PORT%\mm\Library\bin\micromamba.exe" "%MAMBA%.part" >nul
:got_micromamba
if not exist "%MAMBA%.part" exit /b 1
move /y "%MAMBA%.part" "%MAMBA%" >nul
"%MAMBA%" --version
if errorlevel 1 (
  echo        the downloaded micromamba does not run
  del /q "%MAMBA%" >nul 2>&1
  exit /b 1
)
exit /b 0

:remove_incomplete
echo  [1/4] "env" was left unfinished by an earlier setup: removing it to build
echo        it again
rmdir /s /q "\\?\%ROOT%\env" >nul 2>&1
exit /b 0

:too_deep
echo.
echo  This folder's path is too long for geolens's own Python:
echo    %ROOT%
echo  Windows allows 260 characters in a path, and some of the packages have
echo  files about 160 characters deep. Move the geolens folder nearer the top
echo  of the drive ^(for example E:\geolens^) and run this again.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 1

:no_micromamba
echo.
echo  Could not download micromamba. Check the internet connection, or put
echo  micromamba.exe ^(from github.com/mamba-org/micromamba-releases^) in
echo  %PORT% by hand and run this again.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 1

:failed
echo.
echo  Setup stopped with an error (above). Nothing was installed on this
echo  computer. Run this again to carry on, or send the window's text to
echo  whoever maintains geolens.
echo.
if not defined GEOLENS_NO_PAUSE pause
exit /b 1
