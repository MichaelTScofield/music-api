@echo off
setlocal

set "TOOL_DIR=%~dp0"
for %%I in ("%TOOL_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "DIST_NAME=MusicPlaylistGui"
set "DIST_DIR=%TOOL_DIR%dist\%DIST_NAME%"
set "WORKFLOW_SCRIPT=%TOOL_DIR%auto.py"
set "QQ_WORKFLOW_SCRIPT=%TOOL_DIR%qq-auto.py"
set "GUI_SPEC=%TOOL_DIR%MusicPlaylistGui.spec"

if not exist "%WORKFLOW_SCRIPT%" (
  echo Workflow script not found.
  exit /b 1
)
if not exist "%QQ_WORKFLOW_SCRIPT%" (
  echo QQ workflow script not found.
  exit /b 1
)
if not exist "%GUI_SPEC%" (
  echo GUI spec not found.
  exit /b 1
)

echo [1/4] Prepare runtime bundle...
call "%TOOL_DIR%prepare_runtime_bundle.bat"
if errorlevel 1 (
  echo Runtime preparation failed.
  exit /b 1
)

echo [2/4] Clean old build output...
if exist "%TOOL_DIR%build" rmdir /s /q "%TOOL_DIR%build"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"

echo [3/4] Build GUI with PyInstaller...
pyinstaller ^
  --noconfirm ^
  --clean ^
  --distpath "%TOOL_DIR%dist" ^
  --workpath "%TOOL_DIR%build" ^
  "%GUI_SPEC%"

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo [4/4] Build finished.
echo Output: %DIST_DIR%
endlocal
