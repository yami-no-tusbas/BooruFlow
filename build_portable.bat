@echo off
setlocal
pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
    echo BUILD FAILED: unable to open the project directory.
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_portable.ps1" %*
set "BUILD_EXIT_CODE=%ERRORLEVEL%"

echo.
if "%BUILD_EXIT_CODE%"=="0" (
    echo The portable build finished successfully.
) else (
    echo The portable build failed with exit code %BUILD_EXIT_CODE%.
)
echo You can close this window after reviewing the messages above.
pause

popd >nul 2>&1
exit /b %BUILD_EXIT_CODE%
