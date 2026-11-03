# Publish a new version:  powershell -File release.ps1 1.2.0
# Creates branch release/1.2.0 from the current commit and pushes it. GitHub Actions builds the
# installer and publishes release v1.2.0; installed apps pick it up at next start (or within 6 hours).
param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw "Version must look like 1.2.0" }
if (git status --porcelain) { throw "You have uncommitted changes - commit them first" }
$branch = "release/$Version"
if (git ls-remote --heads origin $branch) { throw "$branch already exists on GitHub" }

# keep version.py in the repo in step (the build stamps it from the branch name anyway)
(Get-Content version.py) -replace '__version__ = ".*"', "__version__ = `"$Version`"" | Set-Content version.py
git add version.py
git commit -m "Release v$Version"
git push origin HEAD
git push origin "HEAD:refs/heads/$branch"
Write-Host "`nPushed $branch. Build progress: https://github.com/AngeloMalada/yugioh-vinted-lister/actions"
