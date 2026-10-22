#!/usr/bin/env python3
"""Disposable profile server: isolated HOME/state/tmux and macOS Seatbelt.

`--fake-cli` uses an explicit native-protocol fixture, never real credentials.
Without that option, the server is ready for a person's independent test login.
The generated metadata contains only a disposable browser session; mode 0600.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

STUB = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
int main(int argc, char **argv) {
  const char *tool = strrchr(argv[0], '/'); tool = tool ? tool + 1 : argv[0];
  int codex = !strcmp(tool, "codex");
  const char *root = getenv(codex ? "CODEX_HOME" : "CLAUDE_CONFIG_DIR");
  if (!root) return 2;
  if (argc > 1 && !strcmp(argv[1], "--version")) { puts(codex ? "codex-cli fixture" : "fixture Claude Code"); return 0; }
  if (argc > 1 && (!strcmp(argv[1], "auth") || !strcmp(argv[1], "login"))) {
    char auth[4096]; snprintf(auth, sizeof(auth), "%s/%s", root, codex ? "auth.json" : ".credentials.json");
    int ok = access(auth, F_OK) == 0;
    if (!codex) puts(ok ? "{\"loggedIn\":true}" : "{\"loggedIn\":false}");
    return ok ? 0 : 1;
  }
  char sid[80]; snprintf(sid, sizeof(sid), "00000000-0000-4000-8000-%012d", getpid());
  const char *source = "startup";
  for (int i=1; i+1<argc; i++) if (!strcmp(argv[i], "--resume") || !strcmp(argv[i], "resume")) {
    snprintf(sid,sizeof(sid),"%s",argv[i+1]); source="resume";
  }
  char dir[4096]; snprintf(dir,sizeof(dir),"%s/sessions",root); mkdir(dir,0700);
  char path[8192]; snprintf(path,sizeof(path),"%s/%d.json",dir,getpid());
  FILE *state = fopen(path,"w");
  if (state) { fprintf(state,"{\"pid\":%d,\"entrypoint\":\"cli\",\"sessionId\":\"%s\"}",getpid(),sid); fclose(state); }
  const char *hook = getenv("FSH_SANDBOX_HOOK");
  if (!hook) hook = HOOK_PATH;
  char command[8192]; snprintf(command,sizeof(command),"'%s' session_start %s",hook,codex ? "codex" : "claude");
  FILE *pipe = popen(command,"w");
  char cwd[4096]; getcwd(cwd,sizeof(cwd));
  if (pipe) { fprintf(pipe,"{\"session_id\":\"%s\",\"cwd\":\"%s\",\"source\":\"%s\"}",sid,cwd,source); pclose(pipe); }
  puts("Sandbox native CLI fixture. No model or real account is used.");
  puts("❯ "); fflush(stdout);
  char line[4096]; while (fgets(line,sizeof(line),stdin)) { if (strstr(line,"/exit")) break; puts("❯ "); fflush(stdout); }
  unlink(path);
  return 0;
}
'''


def seatbelt(folder, allow_provider_network=False):
    real_home = Path.home()
    rules = ['(version 1)', '(allow default)', '(deny file-write*)',
             '(allow file-write* (subpath ' + json.dumps(str(folder)) + '))',
             '(allow file-write* (subpath "/dev"))', '(deny network*)',
             '(allow network-bind (local ip "localhost:*"))',
             '(allow network-inbound (local ip "localhost:*"))',
             '(allow network-outbound (remote ip "localhost:*"))',
             '(allow network* (local unix-socket (subpath ' + json.dumps(str(folder)) + ')))',
             '(allow network* (remote unix-socket (subpath ' + json.dumps(str(folder)) + ')))']
    if allow_provider_network:
        rules.append('(allow network-outbound (remote ip "*:*"))')
    for name in ('.clauth', '.claude', '.codex', '.vt', '.ssh', '.aws', '.config/gh', '.vt.env'):
        rules.append('(deny file-read* (subpath ' + json.dumps(str(real_home / name)) + '))')
    path = folder / 'sandbox.sb'
    path.write_text('\n'.join(rules))
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fake-cli', action='store_true')
    parser.add_argument('--allow-provider-network', action='store_true', help='Real-login sandbox only: allow provider HTTPS and DNS')
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--directory', type=Path)
    args = parser.parse_args()
    if args.fake_cli and args.allow_provider_network:
        parser.error('Fixture sandboxes must remain offline')
    folder = (args.directory or Path(tempfile.mkdtemp(prefix='fsh-profile-sandbox-'))).resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    for name in ('home', 'state', 'run', 'tmp', 'bin', 'tmux', 'workspace'):
        (folder / name).mkdir(mode=0o700, exist_ok=True)
    if sys.platform == 'darwin':
        # macOS /bin/ps is setuid. Seatbelt refuses exec of a setuid image;
        # an unprivileged local copy works for same-user inspection. Re-sign
        # that copy ad hoc (Apple's platform signature cannot be relocated).
        # Do not weaken process-identity checks to make the test pass.
        shutil.copyfile('/bin/ps', folder / 'bin' / 'ps')
        (folder / 'bin' / 'ps').chmod(0o700)
        subprocess.run(['codesign', '--force', '--sign', '-', str(folder / 'bin' / 'ps')], check=True, capture_output=True)
    if args.port:
        port = args.port
    else:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
    config = folder / 'home' / 'isolated.env'
    machine_token = secrets.token_hex(32)
    config.write_text('\n'.join(('VT_AUTH_TOKEN=' + machine_token, 'VT_PORT=' + str(port), 'VT_PYTHON=' + sys.executable, 'VT_DIR=' + str(REPO))) + '\n')
    config.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TERM', 'USER', 'LOGNAME', 'SHELL')}
    env.update(HOME=str(folder / 'home'), TMPDIR=str(folder / 'tmp'),
               VT_STATE_DIR=str(folder / 'home' / '.vt-profile-test'), VT_RUN_DIR=str(folder / 'run'),
               VT_CONFIG=str(config), VT_WORKSPACE_PATH=str(folder / 'workspace.json'),
               VT_TMUX_SOCKET='profile-' + secrets.token_hex(5), TMUX_TMPDIR=str(folder / 'tmux'),
               VT_TUNNEL_PROVIDER='none', VT_PORT=str(port), VT_BROWSE_ROOTS=str(folder / 'workspace'),
               VT_USAGE_PROVIDER='native', PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(REPO / 'server'),
               FSH_SANDBOX_DIR=str(folder), VT_TMUX_CONF=str(REPO / 'config' / 'vt-tmux.conf'))
    env['PATH'] = str(folder / 'bin') + os.pathsep + env.get('PATH', '')
    if args.fake_cli:
        if not shutil.which('cc'):
            raise SystemExit('A C compiler is required for the native CLI fixture')
        source = folder / 'native-fixture.c'
        source.write_text('#define HOOK_PATH ' + json.dumps(str(REPO / 'server' / 'agent_hook.sh')) + '\n' + STUB)
        subprocess.run(['cc', str(source), '-o', str(folder / 'bin' / 'claude')], check=True, capture_output=True)
        shutil.copy2(folder / 'bin' / 'claude', folder / 'bin' / 'codex')
    else:
        # Installed CLI binaries can live inside ~/.codex, whose credential
        # tree remains denied. Copy only the selected executable, never that
        # directory or any credentials, into the disposable sandbox.
        import hashlib
        binaries = {}
        for tool in ('claude', 'codex'):
            found = shutil.which(tool)
            if not found:
                continue
            original = Path(found).resolve()
            with original.open('rb') as stream:
                magic = stream.read(4)
            if magic not in (b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\x7fELF'):
                raise SystemExit('Native standalone CLI required for sandbox: ' + tool)
            copied = folder / 'bin' / tool
            shutil.copyfile(original, copied)
            copied.chmod(0o700)
            with copied.open('rb') as stream:
                binaries[tool] = {'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
        (folder / 'native-binaries.json').write_text(json.dumps(binaries))
        (folder / 'native-binaries.json').chmod(0o600)
    bootstrap = folder / 'serve.py'
    bootstrap.write_text('''
import json, os, secrets
from pathlib import Path
from fastapi.responses import HTMLResponse
import vt_env
vt_env.apply_boundary_overrides()
import auth
auth.VT_AUTH_SESSION_KEY = secrets.token_hex(32)
auth.VT_AUTH_PASSWORD_HASH = auth.hash_password('sandbox-only-password')
from main import app
folder = Path(os.environ['FSH_SANDBOX_DIR'])
metadata = {'pid': os.getpid(), 'port': int(os.environ['VT_PORT']), 'cookie': auth.make_session(elev_exp=int(__import__('time').time()) + 3600), 'fake_cli': FAKE, 'directory': str(folder)}
path = folder / 'browser-session.json'
path.write_text(json.dumps(metadata))
path.chmod(0o600)
if FAKE:
    import usage.profiles
    def fake_usage(url, headers):
        if url.endswith('/profile'):
            return {'account': {'uuid': 'sandbox-subject'}, 'organization': {'uuid': 'sandbox-tenant'}}
        if 'wham' in url:
            return {'account_id': 'sandbox-codex', 'rate_limit': {'primary_window': {'used_percent': 15, 'limit_window_seconds': 18000, 'reset_after_seconds': 1000}}}
        return {'five_hour': {'utilization': 25, 'resets_at': '2030-01-01T00:00:00Z'}}
    usage.profiles._request = fake_usage
@app.get('/profile-sandbox', response_class=HTMLResponse)
def profile_test():
    return r"""<!doctype html><html lang='ko' data-skin='farshell'><head><meta name='viewport' content='width=device-width, initial-scale=1'><link rel='stylesheet' href='/static/dist/app.css'></head><body><main id='profile-test' class='vt-agent-config' style='padding:16px;max-width:1000px;margin:auto'></main><script>window.API_BASE='';window._tokenQuery='';</script><script src='/static/js/locales/en.js'></script><script type='module'>const dictionary=window.__VT_I18N; if(new URLSearchParams(location.search).get('lang')==='en')window.vtT=(s,v)=>{let t=dictionary[s]||s;return t.replace(/\\{(\\w+)\\}/g,(m,k)=>v?.[k]??m)}; import {mount} from '/static/js/panels/agent-profiles.js'; window.profileManager=mount(document.getElementById('profile-test'));</script></body></html>"""
import uvicorn
uvicorn.run(app, host='127.0.0.1', port=int(os.environ['VT_PORT']), log_level='warning')
'''.replace('FAKE', repr(args.fake_cli)))
    command = [sys.executable, str(bootstrap)]
    if sys.platform == 'darwin':
        if not shutil.which('sandbox-exec'):
            raise SystemExit('Seatbelt is unavailable; refusing to claim OS sandboxing')
        command = ['sandbox-exec', '-f', str(seatbelt(folder, args.allow_provider_network)), *command]
    else:
        raise SystemExit('This launcher requires macOS Seatbelt; use an isolated container on Linux')
    (folder / 'environment.json').write_text(json.dumps(env, indent=2))
    (folder / 'environment.json').chmod(0o600)
    login_wrapper = folder / 'login-profile'
    login_wrapper.write_text('#!' + sys.executable + '\n' +
        "import json, os, subprocess, sys\nfrom pathlib import Path\n" +
        "folder = Path(__file__).resolve().parent\n" +
        "env = json.loads((folder / 'environment.json').read_text())\n" +
        "sys.exit(subprocess.call(['sandbox-exec', '-f', str(folder / 'sandbox.sb'), " + repr(sys.executable) + ", " + repr(str(REPO / 'server' / 'agent_profiles_cli.py')) + ", 'login', *sys.argv[1:]], env=env, cwd=folder))\n")
    login_wrapper.chmod(0o700)
    print(json.dumps({'directory' : str(folder), 'port': port, 'fake_cli': args.fake_cli}), flush=True)
    try:
        return subprocess.call(command, env=env, cwd=folder)
    finally:
        subprocess.run(['tmux', '-L', env['VT_TMUX_SOCKET'], 'kill-server'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    raise SystemExit(main())
