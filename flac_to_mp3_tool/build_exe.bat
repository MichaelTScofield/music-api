@echo off
setlocal

set "TOOL_DIR=%~dp0"
for %%I in ("%TOOL_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "APP_NAME=FlacToMp3Gui"
set "ENTRY=%TOOL_DIR%flac_to_mp3_gui.py"
set "DIST_DIR=%TOOL_DIR%dist"
set "BUILD_DIR=%TOOL_DIR%build"
set "SPEC_DIR=%BUILD_DIR%\spec"
set "EXE_PATH=%DIST_DIR%\%APP_NAME%.exe"

if not exist "%ENTRY%" (
  echo Entry script not found: %ENTRY%
  exit /b 1
)

echo [1/3] Clean old build output...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
mkdir "%SPEC_DIR%"

echo [2/3] Build one-file exe...
pyinstaller ^
  --noconfirm ^
  --clean ^
  --onefile ^
  --windowed ^
  --name "%APP_NAME%" ^
  --distpath "%DIST_DIR%" ^
  --workpath "%BUILD_DIR%" ^
  --specpath "%SPEC_DIR%" ^
  --paths "%PROJECT_ROOT%" ^
  --hidden-import tkinter.filedialog ^
  --hidden-import tkinter.messagebox ^
  --hidden-import tkinter.ttk ^
  --collect-submodules mutagen ^
  "%ENTRY%"

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo [3/3] Keep only exe artifact...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
for /f "delims=" %%I in ('dir /b "%DIST_DIR%"') do (
  if /I not "%%I"=="%APP_NAME%.exe" (
    if exist "%DIST_DIR%\%%I\" (
      rmdir /s /q "%DIST_DIR%\%%I"
    ) else (
      del /f /q "%DIST_DIR%\%%I"
    )
  )
)

echo Done.
echo Output: %EXE_PATH%
endlocal
