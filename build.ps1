# Builds the Windows app (dist\YugiohVintedLister) and, if Inno Setup is installed, the installer (installer\).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

.\.venv\Scripts\pyinstaller app.py `
  --name YugiohVintedLister `
  --windowed `
  --icon assets\icon.ico `
  --add-data "ui;ui" `
  --add-data "assets\icon.ico;assets" `
  --collect-all rapidocr `
  --collect-all playwright `
  --collect-all pillow_heif `
  --collect-binaries onnxruntime `
  --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
  $version = (Select-String -Path version.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
  & $iscc "/DAppVersion=$version" installer.iss
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
  Write-Host "`nInstaller: $PSScriptRoot\installer\YugiohVintedLister-Setup.exe"
} else {
  Write-Host "`nInno Setup not found - app built in dist\YugiohVintedLister (no installer)."
}
