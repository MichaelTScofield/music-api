@echo off
setlocal

set "TOOL_DIR=%~dp0"
for %%I in ("%TOOL_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "APP_NAME=MusicPlaylistGui"
set "ENTRY=%TOOL_DIR%gui_app.py"
set "DIST_DIR=%TOOL_DIR%dist"
set "BUILD_DIR=%TOOL_DIR%build"
set "SPEC_DIR=%BUILD_DIR%\spec"
set "EXE_PATH=%DIST_DIR%\%APP_NAME%.exe"

if not exist "%ENTRY%" (
  echo Entry script not found: %ENTRY%
  exit /b 1
)
if not exist "%TOOL_DIR%auto.py" (
  echo Workflow script not found: %TOOL_DIR%auto.py
  exit /b 1
)
if not exist "%TOOL_DIR%qq-auto.py" (
  echo QQ workflow script not found: %TOOL_DIR%qq-auto.py
  exit /b 1
)

echo [1/5] Prepare runtime bundle...
call "%TOOL_DIR%prepare_runtime_bundle.bat"
if errorlevel 1 exit /b 1

echo [2/5] Clean old build output...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
mkdir "%SPEC_DIR%"

echo [3/5] Build one-file exe...
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
  --add-data "%TOOL_DIR%auto.py;." ^
  --add-data "%TOOL_DIR%qq-auto.py;." ^
  --add-data "%PROJECT_ROOT%\runtime;runtime" ^
  --hidden-import pystray ^
  --hidden-import pystray._win32 ^
  --hidden-import PIL.Image ^
  --hidden-import PIL.ImageDraw ^
  --hidden-import tkinter.filedialog ^
  --hidden-import tkinter.messagebox ^
  --hidden-import tkinter.ttk ^
  --hidden-import uuid ^
  --collect-submodules mutagen ^
  --collect-submodules opencc ^
  "%ENTRY%"

if errorlevel 1 (
  echo Build failed.
  exit /b 1
)

echo [4/5] Keep only exe artifact...
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

echo [5/5] Done.
echo Output: %EXE_PATH%
endlocal
