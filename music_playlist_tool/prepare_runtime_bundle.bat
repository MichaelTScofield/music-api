@echo off
setlocal

set "TOOL_DIR=%~dp0"
for %%I in ("%TOOL_DIR%..") do set "PROJECT_ROOT=%%~fI"
set "TARGET=%PROJECT_ROOT%\runtime\music-api"

echo 准备 runtime\music-api ...
if exist "%TARGET%" rmdir /s /q "%TARGET%"
mkdir "%TARGET%"

copy "%PROJECT_ROOT%\app.js" "%TARGET%\" >nul
copy "%PROJECT_ROOT%\index.js" "%TARGET%\" >nul
copy "%PROJECT_ROOT%\package.json" "%TARGET%\" >nul

xcopy "%PROJECT_ROOT%\plugins" "%TARGET%\plugins" /E /I /Y >nul
xcopy "%PROJECT_ROOT%\public" "%TARGET%\public" /E /I /Y >nul
xcopy "%PROJECT_ROOT%\routes" "%TARGET%\routes" /E /I /Y >nul
xcopy "%PROJECT_ROOT%\server" "%TARGET%\server" /E /I /Y >nul
xcopy "%PROJECT_ROOT%\utils" "%TARGET%\utils" /E /I /Y >nul

if exist "%PROJECT_ROOT%\node_modules" (
  xcopy "%PROJECT_ROOT%\node_modules" "%TARGET%\node_modules" /E /I /Y >nul
)

echo 已生成：%TARGET%
echo 如需彻底脱离本机 Node，请再放入 runtime\node\node.exe

endlocal
