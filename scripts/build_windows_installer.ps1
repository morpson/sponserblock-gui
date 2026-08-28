param(
    [string]$Python = "py -3.11",
    [string]$OutputDirectory = "dist\windows"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildRoot = Join-Path $ProjectRoot "build\windows"
$Stage = Join-Path $BuildRoot "app"
$Venv = Join-Path $Stage ".venv"
$Output = Join-Path $ProjectRoot $OutputDirectory

if (Test-Path $BuildRoot) {
    Remove-Item $BuildRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $Stage, $Output -Force | Out-Null

& cmd /c "$Python -m venv `"$Venv`""
if ($LASTEXITCODE -ne 0) {
    throw "Unable to create the Windows virtual environment."
}

$VenvPython = Join-Path $Venv "Scripts\python.exe"
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt")
& $VenvPython -m pip install --no-deps -e $ProjectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Unable to install iSponsorBlockTV and its dependencies."
}

Copy-Item (Join-Path $ProjectRoot "scripts") (Join-Path $Stage "scripts") -Recurse
Copy-Item (Join-Path $ProjectRoot "src") (Join-Path $Stage "src") -Recurse
Copy-Item (Join-Path $ProjectRoot "requirements.txt"), (Join-Path $ProjectRoot "config.json.template"),
    (Join-Path $ProjectRoot "LICENSE.md"), (Join-Path $ProjectRoot "README.md"),
    (Join-Path $ProjectRoot "pyproject.toml") -Destination $Stage

$Iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
if (-not $Iscc) {
    $Iscc = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
}
if (-not (Test-Path $Iscc)) {
    throw "Inno Setup 6 (ISCC.exe) is required to build the installer."
}

& $Iscc "/DSourceDir=$Stage" "/DOutputDir=$Output" (Join-Path $ProjectRoot "installer\iSponsorBlockTV.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed."
}
