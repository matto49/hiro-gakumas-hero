$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$deps = if ($env:HIRO_SIMD_DEPS) { $env:HIRO_SIMD_DEPS } else { Join-Path $root '.deps' }
$ocr = Join-Path $deps 'SimdPaddleOCR'
$onnx = Join-Path $deps 'SimdOnnx'
if (-not (Test-Path -LiteralPath (Join-Path $ocr 'models\medium_rec.onnx')) -or
    -not (Test-Path -LiteralPath (Join-Path $onnx 'src\Sdcb.SimdOnnx\Sdcb.SimdOnnx.Sources.props'))) {
    throw '请先准备 SimdPaddleOCR 和对应版本的 SimdOnnx。版本和目录见 docs/assets.md。'
}

New-Item -ItemType Directory -Path (Join-Path $deps 'tmp') -Force | Out-Null
$env:TEMP = Join-Path $deps 'tmp'
$env:TMP = $env:TEMP
$env:DOTNET_CLI_HOME = Join-Path $deps 'dotnet-home'
$env:NUGET_PACKAGES = Join-Path $deps 'nuget'
$env:NUGET_HTTP_CACHE_PATH = Join-Path $deps 'nuget-http'
$env:NUGET_PLUGINS_CACHE_PATH = Join-Path $deps 'nuget-plugins'
$env:NUGET_SCRATCH = Join-Path $deps 'nuget-scratch'
$env:MSBUILDDISABLENODEREUSE = '1'
& dotnet publish (Join-Path $PSScriptRoot 'simd-ocr\HiroSimdOcr.csproj') -c Release --self-contained false -o (Join-Path $deps 'simd-ocr') "-p:SimdPaddleRoot=$ocr" "-p:SimdOnnxRoot=$onnx\" '-p:IsAotCompatible=false' '-p:UseSharedCompilation=false' -m:1 --nologo
exit $LASTEXITCODE
