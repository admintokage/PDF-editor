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

Compress-Archive -Path $outDir -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "作成しました: $zipPath"
