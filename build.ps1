$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot

$LocalPython = Join-Path $ProjectRoot '.python313\python.exe'
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $VenvPython) {
    $Python = $VenvPython
} elseif (Test-Path -LiteralPath $LocalPython) {
    $Python = $LocalPython
} else {
    $Python = & py -3.13 -c 'import sys; print(sys.executable)'
    if ($LASTEXITCODE -ne 0 -or -not $Python) {
        throw 'Python 3.13 is required. Create .venv as described in README.md.'
    }
}

& $Python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $Python -m unittest discover -s tests -t . -v
if ($LASTEXITCODE -ne 0) { throw 'Unit tests failed.' }
& $Python -m PyInstaller --clean --noconfirm YogaFanG8.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$Executable = Join-Path $ProjectRoot 'dist\YogaFanG8\YogaFanG8.exe'
$Distribution = Split-Path -Parent $Executable
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'LICENSE'),(Join-Path $ProjectRoot 'README.md') -Destination $Distribution -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot 'docs') -Destination $Distribution -Recurse -Force
$SelfTest = Start-Process -FilePath $Executable -ArgumentList '--mock-self-test' -Wait -PassThru -WindowStyle Hidden
if ($SelfTest.ExitCode -ne 0) {
    throw "Packaged mock self-test failed with exit code $($SelfTest.ExitCode)."
}

Write-Host "Build and packaged mock self-test complete: $Executable"
Write-Host "Distribute the complete dist\YogaFanG8 folder (not the EXE alone)."
