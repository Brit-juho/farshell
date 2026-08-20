# fsh.ps1 — Windows PowerShell launcher for FarShell
# WSL2 내부의 fsh CLI를 호출합니다. (vt.ps1은 하위 호환용으로 남아 있습니다.)
#
# 사용법:
#   .\fsh.ps1 voice    # 음성 모드
#   .\fsh.ps1 mobile   # 모바일 접속 (브라우저 자동 열림)
#   .\fsh.ps1 start    # 전체 시작
#   .\fsh.ps1 stop     # 종료
#   .\fsh.ps1 status   # 상태

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

# FarShell 경로 감지
$fshPath = wsl.exe -- bash -c "test -f ~/farshell/bin/fsh && echo OK" 2>$null
if ($fshPath -ne "OK") {
    Write-Host ""
    Write-Host "  FarShell이 WSL2에 설치되지 않았습니다." -ForegroundColor Red
    Write-Host "  WSL2 Ubuntu에서:"
    Write-Host "    git clone <repo> ~/farshell"
    Write-Host "    cd ~/farshell && ./install.sh"
    Write-Host ""
    exit 1
}

# fsh 실행
wsl.exe -- bash -c "cd ~/farshell && bin/fsh $Command"

# mobile/start 시 브라우저 자동 열기
if ($Command -eq "mobile" -or $Command -eq "start") {
    $port = wsl.exe -- bash -c "grep VT_PORT ~/.vt.env 2>/dev/null | head -1 | cut -d= -f2" 2>$null
    if (-not $port) { $port = "7777" }
    $port = $port.Trim()
    Start-Sleep -Seconds 2
    Start-Process "http://localhost:$port"
}
