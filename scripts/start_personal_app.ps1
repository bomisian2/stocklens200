[CmdletBinding()]
param(
    [switch]$NoBrowser,
    [switch]$StatusOnly,
    [int]$StartupTimeoutSeconds = 45
)

$ErrorActionPreference = "Stop"
$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$appPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "personal_app.py"))
$url = "http://127.0.0.1:8512/"
$port = 8512
$stateRoot = Join-Path $env:LOCALAPPDATA "StockLens200"
$logRoot = Join-Path $stateRoot "logs"
$fallbackVenv = Join-Path $stateRoot "venv"
$repoVenv = Join-Path $repoRoot ".venv-py312"
$pidFile = Join-Path $stateRoot "streamlit-8512.json"

function Get-ListenerProcess {
    $connection = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -eq $connection) { return $null }
    return Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
}

function Test-IsStockLensProcess([object]$processInfo) {
    if ($null -eq $processInfo -or -not (Test-Path -LiteralPath $pidFile)) { return $false }
    try {
        $record = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
        return (
            [int]$record.pid -eq [int]$processInfo.Id -and
            [System.IO.Path]::GetFullPath([string]$record.app_path) -eq $appPath -and
            [string]$record.python_version -like "3.12.*"
        )
    } catch {
        return $false
    }
}

function Get-Python312 {
    $candidates = @(
        (Join-Path $repoVenv "Scripts\python.exe"),
        (Join-Path $fallbackVenv "Scripts\python.exe"),
        $env:STOCKLENS_PYTHON312,
        "C:\Users\leegeon\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and (Test-Path -LiteralPath $_) }

    foreach ($candidate in $candidates) {
        $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $version.Trim() -eq "3.12") {
            return [System.IO.Path]::GetFullPath($candidate)
        }
    }
    return $null
}

function Ensure-Environment {
    $python = Get-Python312
    if ($null -ne $python -and $python -match "[\\/]Scripts[\\/]python\.exe$") { return $python }
    if ($null -eq $python) {
        throw "Python 3.12를 찾지 못했습니다. Python 3.12 설치 후 다시 실행하세요."
    }

    New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
    & $python -m venv $fallbackVenv
    if ($LASTEXITCODE -ne 0) { throw "StockLens Python 3.12 환경 생성에 실패했습니다." }
    $venvPython = Join-Path $fallbackVenv "Scripts\python.exe"
    & $venvPython -m pip install --disable-pip-version-check -r (Join-Path $repoRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "StockLens 필수 패키지 설치에 실패했습니다." }
    return $venvPython
}

$mutex = [System.Threading.Mutex]::new($false, "Local\StockLens200Launcher8512")
$mutexHeld = $false
try {
    $mutexHeld = $mutex.WaitOne([TimeSpan]::FromSeconds(10))
    if (-not $mutexHeld) { throw "다른 StockLens 시작 작업이 진행 중입니다. 잠시 후 다시 실행하세요." }

    $existing = Get-ListenerProcess
    if ($null -ne $existing) {
        if (-not (Test-IsStockLensProcess $existing)) {
            throw "포트 8512를 다른 프로그램(PID $($existing.Id))이 사용 중입니다. 자동 종료하지 않았습니다."
        }
        Write-Host "StockLens가 이미 실행 중입니다. PID $($existing.Id) — 중복 실행하지 않습니다."
        if (-not $NoBrowser -and -not $StatusOnly) { Start-Process $url }
        exit 0
    }

    if ($StatusOnly) {
        Write-Host "StockLens가 현재 실행 중이 아닙니다."
        exit 3
    }

    $python = Ensure-Environment
    $pythonVersion = & $python -c "import platform; print(platform.python_version())"
    if (-not $pythonVersion.StartsWith("3.12.")) { throw "선택된 실행 환경이 Python 3.12가 아닙니다." }

    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    $stdoutLog = Join-Path $logRoot "streamlit-8512.out.log"
    $stderrLog = Join-Path $logRoot "streamlit-8512.err.log"
    $env:STOCKLENS_DEFAULT_TICKER = "NVDA"
    $arguments = @(
        "-m", "streamlit", "run", $appPath,
        "--server.port", "8512",
        "--server.address", "127.0.0.1",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.fileWatcherType", "none"
    )
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repoRoot `
        -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    $healthy = $false
    while ((Get-Date) -lt $deadline -and -not $process.HasExited) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -eq 200) { $healthy = $true; break }
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $healthy) {
        $errorTail = if (Test-Path $stderrLog) { (Get-Content $stderrLog -Tail 20) -join [Environment]::NewLine } else { "로그 없음" }
        throw "StockLens가 ${StartupTimeoutSeconds}초 안에 시작되지 않았습니다.`n$errorTail"
    }

    $listenerProcess = Get-ListenerProcess
    if ($null -eq $listenerProcess) { throw "8512 포트의 StockLens 프로세스 ID를 확인하지 못했습니다." }
    [ordered]@{
        pid = $listenerProcess.Id
        app_path = $appPath
        python_path = $python
        python_version = $pythonVersion
        started_at = (Get-Date).ToUniversalTime().ToString("o")
        url = $url
    } | ConvertTo-Json | Set-Content -LiteralPath $pidFile -Encoding UTF8

    $head = (& git -C $repoRoot rev-parse HEAD 2>$null).Trim()
    Write-Host "StockLens 실행 완료 · PID $($listenerProcess.Id) · Python $pythonVersion · commit $head"
    Write-Host $url
    if (-not $NoBrowser) { Start-Process $url }
    exit 0
} catch {
    Write-Host "StockLens 시작 실패" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 2
} finally {
    if ($mutexHeld) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
