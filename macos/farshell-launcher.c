/*
 * FarShell.app 실행 껍데기 (macOS 전용).
 *
 * 왜 필요한가(2026-11-06): FarShell 서버가 Python 으로 직접 뜨면 macOS 권한(TCC)의 "책임자"가 그 Python 이 된다.
 * 그래서 서버와, 서버가 띄운 tmux 안의 모든 프로그램이 데스크톱·다운로드 같은 보호 위치에 닿을 때마다
 * "python3.14 가 접근하려 합니다" 창이 폴더 종류별로 떴고, Python 경로에 패치 버전이 들어 있어 업그레이드하면
 * 허용 기록이 초기화될 수 있었다. 이 껍데기를 거쳐 띄우면 책임자가 FarShell.app(com.farshell.server)이 된다
 * — 권한 창에 FarShell 이라고 뜨고, 껍데기를 다시 빌드하지 않는 한 허용이 유지된다.
 *
 * 동작: 1단계(바깥)는 자기 자신을 "책임 끊기" 속성(responsibility_spawnattrs_setdisclaim)으로 다시 띄운다.
 * 2단계(안쪽)는 스스로 책임자가 되고, 인자로 받은 명령을 보통 방식으로 띄워(책임 상속) 끝날 때까지 기다린다.
 * 두 단계 모두 받은 시그널을 자식에게 넘기고(무시 중이던 시그널은 그대로 둔다 — nohup 의 SIGHUP), 자식의 종료
 * 코드를 그대로 돌려준다. 안쪽은 FSH_APP_PIDFILE 이 있으면 실제 명령의 pid 를 그 파일에 쓴다(bin/fsh 가
 * server.pid 로 옮긴다 — 워치독·fsh stop·SIGUSR1 스택 덤프가 Python 서버를 직접 가리키게).
 */
#include <errno.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
int responsibility_spawnattrs_setdisclaim(posix_spawnattr_t *attrs, int disclaim);

static volatile pid_t child = 0;
static const int FORWARD[] = {SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1, SIGUSR2};

static void forward(int sig) {
    if (child > 0) kill(child, sig);
}

static void install_forwarding(void) {
    for (size_t i = 0; i < sizeof FORWARD / sizeof FORWARD[0]; i++) {
        struct sigaction old;
        if (sigaction(FORWARD[i], NULL, &old) == 0 && old.sa_handler == SIG_IGN) continue;
        struct sigaction sa;
        memset(&sa, 0, sizeof sa);
        sa.sa_handler = forward;
        sigemptyset(&sa.sa_mask);
        sa.sa_flags = SA_RESTART;
        sigaction(FORWARD[i], &sa, NULL);
    }
}

static int write_pidfile(const char *path, pid_t pid) {
    char tmp[4096];
    if (snprintf(tmp, sizeof tmp, "%s.tmp", path) >= (int)sizeof tmp) return -1;
    FILE *f = fopen(tmp, "w");
    if (!f) return -1;
    fprintf(f, "%d\n", (int)pid);
    if (fclose(f) != 0) return -1;
    return rename(tmp, path);
}

static int run(char *const argv[], int disclaim, const char *pidfile) {
    posix_spawnattr_t at;
    posix_spawnattr_init(&at);
    if (disclaim && responsibility_spawnattrs_setdisclaim(&at, 1) != 0) {
        fprintf(stderr, "FarShell: responsibility disclaim failed\n");
        posix_spawnattr_destroy(&at);
        return 111;
    }
    pid_t pid;
    int rc = posix_spawnp(&pid, argv[0], NULL, &at, argv, environ);
    posix_spawnattr_destroy(&at);
    if (rc != 0) {
        fprintf(stderr, "FarShell: cannot start %s: %s\n", argv[0], strerror(rc));
        return 127;
    }
    child = pid;
    if (pidfile && *pidfile && write_pidfile(pidfile, pid) != 0)
        fprintf(stderr, "FarShell: cannot write pid file %s: %s\n", pidfile, strerror(errno));
    int st;
    while (waitpid(pid, &st, 0) < 0) {
        if (errno != EINTR) return 113;
    }
    if (WIFEXITED(st)) return WEXITSTATUS(st);
    return 128 + WTERMSIG(st);
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: FarShell command [args...]\n");
        return 2;
    }
    install_forwarding();
    if (!getenv("FSH_APP_INNER")) {
        char self[4096];
        uint32_t n = sizeof self;
        if (_NSGetExecutablePath(self, &n) != 0) return 112;
        setenv("FSH_APP_INNER", "1", 1);
        argv[0] = self;
        return run(argv, 1, NULL);           /* 안쪽 FarShell 을 책임 끊기로 띄운다 */
    }
    unsetenv("FSH_APP_INNER");
    const char *pidfile = getenv("FSH_APP_PIDFILE");
    char *pf = pidfile ? strdup(pidfile) : NULL;
    unsetenv("FSH_APP_PIDFILE");             /* 실제 명령의 환경에는 남기지 않는다 */
    return run(argv + 1, 0, pf);             /* 실제 명령은 FarShell 책임으로 */
}
