@echo off
title Football Tracker Bot - Update
echo ========================================
echo  Football Tracker Bot - Remote Update
echo ========================================
echo.
echo Connecting to raspberry.local...
echo.

ssh lucac@raspberry.local "cd ~/football_tracker_bot && bash update.sh && echo && sudo systemctl status marco_van_botten --no-pager"

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================
    echo  Done! Bot updated and restarted.
    echo ========================================
) else (
    echo.
    echo ========================================
    echo  ERROR: Something went wrong.
    echo  Check the output above for details.
    echo ========================================
)

echo.
pause
