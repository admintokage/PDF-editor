# 配布物をビルドして zip にする。Windows PowerShell 5.1 で動く。
# 使い方: powershell -ExecutionPolicy Bypass -File packaging/build.ps1 -Target smoke|app
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("smoke", "app")]
    [string]$Target
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if ($Target -eq "smoke") {
    $name = "PDF-editor-smoke"
    $entry = Join-Path $root "packaging\smoke_app.py"
} else {
    $name = "PDF-editor"
    $entry = Join-Path $root "pdfeditor\__main__.py"
}

if (-not (Test-Path $entry)) {
    Write-Error "エントリポイントが見つかりません: $entry。このターゲットはまだビルドできません。"
    exit 1
}

$env:PDFEDITOR_TARGET = $Target
$distDir = Join-Path $root "dist"
$outDir = Join-Path $distDir $name
$zipPath = Join-Path $distDir "$name.zip"

if (Test-Path $outDir) { Remove-Item -Recurse -Force $outDir }
if (Test-Path $zipPath) { Remove-Item -Force $zipPath }

uv run pyinstaller --noconfirm --clean --distpath $distDir --workpath (Join-Path $root "build") (Join-Path $root "packaging\pdfeditor.spec")
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller が失敗しました (終了コード $LASTEXITCODE)"
    exit 1
}

if ($Target -eq "app") {
    # 利用者に見せる説明とライセンスを、展開したフォルダの直下に置く
    Copy-Item (Join-Path $root "LICENSE") $outDir
    Copy-Item (Join-Path $root "packaging\README.txt") $outDir
}

# Compress-Archive は古い PowerShell でパス区切りが円記号になるため、.NET で zip にする。
# 展開すると $name フォルダ 1 つになるよう、基底フォルダ名を含める。
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($outDir, $zipPath, [System.IO.Compression.CompressionLevel]::Optimal, $true)
Write-Host "作成しました: $zipPath"
