$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$Root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$LuaCommit = "faaf663340347a78b22ed94c63c24fe090bd9784"
$LuaRepo = "https://luajit.org/git/luajit.git"
$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }
$Runtime = Join-Path $Root "packaging/runtime/luajit"
$Spec = Join-Path $Root "packaging/translation_builder.spec"
if ($PSVersionTable.PSVersion.Major -ge 7) { $PSNativeCommandUseErrorActionPreference = $true }
function Invoke-Native([scriptblock]$Command, [string]$Name) {
  & $Command
  $ExitCode = $LASTEXITCODE
  if ($ExitCode -ne 0) { throw "$Name failed with exit code $ExitCode" }
}

Push-Location $Root
try {
  Invoke-Native { & $Python -m pip install --requirement packaging/requirements-windows.txt } "pip install"
  Invoke-Native { & $Python -m unittest discover -s tests } "tests"

  $TempRoot = if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() }
  $LuaSource = Join-Path $TempRoot "luajit-source"
  if (Test-Path $LuaSource) { Remove-Item -Recurse -Force $LuaSource }
  try {
    # The official dumb-HTTP server requires a complete clone;
    # clone --no-checkout has already downloaded all repository objects.
    Invoke-Native { git clone --no-checkout $LuaRepo $LuaSource } "git clone"
    Invoke-Native { git -C $LuaSource checkout --detach $LuaCommit } "git checkout"
    $LuaHead = (Invoke-Native { git -C $LuaSource rev-parse HEAD } "git rev-parse" | Out-String).Trim()
    if ($LuaHead -ne $LuaCommit) { throw "LuaJIT checkout mismatch: expected $LuaCommit, got $LuaHead" }

    $VsDevCmd = Get-ChildItem "${env:ProgramFiles}\Microsoft Visual Studio\2022" -Filter VsDevCmd.bat -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $VsDevCmd) { throw "Visual Studio 2022 VsDevCmd.bat was not found" }
    $LuaSourceSrc = Join-Path $LuaSource "src"
    Invoke-Native { cmd /c "`"$($VsDevCmd.FullName)`" -arch=x64 && cd /d `"$LuaSourceSrc`" && call msvcbuild.bat" } "LuaJIT MSVC build"

    if (Test-Path $Runtime) { Remove-Item -Recurse -Force $Runtime }
    New-Item -ItemType Directory -Force $Runtime | Out-Null
    Copy-Item (Join-Path $LuaSource "src/luajit.exe") $Runtime
    Copy-Item (Join-Path $LuaSource "src/lua51.dll") $Runtime
    Copy-Item -Recurse (Join-Path $LuaSource "src/jit") (Join-Path $Runtime "jit")
    foreach ($file in @("luajit.exe", "lua51.dll")) {
      if (-not (Test-Path (Join-Path $Runtime $file))) { throw "Missing LuaJIT runtime file: $file" }
    }
  } finally {
    if (Test-Path $LuaSource) { Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $LuaSource }
  }
  $bytes = [IO.File]::ReadAllBytes((Join-Path $Runtime "luajit.exe"))
  if ($bytes[0] -ne 0x4d -or $bytes[1] -ne 0x5a) { throw "LuaJIT executable is not a PE file" }
  $pe = [BitConverter]::ToUInt16($bytes, [BitConverter]::ToInt32($bytes, 0x3c) + 4)
  if ($pe -ne 0x8664) { throw "LuaJIT executable is not x64" }

  $VersionFile = Join-Path $TempRoot "version.txt"
  $Version = (Get-Content (Join-Path $Root "pyproject.toml") |
    Select-String '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1).Matches.Groups[1].Value
  if (-not $Version) { throw "project version is missing from pyproject.toml" }
  Set-Content -Path $VersionFile -Value $Version -Encoding ascii
  $Version = (Get-Content $VersionFile -Raw).Trim()
  foreach ($Variant in @("cli", "gui")) {
    $env:GEN1RECOMP_VARIANT = $Variant
    Invoke-Native { & $Python -m PyInstaller --clean --noconfirm $Spec } "PyInstaller $Variant"
    $Binary = Join-Path $Root "dist/gen1recomp-translation-mod-generator-$Variant.exe"
    Invoke-Native { & $Binary --self-check } "self-check $Variant"
    if ($Variant -eq "gui") {
      Invoke-Native { & $Binary --gui-self-check } "GUI smoke check"
    }
    $Artifact = Join-Path $Root "dist/gen1recomp-translation-mod-generator-$Version-$Variant-windows-x64.exe"
    if (Test-Path $Artifact) { Remove-Item -Force $Artifact }
    Copy-Item $Binary $Artifact
  }
  Remove-Item Env:GEN1RECOMP_VARIANT -ErrorAction SilentlyContinue
} finally { Pop-Location }
