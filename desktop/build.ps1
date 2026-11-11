# Construit l'application de bureau : dist\DiscordTranscript\DiscordTranscript.exe
# Usage (depuis la racine du projet) :  powershell -ExecutionPolicy Bypass -File desktop\build.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv-win\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Environnement .venv-win introuvable (voir README)." }

if (-not (Test-Path "bot\node_modules")) {
    Write-Host "Installation des dépendances du bot Discord..."
    Push-Location bot; npm install --omit=dev; Pop-Location
}

& $python -m pip install --quiet -r requirements-dev.txt -r desktop\requirements-desktop.txt

# Pas d'application construite si un test échoue (les tests lents se lancent à part : python -m pytest)
& $python -m pytest --skip-slow -q
if ($LASTEXITCODE -ne 0) { throw "Tests en échec : construction annulée." }

& $python -m PyInstaller desktop\DiscordTranscript.spec --noconfirm --clean --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) { throw "La construction a échoué." }

Write-Host ""
Write-Host "Application prête : $root\dist\DiscordTranscript\DiscordTranscript.exe"

# Installeur (Inno Setup 6, facultatif) : dist\DiscordTranscript-Setup-<version>.exe
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
          "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    Write-Host "Inno Setup introuvable : pas d'installeur (winget install JRSoftware.InnoSetup)."
    exit 0
}
$version = & $python -c "import app; print(app.__version__)"
& $iscc /Q "/DAppVersion=$version" desktop\installer.iss
if ($LASTEXITCODE -ne 0) { throw "La création de l'installeur a échoué." }
Write-Host "Installeur prêt : $root\dist\DiscordTranscript-Setup-$version.exe"
