"""선택 음성 의존성이 없는 core 설치의 API 동작."""

import asyncio

from fastapi.responses import JSONResponse

from routes import voice


def test_local_mic_endpoints_report_unavailable_without_optional_dependencies(monkeypatch):
    """core 프로필에서도 서버는 기동하고, local-mic만 503으로 명확히 거절한다."""
    monkeypatch.setattr(voice.local_mic, "available", lambda: False)

    start = asyncio.run(voice.local_mic_start())
    stop = asyncio.run(voice.local_mic_stop(None))

    for response in (start, stop):
        assert isinstance(response, JSONResponse)
        assert response.status_code == 503
        assert b"local_mic_unavailable" in response.body
