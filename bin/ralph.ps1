# ralph.ps1 — Windows PowerShell launcher for 랄프톤 Voice Terminal
# WSL2 내부의 ralph CLI를 호출합니다.
#
# 사용법:
#   .\ralph.ps1 voice    # 음성 모드
#   .\ralph.ps1 mobile   # 모바일 접속 (브라우저 자동 열림)
#   .\ralph.ps1 start    # 전체 시작
#   .\ralph.ps1 stop     # 종료
#   .\ralph.ps1 status   # 상태

param(
    [Parameter(Position=0)]
    [string]$Command = "help"
)

# WSL2 확인
$wslCheck = wsl.exe --status 2>$null
if (-not $?) {
    Write-Host ""
    Write-Host "  WSL2가 설치되지 않았습니다." -ForegroundColor Red
    Write-Host "  설치: wsl --install"
    Write-Host ""
    exit 1
}

# 랄프톤 경로 감지
$ralphPath = wsl.exe -- bash -c "test -f ~/ralphton/bin/ralph && echo OK" 2>$null
if ($ralphPath -ne "OK") {
    Write-Host ""
    Write-Host "  랄프톤이 WSL2에 설치되지 않았습니다." -ForegroundColor Red
    Write-Host "  WSL2 Ubuntu에서:"
    Write-Host "    git clone <repo> ~/ralphton"
    Write-Host "    cd ~/ralphton && ./setup.sh"
    Write-Host ""
    exit 1
}

# ralph 실행
wsl.exe -- bash -c "cd ~/ralphton && bin/ralph $Command"

# mobile/start 시 브라우저 자동 열기
if ($Command -eq "mobile" -or $Command -eq "start") {
    $port = wsl.exe -- bash -c "grep RALPH_PORT ~/.ralph.env 2>/dev/null | head -1 | cut -d= -f2" 2>$null
    if (-not $port) { $port = "7777" }
    $port = $port.Trim()
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:$port"
}
