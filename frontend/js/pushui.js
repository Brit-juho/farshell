// 푸시 알림 토글 UI (P5) — ⋯ 메뉴의 "푸시 알림" 항목.
// 구독 로직 자체는 swreg.js(window.VTPush)에 있다.

    async function _refreshPushLabel() {
      const label = document.getElementById('push-label');
      const btn = document.getElementById('push-btn');
      if (!label || !window.VTPush) return;

      // 구독이 불가능한 환경이면 이유를 그대로 보여준다.
      // "왜 안 되는지 모르겠는 알림"이 제일 나쁘다.
      const reason = VTPush.blockReason();
      if (reason) {
        label.textContent = '푸시 알림 — 사용 불가';
        if (btn) { btn.title = reason; btn.classList.add('disabled'); }
        return;
      }
      const on = await VTPush.isSubscribed();
      label.textContent = on ? '푸시 알림 ✓' : '푸시 알림';
      if (btn) {
        btn.title = on
          ? '켜져 있습니다 — 앱을 닫아도 작업 완료 알림이 옵니다 (눌러서 끄기)'
          : '앱을 닫아도 작업 완료 알림을 받습니다';
        btn.classList.remove('disabled');
      }
    }

    async function togglePush() {
      if (!window.VTPush) return;
      const reason = VTPush.blockReason();
      if (reason) { showToast(reason); return; }

      const on = await VTPush.isSubscribed();
      try {
        if (on) {
          await VTPush.unsubscribe();
          showToast('푸시 알림 껐습니다');
        } else {
          const r = await VTPush.subscribe();
          showToast(r.ok ? '푸시 알림 켰습니다' : (r.reason || '구독 실패'));
        }
      } catch (e) {
        showToast(`푸시 설정 실패: ${e.message}`);
      }
      _refreshPushLabel();
    }

    // capabilities 로 서버쪽 지원 여부를 확인해 진입점을 숨긴다(grid.js가 .needs-push 처리).
    // 여기서는 라벨 초기화만 한다.
    (function () {
      const init = () => setTimeout(_refreshPushLabel, 300);
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
      } else {
        init();
      }
    })();
