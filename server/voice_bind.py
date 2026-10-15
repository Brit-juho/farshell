"""실제 pane 셸에서 음성 대화 ID를 연결한다."""
import json
import os
import sys
import fsh_client
import vt_env
from i18n import tr

cfg = vt_env.load()
token = cfg.get('VT_AUTH_TOKEN') or cfg.get('VT_TOKEN') or os.environ.get('VT_AUTH_TOKEN') or ''
body = {'tool': sys.argv[1], 'run': sys.argv[2], 'pane': os.environ.get('TMUX_PANE'), 'tmux': os.environ.get('TMUX')}
port = cfg.get('VT_PORT') or os.environ.get('VT_PORT') or '7777'
base = os.environ.get('VT_SERVER') or 'http://127.0.0.1:' + str(port)
try:
    # pane 안에서 도는 프로세스라 소켓에서는 agent 로 식별된다(svc:voice 는 귀속 표기). 소켓이 없으면 TCP + 토큰.
    _, raw = fsh_client.call('POST', '/voice/bind', body=body, timeout=5, svc='voice', server=base, token=token,
                             port=port, raise_for_status=True)
    result = json.loads(raw.decode('utf-8'))
    print(tr('음성 연결: {name} {pane}', name=result['target']['name'], pane=result['target']['pane']))
except Exception as e:
    print(tr('음성 연결 실패: {e}', e=e), file=sys.stderr)
    sys.exit(1)
