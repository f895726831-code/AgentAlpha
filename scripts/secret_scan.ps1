# 本地密钥扫描（Windows PowerShell）
#
# 依赖：gitleaks
#   winget install gitleaks
#   或 scoop install gitleaks
#
# 用法：
#   powershell -File scripts/secret_scan.ps1          # 扫描 git 历史 + 工作区
#   powershell -File scripts/secret_scan.ps1 -Files   # 仅扫描工作区文件（不读 git 历史）

param(
    [switch]$Files
)

$ErrorActionPreference = "Stop"

$gitleaks = Get-Command gitleaks -ErrorAction SilentlyContinue
if (-not $gitleaks) {
    Write-Host "未找到 gitleaks。请先安装：" -ForegroundColor Yellow
    Write-Host "  winget install gitleaks" -ForegroundColor Yellow
    Write-Host "  # 或 scoop install gitleaks" -ForegroundColor Yellow
    exit 1
}

Push-Location (Join-Path $PSScriptRoot "..")
try {
    if ($Files) {
        Write-Host "==> 扫描工作区文件（忽略 .gitignore）" -ForegroundColor Cyan
        gitleaks detect --source . --no-git --redact --verbose
    } else {
        Write-Host "==> 扫描 git 历史（默认）" -ForegroundColor Cyan
        gitleaks detect --source . --redact --verbose
    }
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`nOK: 未发现密钥。" -ForegroundColor Green
    } else {
        Write-Host "`n发现疑似密钥，请处理后重试。" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
