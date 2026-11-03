# Publish a new version:  powershell -File release.ps1 1.2.0
# Bumps version.py, commits, tags v1.2.0 and pushes. GitHub Actions then builds the installer and
# creates the release; installed apps pick it up within a few hours (or on next start).
param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must look like 1.2.0" }
if (git tag --list "v$Version") { throw "Tag v$Version already exists" }

(Get-Content version.py) -replace '__version__ = ".*"', "__version__ = `"$Version`"" | Set-Content version.py
git add -A
git commit -m "Release v$Version"
git tag "v$Version"
git push origin HEAD
git push origin "v$Version"
Write-Host "`nPushed v$Version. Build progress: https://github.com/AngeloMalada/yugioh-vinted-lister/actions"
