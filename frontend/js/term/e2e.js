// E2E 암호화(NaCl) — F4에서 terminal.js(구 :15-153)에서 분리. 독립성이 가장
// 높은 블록이라 분할 1순위였다. window.nacl은 index.html이 ?e2e=1일 때만
// document.write로 조건부 로드하는 classic vendor script라 여기서도 계속
// bare `nacl`로 읽는다(모듈로 감싸지 않음 — core/vendor.js를 새로 만들 이유가
// 아직 없다는 F1의 판단을 그대로 따른다).
import { VT_TOKEN } from '../core/env.js';

const _urlParams = new URLSearchParams(location.search);
const _hashParams = new URLSearchParams(location.hash.slice(1));

// E2E 암호화 활성화 — URL에 ?e2e=1 또는 #e2e=1 있으면 ON (D3)
export const E2E_ENABLED = (_urlParams.get('e2e') === '1' || _hashParams.get('e2e') === '1');

// WS 경로용 쿼리 문자열을 호출 시점의 VT_TOKEN/E2E_ENABLED로 항상 새로
// 조립한다. URLSearchParams를 쓰므로 구분자(?/&)를 손으로 이어붙이다
// 상태가 어긋나는 문제가 구조적으로 재발하지 않는다.
export function _wsQuery() {
  const params = new URLSearchParams();
  if (VT_TOKEN) params.set('token', VT_TOKEN);
  if (E2E_ENABLED) params.set('e2e', '1');
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

// base64url ↔ Uint8Array
function _b64uDec(s) {
  const pad = '='.repeat((4 - s.length % 4) % 4);
  return nacl.util.decodeBase64(s.replace(/-/g, '+').replace(/_/g, '/') + pad);
}
function _b64uEnc(bytes) {
  return nacl.util.encodeBase64(bytes).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// TOFU 핀닝: 서버 장기 identity 공개키를 호스트별로 localStorage에 저장.
// Fix 1 (능동 MITM 방어) — ephemeral 키(nacl.box)는 세션마다 바뀌므로 그 자체로는
// "진짜 서버"임을 증명하지 못한다. 서버가 안정적인 Ed25519 identity 키로 ephemeral
// 공개키에 서명해 보내면, 클라이언트는 그 서명을 검증하고 identity_pub을 첫 접속 때
// 신뢰(Trust On First Use)한 뒤 이후 접속마다 같은 키인지 대조한다.
function _e2eTofuKey() {
  return `vt_e2e_identity_pub:${location.hostname}`;
}
function _e2eGetPinned() {
  try { return localStorage.getItem(_e2eTofuKey()); } catch { return null; }
}
function _e2eSetPinned(identityPub) {
  try { localStorage.setItem(_e2eTofuKey(), identityPub); } catch { /* localStorage 불가 — 핀닝 생략 */ }
}
// 사용자가 재신뢰를 명시적으로 승인했을 때만 핀을 갱신한다 (silent fallback 금지).
function _e2eConfirmRetrust(newIdentityPub) {
  return window.confirm(
    '⚠️ 이 서버의 E2E 암호화 identity 키가 이전과 다릅니다.\n' +
    '서버를 재설치했거나 키를 재발급했다면 정상이지만, 중간자 공격(MITM)일 수도 있습니다.\n\n' +
    '새 키를 신뢰하고 계속하시겠습니까? (신뢰하지 않으면 연결을 종료합니다)'
  );
}

// E2E WebSocket 래퍼 — 핸드셰이크 후 encrypt/decrypt 자동 처리
// onReady(handle) 에서 handle은 { send(bytes), close(), readyState } 형태 (원본 WS 인터페이스와 호환)
// onE2EError(msg)가 있으면 핸드셰이크 실패(서명 불일치/재신뢰 거부) 시 호출된다.
export function wrapE2E(ws, onReady, onData, onE2EError) {
  if (!E2E_ENABLED) {
    ws.addEventListener('message', (e) => {
      if (e.data instanceof ArrayBuffer) onData(new Uint8Array(e.data));
    });
    onReady({
      send: (bytes) => ws.send(bytes),
      close: () => ws.close(),
      get readyState() { return ws.readyState; },
    });
    return;
  }
  const fail = (msg) => {
    console.error('[E2E]', msg);
    try { ws.close(); } catch {}
    if (typeof onE2EError === 'function') onE2EError(msg);
    else alert('E2E 암호화 핸드셰이크 실패: ' + msg);
  };
  let sharedKey = null;
  ws.addEventListener('message', (e) => {
    // 핸드셰이크: 첫 텍스트로 서버 공개키 수신
    if (typeof e.data === 'string' && !sharedKey) {
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg.type !== 'e2e-hello' || !msg.pub) return;

      // identity_pub/sig가 없는 서버(구버전) — 서명 검증 없이 진행하면 능동 MITM을
      // 막을 수 없으므로 명시적으로 거부한다. 조용한 평문 폴백은 하지 않는다.
      if (!msg.identity_pub || !msg.sig) {
        fail('서버가 identity 서명을 보내지 않았습니다 (구버전 서버 또는 변조된 응답)');
        return;
      }
      let ephemeralPub, identityPub, sig;
      try {
        ephemeralPub = _b64uDec(msg.pub);
        identityPub = _b64uDec(msg.identity_pub);
        sig = _b64uDec(msg.sig);
      } catch {
        fail('e2e-hello 페이로드 디코딩 실패');
        return;
      }
      // Ed25519 서명 검증: identity_pub이 실제로 이 ephemeral 공개키에 서명했는가.
      if (!nacl.sign.detached.verify(ephemeralPub, sig, identityPub)) {
        fail('서명 검증 실패 — ephemeral 공개키가 identity 키로 서명되지 않았습니다 (MITM 의심)');
        return;
      }
      // TOFU 핀닝: 첫 접속이면 신뢰하고 저장, 이후 접속은 대조.
      const pinned = _e2eGetPinned();
      if (!pinned) {
        _e2eSetPinned(msg.identity_pub);
      } else if (pinned !== msg.identity_pub) {
        if (_e2eConfirmRetrust(msg.identity_pub)) {
          _e2eSetPinned(msg.identity_pub);
        } else {
          fail('identity 키 변경을 사용자가 거부했습니다 — 연결을 중단합니다');
          return;
        }
      }

      const kp = nacl.box.keyPair();
      const serverPub = ephemeralPub;
      // nacl.box.before() == PyNaCl Box.shared_key() (둘 다 crypto_box_beforenm)
      sharedKey = nacl.box.before(serverPub, kp.secretKey);
      ws.send(JSON.stringify({ type: 'e2e-ack', pub: _b64uEnc(kp.publicKey) }));
      onReady({
        send: (bytes) => {
          if (ws.readyState !== WebSocket.OPEN) return;
          const buf = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
          const nonce = nacl.randomBytes(nacl.secretbox.nonceLength);
          const ct = nacl.secretbox(buf, nonce, sharedKey);
          const wire = new Uint8Array(nonce.length + ct.length);
          wire.set(nonce, 0); wire.set(ct, nonce.length);
          ws.send(wire);
        },
        close: () => ws.close(),
        get readyState() { return ws.readyState; },
      });
      return;
    }
    // 암호화된 바이트 메시지
    if (e.data instanceof ArrayBuffer && sharedKey) {
      const wire = new Uint8Array(e.data);
      const nonce = wire.slice(0, nacl.secretbox.nonceLength);
      const ct = wire.slice(nacl.secretbox.nonceLength);
      const pt = nacl.secretbox.open(ct, nonce, sharedKey);
      if (pt) onData(pt);
      else console.warn('E2E decrypt failed');
    }
  });
}
