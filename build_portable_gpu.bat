@echo off
call "%~dp0build_portable.bat" -RuntimeProfile CUDA
exit /b %ERRORLEVEL%
