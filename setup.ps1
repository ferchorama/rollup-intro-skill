<#
.SYNOPSIS
  One-folder tool setup for the Rollup intro skill (Windows).

.DESCRIPTION
  Everything goes into ONE folder, by default <skill>\.rollup-intro-env
  (override with -EnvHome or the ROLLUP_INTRO_HOME environment variable):
    uv\        astral uv (only if uv is not already installed)
    python\    uv-managed CPython (no system Python needed or touched)
    cache\uv\  uv download cache
    venv\      the virtualenv with every Python dependency, including PyTorch
    models\    Whisper large-v3-turbo + Real-ESRGAN weights
    hf\        HF_HOME for any other Hugging Face download
    tools\ffmpeg\bin\  portable ffmpeg (only if ffmpeg is not on PATH, or -Ffmpeg portable)
  Nothing outside that folder is modified: no PATH edits, no registry, no global pip.
  Delete the folder to uninstall.

  After uv and the venv exist, this script hands off to `introkit setup`, which
  installs packages, the CUDA or CPU PyTorch wheel, ffmpeg, Whisper and Real-ESRGAN.
  Already-present pieces are skipped. Zoomed-shot upscaling is on by default afterwards.

  If any Python 3.9+ is already installed, you can also run:
    python scripts\introkit.py setup
  Any introkit command will run setup automatically the first time something is missing.

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
$env:ROLLUP_INTRO_HOME = $EnvHome
Write-Host "[setup] tool folder: $EnvHome"

# keep every uv artefact inside the tool folder
$env:UV_CACHE_DIR = Join-Path $EnvHome 'cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $EnvHome 'python'
$env:UV_LINK_MODE = 'copy'        # cache and venv may sit on different drives
$env:HF_HOME = Join-Path $EnvHome 'hf'

# 1. uv (no system Python required)
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

# 3. hand off to introkit setup (packages, PyTorch, ffmpeg, models, summary)
$kit = Join-Path $Skill 'scripts\introkit.py'
$setupArgs = @('setup', '--cuda', $Cuda, '--ffmpeg', $Ffmpeg, '--python', $Python)
if ($SkipModel) { $setupArgs += '--skip-model' }
if ($Unlocked) { $setupArgs += '--unlocked' }
$env:INTROKIT_NO_REEXEC = '1'
$env:INTROKIT_NO_SETUP = '1'
$env:INTROKIT_SETUP_RUNNING = '1'
& $py $kit @setupArgs
if ($LASTEXITCODE) { throw 'introkit setup failed' }
