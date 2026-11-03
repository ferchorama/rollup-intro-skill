<#
.SYNOPSIS
  One-folder tool setup for the Rollup intro skill (Windows).

.DESCRIPTION
  Everything goes into ONE folder, by default <skill>\.rollup-intro-env
  (override with -EnvHome or the ROLLUP_INTRO_HOME environment variable):
    uv\        astral uv (only if uv is not already installed)
    python\    uv-managed CPython (no system Python needed or touched)
    cache\uv\  uv download cache
    venv\      the virtualenv with every Python dependency
    models\    Whisper large-v3-turbo (faster-whisper format, ~1.6 GB)
    hf\        HF_HOME for any other Hugging Face download
    tools\ffmpeg\bin\  portable ffmpeg (only if ffmpeg is not on PATH, or -Ffmpeg portable)
  Nothing outside that folder is modified: no PATH edits, no registry, no global pip.
  Delete the folder to uninstall.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File setup.ps1
  powershell -ExecutionPolicy Bypass -File setup.ps1 -Cuda no -SkipModel
#>
param(
  [string]$EnvHome = $env:ROLLUP_INTRO_HOME,
  [ValidateSet('auto', 'yes', 'no')][string]$Cuda = 'auto',
  [ValidateSet('auto', 'portable', 'skip')][string]$Ffmpeg = 'auto',
  [switch]$SkipModel,
  [switch]$Unlocked,          # install from requirements.txt (minimums) instead of the tested lock
  [string]$Python = '3.11'
)
$ErrorActionPreference = 'Stop'
$Skill = $PSScriptRoot
if (-not $EnvHome) { $EnvHome = Join-Path $Skill '.rollup-intro-env' }
New-Item -ItemType Directory -Force -Path $EnvHome | Out-Null
$EnvHome = (Resolve-Path $EnvHome).Path
Write-Host "[setup] tool folder: $EnvHome"

# keep every uv artefact inside the tool folder
$env:UV_CACHE_DIR = Join-Path $EnvHome 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $EnvHome 'python'
$env:UV_LINK_MODE = 'copy'        # cache and venv may sit on different drives
$env:HF_HOME = Join-Path $EnvHome 'hf'

# 1. uv
$uv = Join-Path $EnvHome 'uv\uv.exe'
if (-not (Test-Path $uv)) {
  $sys = Get-Command uv -ErrorAction SilentlyContinue
  if ($sys) { $uv = $sys.Source }
  else {
    Write-Host '[setup] installing uv into the tool folder'
    $env:UV_INSTALL_DIR = Join-Path $EnvHome 'uv'
    $env:UV_NO_MODIFY_PATH = '1'
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $uv = Join-Path $EnvHome 'uv\uv.exe'
  }
}
Write-Host "[setup] uv: $uv ($(& $uv --version))"

# 2. venv with a uv-managed Python inside the tool folder
$venv = Join-Path $EnvHome 'venv'
$py = Join-Path $venv 'Scripts\python.exe'
if (-not (Test-Path $py)) {
  & $uv venv $venv --python $Python --python-preference only-managed --prompt rollup-intro
  if ($LASTEXITCODE) { throw 'uv venv failed' }
}

# 3. dependencies
$useCuda = $false
if ($Cuda -eq 'yes') { $useCuda = $true }
elseif ($Cuda -eq 'auto') {
  $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
  if ($smi) { & $smi.Source -L *> $null; $useCuda = ($LASTEXITCODE -eq 0) }
}
if ($Unlocked) { $reqs = @('-r', (Join-Path $Skill 'requirements.txt')); if ($useCuda) { $reqs += @('-r', (Join-Path $Skill 'requirements-cuda.txt')) } }
elseif ($useCuda) { $reqs = @('-r', (Join-Path $Skill 'requirements-cuda.lock.txt')) }
else { $reqs = @('-r', (Join-Path $Skill 'requirements.lock.txt')) }
Write-Host "[setup] installing Python packages (NVIDIA CUDA runtime: $useCuda)"
& $uv pip install --python $py @reqs
if ($LASTEXITCODE) { throw 'uv pip install failed' }

# 4. ffmpeg
$haveFf = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($Ffmpeg -eq 'portable' -or ($Ffmpeg -eq 'auto' -and -not $haveFf)) {
  $dst = Join-Path $EnvHome 'tools\ffmpeg'
  if (-not (Test-Path (Join-Path $dst 'bin\ffmpeg.exe'))) {
    Write-Host '[setup] downloading portable ffmpeg (BtbN GPL build: NVENC, QSV, AMF, D3D11VA)'
    $zip = Join-Path $EnvHome 'tools\ffmpeg.zip'
    New-Item -ItemType Directory -Force -Path (Split-Path $zip) | Out-Null
    Invoke-WebRequest -UseBasicParsing -OutFile $zip `
      https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip
    $tmp = Join-Path $EnvHome 'tools\ffmpeg_unzip'
    Expand-Archive -Force $zip $tmp
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Move-Item $inner.FullName $dst
    Remove-Item -Recurse -Force $tmp, $zip
  }
  Write-Host "[setup] ffmpeg: $(Join-Path $dst 'bin\ffmpeg.exe')"
} elseif ($haveFf) { Write-Host "[setup] ffmpeg on PATH: $($haveFf.Source)" }
else { Write-Warning 'ffmpeg not found and -Ffmpeg skip given: install it (winget install Gyan.FFmpeg) before building' }

# 5. Whisper large-v3-turbo into the tool folder
$kit = Join-Path $Skill 'scripts\introkit.py'
if (-not $SkipModel) {
  & $py $kit fetch-model
  if ($LASTEXITCODE) { throw 'model download failed' }
}

# 6. report
& $py $kit env
& $py $kit hw
Write-Host "[setup] done. Run:  `"$py`" `"$kit`" <command>   (or any python: introkit re-runs itself in this env)"
