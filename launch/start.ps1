# One-click start for Windows (run by Start.bat). Same steps as bootstrap.sh:
# uv → Node (checksum-verified, kept in .tools\node) → packages (when lockfiles
# change) → UI build (when UI code changes) → start both servers → open browser.
# NOTE: written to mirror the macOS script but not yet tested on a Windows PC.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Tools = Join-Path $Root ".tools"
$Logs = Join-Path $Tools "logs"
$NodeVersion = "v22.12.0"
$Url = "http://localhost:8766"
New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$env:Path = "$Tools\node;$env:USERPROFILE\.local\bin;$env:Path"

function Say($m) { Write-Host "`n* $m" -ForegroundColor Magenta }
function Fail($m) { Write-Host "`nX $m" -ForegroundColor Red; Write-Host "  Details are in $Logs"; exit 1 }
function PortBusy($p) { try { (New-Object Net.Sockets.TcpClient("127.0.0.1", $p)).Close(); $true } catch { $false } }
function Hash($files) {
  $sha = [Security.Cryptography.SHA256]::Create()
  $bytes = foreach ($f in $files) { if (Test-Path $f) { [IO.File]::ReadAllBytes($f) } }
  ($sha.ComputeHash([byte[]]($bytes | ForEach-Object { $_ })) | ForEach-Object { $_.ToString("x2") }) -join ""
}

if ((PortBusy 8765) -and (PortBusy 8766)) { Say "The app is already running - opening it."; Start-Process $Url; exit 0 }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Say "Installing uv (Python tools) - one time only..."
  try { powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex" *> "$Logs\uv-install.log" }
  catch { Fail "Couldn't install uv. Check your internet connection and run Start again." }
}

$nodeOk = $false
if (Get-Command node -ErrorAction SilentlyContinue) { $nodeOk = [int]((node -v).TrimStart("v").Split(".")[0]) -ge 20 }
if (-not $nodeOk) {
  $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
  $name = "node-$NodeVersion-win-$arch"
  Say "Downloading Node.js $NodeVersion - one time only..."
  $tmp = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "jp-node")
  $base = "https://nodejs.org/dist/$NodeVersion"
  try {
    Invoke-WebRequest "$base/$name.zip" -OutFile "$tmp\$name.zip"
    Invoke-WebRequest "$base/SHASUMS256.txt" -OutFile "$tmp\SHASUMS256.txt"
  } catch { Fail "Couldn't download Node.js. Check your internet connection and run Start again." }
  $expected = (Select-String -Path "$tmp\SHASUMS256.txt" -Pattern " $name.zip$").Line.Split(" ")[0]
  $actual = (Get-FileHash "$tmp\$name.zip" -Algorithm SHA256).Hash.ToLower()
  if ($expected -ne $actual) { Fail "Node.js download didn't match its checksum - not installing it." }
  Expand-Archive "$tmp\$name.zip" -DestinationPath $tmp -Force
  Remove-Item -Recurse -Force "$Tools\node" -ErrorAction SilentlyContinue
  Move-Item "$tmp\$name" "$Tools\node"
}

$deps = Hash @("uv.lock", "pyproject.toml", "ui\package-lock.json", "job_pipeline\resume\node\package-lock.json")
$depsFile = "$Tools\.deps"
if (-not (Test-Path ".venv") -or -not (Test-Path "ui\node_modules") -or ((Get-Content $depsFile -ErrorAction SilentlyContinue) -ne $deps)) {
  Say "Installing app packages - this takes a few minutes the first time..."
  uv sync *> "$Logs\uv-sync.log"; if ($LASTEXITCODE) { Fail "Couldn't install the Python packages." }
  Push-Location ui; npm ci --no-audit --no-fund *> "$Logs\npm-ui.log"; $c = $LASTEXITCODE; Pop-Location
  if ($c) { Fail "Couldn't install the web UI packages." }
  Push-Location job_pipeline\resume\node; npm ci --no-audit --no-fund *> "$Logs\npm-resume.log"; $c = $LASTEXITCODE; Pop-Location
  if ($c) { Fail "Couldn't install the resume tools." }
  Set-Content $depsFile $deps
  Remove-Item "$Tools\.ui-build" -ErrorAction SilentlyContinue
}

$uiFiles = Get-ChildItem -Recurse -File ui\app, ui\components, ui\lib | Sort-Object FullName | ForEach-Object { $_.FullName }
$ui = Hash ($uiFiles + @("ui\package.json"))
if (-not (Test-Path "ui\.next\BUILD_ID") -or ((Get-Content "$Tools\.ui-build" -ErrorAction SilentlyContinue) -ne $ui)) {
  Say "Preparing the web interface..."
  Push-Location ui; npm run build *> "$Logs\ui-build.log"; $c = $LASTEXITCODE; Pop-Location
  if ($c) { Fail "Couldn't build the web interface." }
  Set-Content "$Tools\.ui-build" $ui
}

Say "Starting the app..."
$backend = Start-Process uv -ArgumentList "run", "python", "-m", "job_pipeline.cli", "serve", "--port", "8765" `
  -RedirectStandardOutput "$Logs\backend.log" -RedirectStandardError "$Logs\backend.err" -PassThru -WindowStyle Hidden
$frontend = Start-Process npm.cmd -ArgumentList "run", "start" -WorkingDirectory (Join-Path $Root "ui") `
  -RedirectStandardOutput "$Logs\ui.log" -RedirectStandardError "$Logs\ui.err" -PassThru -WindowStyle Hidden
try {
  for ($i = 0; $i -lt 90 -and -not ((PortBusy 8765) -and (PortBusy 8766)); $i++) { Start-Sleep 1 }
  if (-not (PortBusy 8766)) { Fail "The app took too long to start." }
  Start-Process $Url
  Write-Host "`nJob Pipeline is running at $Url" -ForegroundColor Green
  Read-Host "Leave this window open while you use the app. Press Enter to stop"
} finally {
  foreach ($p in @($backend, $frontend)) { if ($p) { taskkill /PID $p.Id /T /F *> $null } }
}
