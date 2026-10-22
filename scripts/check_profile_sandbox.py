#!/usr/bin/env python3
"""Exercise the disposable OS-sandboxed server over HTTP and a fresh browser.

Refuses live-login sandboxes. No production token, browser profile or tmux socket
is reused. Fixture/native/live results are intentionally reported separately.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args()
    folder = args.directory.resolve()
    metadata = json.loads((folder / 'browser-session.json').read_text())
    if not metadata.get('fake_cli') or Path(metadata['directory']).resolve() != folder:
        raise SystemExit('This check only mutates an explicit fake-CLI sandbox')
    env = json.loads((folder / 'environment.json').read_text())
    home = Path(env['HOME'])
    state = Path(env['VT_STATE_DIR']) / 'agent-profiles'
    base = 'http://127.0.0.1:' + str(metadata['port'])
    machine_token = next(line.split('=', 1)[1] for line in Path(env['VT_CONFIG']).read_text().splitlines() if line.startswith('VT_AUTH_TOKEN='))
    evidence = {'fake_native_cli': True, 'real_oauth_tested': False, 'checks': []}

    def check(name, condition):
        assert condition, name
        evidence['checks'].append(name)

    def api(action, payload=None, *, apply=False, cookie=True, token=False):
        path = '/api/agent-profiles/apply' if apply else '/api/agent-profiles/read/' + action
        headers = {'Content-Type': 'application/json'}
        if cookie:
            headers['Cookie'] = 'vt_session=' + metadata['cookie']
        if token:
            headers['Authorization'] = 'Bearer ' + machine_token
        request = urllib.request.Request(base + path, data=json.dumps(payload or {}).encode(), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def read(action, payload=None):
        code, data = api(action, payload)
        assert code == 200, (action, code, data)
        return data

    def change(operation, payload):
        plan = read('preview', {'operation': operation, 'payload': payload})
        code, result = api('', {'token': plan['token']}, apply=True)
        assert code == 200, (operation, code, result)
        return result

    check('anonymous cannot manage profiles', api('list', cookie=False)[0] == 401)
    check('machine-token caller cannot manage profiles', api('list', cookie=False, token=True)[0] == 403)
    check('unknown request fields fail closed', api('list', {'root': str(home)})[0] == 409)
    first = change('create', {'name': 'sandbox-development', 'tool': 'claude', 'purpose': 'test'})
    second = change('create', {'name': 'sandbox-review', 'tool': 'claude', 'purpose': 'test', 'account_id': first['account_id']})
    check('one account has two separate profiles', first['account_id'] == second['account_id'] and first['id'] != second['id'])
    for row in (first, second):
        root = state / row['id'] / row['tool']
        (root / '.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': 'sandbox-token-' + row['id'], 'refreshToken': 'sandbox-refresh', 'expiresAt': 9999999999999}}))
        (root / '.credentials.json').chmod(0o600)
    native = home / '.claude'
    native.mkdir(exist_ok=True)
    (native / 'settings.json').write_text(json.dumps({'model': 'sample-model', 'env': {'TOKEN': 'private-sentinel'}}))
    (home / '.claude.json').write_text(json.dumps({'mcpServers': {'docs': {'command': 'sample-server'}}}))
    skill = native / 'skills' / 'sample'
    skill.mkdir(parents=True, exist_ok=True)
    (skill / 'SKILL.md').write_text('---\nname: sample\n---\nSample skill\n')
    script = skill / 'run.sh'
    script.write_text('#!/bin/sh\nprintf sample\n')
    script.chmod(0o700)
    source = next(s for s in read('sources')['sources'] if s['tool'] == 'claude')
    imported = change('config-import', {'profile_id': first['id'], 'revision': first['revision'], 'source_id': source['id']})
    check('configuration import excludes native login credentials', imported['credentials_copied'] is False)
    check('skill executables retain executable permission', (state / first['id'] / 'claude/skills/sample/run.sh').stat().st_mode & 0o777 == 0o700)

    def current():
        return next(p for p in read('list')['profiles'] if p['id'] == first['id'])

    for kind in ('mcp', 'skill'):
        scan = read('inventory', {'profile_id': first['id']})
        row = next(r for r in scan['items'] if r['kind'] == kind)
        for enabled in (False, True):
            change('native-toggle', {'profile_id': first['id'], 'revision': current()['revision'], 'changes': [{'id': row['id'], 'enabled': enabled}]})
            after = read('inventory', {'profile_id': first['id']})
            check(kind + (' ON' if enabled else ' OFF') + ' round trip', next(r for r in after['items'] if r['kind'] == kind)['enabled'] is enabled)
    check('configuration values are absent from HTTP inventory', 'private-sentinel' not in json.dumps(read('inventory', {'profile_id': first['id']})))
    change('hook', {'profile_id': first['id'], 'revision': current()['revision'], 'event': 'Stop', 'command': 'sample-notifier --done'})
    check('profile hook changes do not change sibling profile', all(h['owned'] for h in read('hooks', {'profile_id': second['id']})['hooks']))
    check('provider-proven account usage is not double-counted', len(read('usage')['profiles']) == 1)
    session = change('launch', {'profile_id': first['id'], 'revision': current()['revision'], 'cwd': env['VT_BROWSE_ROOTS']})
    status = None
    for _ in range(30):
        status = read('session-status', {'session': session['session']})
        if status['state'] == 'running':
            break
        time.sleep(.2)
    check('real tmux process and verified lifecycle event agree', status['state'] == 'running')
    check('started profile revision is pinned', status['applied_revision'] == current()['revision'])

    # Write-only secret edits, rollback and test-to-work conversion use the same
    # actor-bound HTTP review/apply path as the product UI.
    before = (state / second['id'] / 'claude/settings.json').read_bytes()
    patch = {'env': {'ANTHROPIC_AUTH_TOKEN': 'sandbox-private-api-sentinel'}, 'base_url': 'https://proxy.example.invalid'}
    plan = read('preview', {'operation': 'provider-edit', 'payload': {'profile_id': second['id'], 'revision': second['revision'], 'patch': patch}})
    check('review never returns submitted API secret', 'sandbox-private-api-sentinel' not in json.dumps(plan))
    code, edited = api('', {'token': plan['token']}, apply=True)
    assert code == 200, edited
    row = next(p for p in read('list')['profiles'] if p['id'] == second['id'])
    check('listing never returns API secret', 'sandbox-private-api-sentinel' not in json.dumps(read('list')))
    private = state / second['id'] / 'claude/.farshell-provider.json'
    check('API configuration has private permissions', private.stat().st_mode & 0o777 == 0o600)
    change('rollback', {'profile_id': second['id'], 'revision': row['revision'], 'job_id': next(j['id'] for j in read('list')['jobs'] if j['profile_id'] == second['id'] and j['kind'] == 'provider-edit')})
    check('API rollback restores original native configuration', not private.exists() and (state / second['id'] / 'claude/settings.json').read_bytes() == before)
    row = next(p for p in read('list')['profiles'] if p['id'] == second['id'])
    credentials = (state / second['id'] / 'claude/.credentials.json').read_bytes()
    change('adopt', {'profile_id': second['id'], 'revision': row['revision']})
    row = next(p for p in read('list')['profiles'] if p['id'] == second['id'])
    check('adoption preserves native login and root', row['purpose'] == 'work' and (state / second['id'] / 'claude/.credentials.json').read_bytes() == credentials)
    code, _ = api('preview', {'operation': 'refresh-test', 'payload': {'profile_id': second['id'], 'revision': row['revision']}})
    check('adopted work profile cannot force-expire login', code == 409)
    code, _ = api('preview', {'operation': 'provider-edit', 'payload': {'profile_id': first['id'], 'revision': current()['revision'], 'patch': patch}})
    check('live profile refuses authentication edits', code == 409)

    # Verify the actual Seatbelt policy, not only environment-variable isolation.
    if sys.platform == 'darwin':
        probe = folder / 'isolation-probe.py'
        probe.write_text('''
import json, os, socket, sys
from pathlib import Path
out={}
try:
    Path(sys.argv[1]).write_text('must-not-write')
    out['outside_write_denied']=False
except PermissionError:
    out['outside_write_denied']=True
try:
    Path(sys.argv[2]).stat()
    out['production_credentials_read_denied']=False
except PermissionError:
    out['production_credentials_read_denied']=True
try:
    s=socket.socket(); s.settimeout(1); s.connect(('1.1.1.1',443))
    out['external_network_denied']=False
except PermissionError:
    out['external_network_denied']=True
print(json.dumps(out))
''')
        outside = folder.parent / ('outside-' + folder.name)
        response = subprocess.run(['sandbox-exec', '-f', str(folder / 'sandbox.sb'), sys.executable, str(probe), str(outside), str(Path.home() / '.clauth')], env=env, capture_output=True, text=True, check=True)
        for name, passed in json.loads(response.stdout).items():
            check(name, passed)

    if args.browser:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            executable = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
            browser = playwright.chromium.launch(headless=True, **({'executable_path': str(executable)} if executable.exists() else {}))
            for language in ('ko', 'en'):
                for width, height in ((390, 844), (1440, 1000)):
                    context = browser.new_context(viewport={'width': width, 'height': height})
                    context.add_cookies([{'name': 'vt_session', 'value': metadata['cookie'], 'url': base}])
                    context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(base + '/') else route.abort())
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base + '/profile-sandbox?lang=' + language)
                    label = '실행 프로필' if language == 'ko' else 'Launch profiles'
                    page.get_by_label(label, exact=True).wait_for()
                    page.get_by_label(label, exact=True).select_option(first['id'])
                    login = '로그인 상태·방법' if language == 'ko' else 'Login status and instructions'
                    page.get_by_role('button', name=login, exact=True).click()
                    page.locator('pre').filter(has_text='profile login').wait_for()
                    check('isolated login instructions ' + language + '/' + str(width), env['VT_STATE_DIR'] in page.locator('pre').inner_text())
                    check('no horizontal page overflow ' + language + '/' + str(width), page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    page.keyboard.press('Tab')
                    check('keyboard focus ' + language + '/' + str(width), page.evaluate('document.activeElement !== document.body'))
                    page.screenshot(path=str(folder / (language + '-' + str(width) + '.png')), full_page=True)
                    check('no browser errors ' + language + '/' + str(width), not errors)
                    context.close()
            browser.close()
    (folder / 'verification.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps({'passed': len(evidence['checks']), 'real_oauth_tested': False, 'evidence': str(folder / 'verification.json')}))


if __name__ == '__main__':
    main()
