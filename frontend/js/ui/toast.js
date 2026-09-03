// 통합 토스트 — picker.js(타입 지원·스택)와 grid.js(싱글톤·교체) 두 벌을 하나로.
//
// 왜 합쳤나 (실버그):
//   같은 이름의 showToast 가 picker.js:71 과 grid.js:377 에 각각 있었고,
//   bootstrap manifest 순서상 grid.js(8번째)가 picker.js(6번째)를 덮어썼다.
//   grid.js 판은 두 번째 인자(type)를 아예 받지 않아서, picker/terminal/snippets
//   11곳이 넘기던 'error'/'success' 가 조용히 버려졌다 — 에러 토스트가 중립색으로
//   떴다. 게다가 grid.js 판은 #agent-toast 싱글톤이라 연속 토스트가 서로를 덮어썼다.
//
// key 옵션이 있는 이유:
//   에이전트 도구 이벤트(grid.js)는 아주 잦아서 그대로 쌓으면 화면이 토스트로 덮인다.
//   같은 key 를 가진 토스트는 새 것이 옛 것을 제자리에서 교체하고(= 옛 싱글톤 동작),
//   key 가 없으면 세로로 쌓인다(= 옛 picker 동작). 두 동작이 다 필요했던 것이지
//   한쪽이 틀렸던 게 아니다.
//
// 배치는 #vt-toasts 컨테이너가 맡는다(app.css). voice.js 의 showNotification 은
// 아직 body 에 .vt-toast 를 직접 붙이므로, .vt-toast 자체의 fixed 배치는 남겨두고
// 컨테이너 안에 들어온 것만 정적 배치로 되돌린다. (통합은 D6에서)
(function () {
    'use strict';

    var TYPE_CLASS = { info: 'info', error: 'err', success: 'ok' };
    var DEFAULT_MS = 4000;
    var keyed = new Map();   // key -> element (제자리 교체용)

    function container() {
        var c = document.getElementById('vt-toasts');
        if (!c) {
            c = document.createElement('div');
            c.id = 'vt-toasts';
            // 터치 기기는 screenReaderMode 가 기본 ON 인데(M5) 정작 알림이 안 읽히던
            // 문제가 있었다. polite 로 두고, 에러만 개별 요소에서 alert 로 승격한다.
            c.setAttribute('aria-live', 'polite');
            document.body.appendChild(c);
        }
        return c;
    }

    function dismiss(el) {
        if (!el) return;
        clearTimeout(el._vtTimer);
        if (el._vtKey && keyed.get(el._vtKey) === el) keyed.delete(el._vtKey);
        if (el.isConnected) el.remove();
    }

    // showToast(메시지, 'info'|'error'|'success', { key, duration })
    //   key      — 같은 key 의 기존 토스트를 제자리에서 교체 (없으면 새로 쌓음)
    //   duration — ms. 0 이하면 자동으로 사라지지 않음
    function showToast(msg, type, opts) {
        type = type || 'info';
        opts = opts || {};
        var key = opts.key || null;
        var ms = (typeof opts.duration === 'number' && isFinite(opts.duration))
            ? opts.duration : DEFAULT_MS;

        var el = key ? keyed.get(key) : null;
        if (el && !el.isConnected) { keyed.delete(key); el = null; }
        if (!el) {
            el = document.createElement('div');
            if (key) { el._vtKey = key; keyed.set(key, el); }
            container().appendChild(el);
        }

        el.className = 'vt-toast ' + (TYPE_CLASS[type] || 'info');
        el.textContent = msg == null ? '' : String(msg);
        el.setAttribute('role', type === 'error' ? 'alert' : 'status');

        clearTimeout(el._vtTimer);
        if (ms > 0) el._vtTimer = setTimeout(function () { dismiss(el); }, ms);
        return el;
    }

    window.showToast = showToast;
    window.dismissToast = dismiss;
})();
