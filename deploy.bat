@echo off
REM ============================================================
REM  Club 8 - one-click deploy
REM  Double-click this file to push your local changes to GitHub.
REM  Vercel (frontend) and Render (backend) then redeploy on their
REM  own. Nothing else to do.
REM ============================================================
setlocal
cd /d "%~dp0"

echo.
echo ==== Club 8 deploy ====
echo.

REM --- make sure this is a git repo ---
git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
  echo ERROR: This folder is not a git repository.
  echo Run this only from your project folder.
  pause
  exit /b 1
)

REM --- show what will be sent ---
echo Changes to be deployed:
git status --short
echo.

REM --- ask for a short message (optional) ---
set "MSG="
set /p "MSG=Describe this update (or press Enter for a default): "

if "%MSG%"=="" (
  for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH:mm"') do set "STAMP=%%i"
  set "MSG=Site update %STAMP%"
)

echo.
echo Staging all changes...
git add -A

REM --- nothing to commit? stop cleanly ---
git diff --cached --quiet
if not errorlevel 1 (
  echo.
  echo Nothing new to deploy. You're already up to date.
  echo.
  pause
  exit /b 0
)

echo Committing: %MSG%
git -c user.email="devrajgussar80@gmail.com" -c user.name="devrajgussar80-commits" commit -m "%MSG%"
if errorlevel 1 (
  echo.
  echo ERROR: commit failed. See the message above.
  pause
  exit /b 1
)

echo.
echo Pushing to GitHub...
git push
if errorlevel 1 (
  echo.
  echo ERROR: push failed. If it mentions authentication, run:  gh auth login
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  Done. GitHub updated.
echo  Vercel and Render will redeploy automatically in a minute.
echo ============================================================
echo.
pause
