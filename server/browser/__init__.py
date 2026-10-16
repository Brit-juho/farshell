"""브라우저 broker — 에이전트가 격리된 브라우저 탭을 열고 조작하는 길(`VT_AUTHZ_ENFORCE` 의 `browser`, 기본 꺼짐). 규칙은 `broker.py` 머리말, 문서는 `docs/ref/auth.md` «Browser broker».

이 패키지를 import 해도 playwright 같은 선택 의존성이나 브라우저를 부르지 않는다(어댑터는 지연 import).
"""
