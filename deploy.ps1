# Club 8 - one-click deploy (PowerShell)
#
# Usage:
#   Right-click > Run with PowerShell, or from a terminal:
#     ./deploy.ps1                 # prompts for a message
#     ./deploy.ps1 "Fixed the QR"  # uses that message
#
# Pushes local changes to GitHub; Vercel and Render redeploy automatically.

param([string]$Message)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

git rev-parse --is-inside-work-tree *> $null
if ($LASTEXITCODE -ne 0) {
  Write-Host "ERROR: not a git repository. Run this from the project folder." -ForegroundColor Red
  exit 1
}

Write-Host "`n==== Club 8 deploy ====`n" -ForegroundColor Cyan
Write-Host "Changes to be deployed:"
git status --short
Write-Host ""

if (-not $Message) {
  $Message = Read-Host "Describe this update (or press Enter for a default)"
}
if (-not $Message) {
  $Message = "Site update " + (Get-Date -Format "yyyy-MM-dd_HH:mm")
}

git add -A

# Nothing staged? stop cleanly.
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
  Write-Host "`nNothing new to deploy. You're already up to date.`n" -ForegroundColor Yellow
  exit 0
}

Write-Host "Committing: $Message"
git -c user.email="devrajgussar80@gmail.com" -c user.name="devrajgussar80-commits" commit -m $Message
if ($LASTEXITCODE -ne 0) { Write-Host "commit failed." -ForegroundColor Red; exit 1 }

Write-Host "`nPushing to GitHub..."
git push
if ($LASTEXITCODE -ne 0) {
  Write-Host "push failed. If it mentions authentication, run: gh auth login" -ForegroundColor Red
  exit 1
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host " Done. Vercel and Render will redeploy automatically." -ForegroundColor Green
Write-Host "============================================================`n" -ForegroundColor Green
