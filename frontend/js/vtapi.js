// 패널 공용 fetch 래퍼 — viewer.js/ports.js/queue.js가 각자 만들던
// _api/_ptApi/_qApi 를 하나로 합친 것. 셋 다 규칙이 같았다:
// 토큰 쿼리 부착 + {error,reason} 형태의 서버 에러를 Error로 통일.
//
// grid.js 뒤에 로드되므로 API_BASE / _tokenQuery 를 그대로 참조한다
// (classic script 최상위 스코프 공유 — viewer.js 원래 주석과 동일한 계약).

    async function vtFetch(path, opts) {
      const sep = path.includes('?') ? '&' : '?';
      const res = await fetch(`${API_BASE}${path}${sep}${_tokenQuery.replace(/^[?&]/, '')}`, opts);
      let data = null;
      try { data = await res.json(); } catch (_) { data = null; }
      if (!res.ok) {
        const e = new Error((data && (data.reason || data.error)) || `HTTP ${res.status}`);
        e.status = res.status; e.data = data;
        throw e;
      }
      return data;
    }

    // 서버가 준 문자열은 절대 innerHTML 로 넣지 않는다 — textContent 경유로 이스케이프.
    function vtEsc(s) {
      const d = document.createElement('div');
      d.textContent = s == null ? '' : String(s);
      return d.innerHTML;
    }
