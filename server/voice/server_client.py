"""데몬은 서버 STT를 공유한다. 실패·응답 유실을 독립 입력으로 재시도하지 않는다."""
import json
import os
import fsh_client
from .config import vt_getenv, _VT_ENV
from i18n import tr


def independent():
    return vt_getenv('VT_VOICE_STANDALONE', 'off').lower() == 'on'


def request(path, body=b'', content_type='application/json', extra=None, timeout=8):
    port = vt_getenv('VT_PORT', '7777')
    base = vt_getenv('VT_SERVER', f'http://127.0.0.1:{port}')
    token = (_VT_ENV.get('VT_AUTH_TOKEN') or _VT_ENV.get('VT_TOKEN')
             or os.environ.get('VT_AUTH_TOKEN') or os.environ.get('VT_TOKEN') or '')
    # 소켓 우선(svc:voice), 소켓이 없으면 TCP + 토큰. 4xx/5xx 는 urlopen 과 같이 HTTPError.
    _, raw = fsh_client.call('POST', path, body=body, headers={'Content-Type': content_type, **(extra or {})},
                             timeout=timeout, svc='voice', server=base, token=token, port=port,
                             raise_for_status=True)
    return json.loads(raw.decode('utf-8'))


def target(pane):
    result = request('/voice/target', json.dumps({'pane': pane}).encode())
    ready = request('/voice/stt/preload', timeout=130)
    if not ready.get('loaded'):
        raise RuntimeError(ready.get('error') or tr('음성 인식 모델을 먼저 준비해 주세요'))
    request('/voice/cancel', json.dumps({'server': True, 'pause': True}).encode())
    return result
