"""실제 pane 셸에서 음성 대화 ID를 연결한다."""
import json
import os
import sys
import urllib.request
import vt_env
from i18n import tr

cfg = vt_env.load()
token = cfg.get('VT_AUTH_TOKEN') or cfg.get('VT_TOKEN') or os.environ.get('VT_AUTH_TOKEN') or ''
body = {'tool': sys.argv[1], 'run': sys.argv[2], 'pane': os.environ.get('TMUX_PANE'), 'tmux': os.environ.get('TMUX')}
base = os.environ.get('VT_SERVER') or 'http://127.0.0.1:' + str(cfg.get('VT_PORT') or '7777')
req = urllib.request.Request(base + '/voice/bind', data=json.dumps(body).encode(),
                             headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
try:
    with urllib.request.urlopen(req, timeout=5) as r:
        result = json.load(r)
    print(tr('음성 연결: {name} {pane}', name=result['target']['name'], pane=result['target']['pane']))
except Exception as e:
    print(tr('음성 연결 실패: {e}', e=e), file=sys.stderr)
    sys.exit(1)
