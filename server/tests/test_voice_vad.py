"""voice_vad.EnergyVad — 합성 PCM(사인파 발화 + 무음 + 잡음 바닥)으로 발화 확정·끝점·오판 방향을 잠근다."""
import array
import math
import random

import pytest

import voice_vad as vv

SR = 16000


def tone(sec, amp=0.3, freq=220.0):
    """발화 대용: 사인파(0.3 ≈ -13dBFS RMS)."""
    n = int(sec * SR)
    return [amp * math.sin(2 * math.pi * freq * i / SR) for i in range(n)]


def noise(sec, amp=0.002, seed=1):
    """잡음 바닥: 가우스 잡음(0.002 ≈ -54dBFS RMS). 시드 고정이라 결정적."""
    rnd = random.Random(seed)
    return [rnd.gauss(0.0, amp) for _ in range(int(sec * SR))]


def mix(a, b):
    n = max(len(a), len(b))
    return [(a[i] if i < len(a) else 0.0) + (b[i] if i < len(b) else 0.0) for i in range(n)]


def pcm(samples):
    out = array.array("h", (max(-32768, min(32767, int(round(v * 32767)))) for v in samples))
    return out.tobytes()   # 테스트 머신은 리틀 엔디언(서버 코드는 빅 엔디언도 처리)


def feed(vad, samples, chunk_sec=0.5):
    """0.5초 조각(=클라이언트 청크 크기)으로 나눠 넣는다. 각 조각 뒤 (audio_ms, endpoint) 기록."""
    data = pcm(samples)
    step = int(chunk_sec * SR) * 2
    trace = []
    for i in range(0, len(data), step):
        vad.feed(data[i:i + step])
        trace.append((vad.audio_ms, vad.confirmed, vad.endpoint))
    return trace


def test_silence_and_noise_never_confirm_or_endpoint():
    for samples in (noise(10), [0.0] * (10 * SR), noise(10, amp=0.02, seed=3)):   # 잡음 바닥·디지털 무음·시끄러운 방
        v = vv.EnergyVad()
        feed(v, samples)
        assert not v.confirmed and not v.endpoint and v.speech_ms == 0


def test_speech_then_silence_gives_endpoint_after_one_second():
    v = vv.EnergyVad()
    feed(v, noise(0.5) + mix(tone(1.0), noise(1.0, seed=2)))
    assert v.confirmed and not v.endpoint
    feed(v, noise(0.9, seed=4))
    assert not v.endpoint                                 # 0.9초 침묵 — 아직
    feed(v, noise(0.2, seed=5))
    assert v.endpoint                                     # 1초 넘김
    feed(v, mix(tone(0.5), noise(0.5, seed=6)))
    assert v.endpoint                                     # 한 번 끝점이면 유지(클라이언트가 이미 멈추는 중)


def test_endpoint_timing_is_audio_time_and_chunking_independent():
    seq = noise(0.5) + mix(tone(1.0), noise(1.0, seed=2)) + noise(1.5, seed=3)
    data = pcm(seq)
    ends = []
    for step in (16000, 4161, 40, 96001):                 # 조각 크기가 달라도(홀수 바이트·프레임 경계 넘는 나머지 포함) 같은 판정
        v = vv.EnergyVad()
        for i in range(0, len(data), step):
            v.feed(data[i:i + step])
        ends.append(v.endpoint_at_ms)
    assert ends[0] > 0 and len(set(ends)) == 1            # 큰 조각 하나에 끝점이 들어 있어도 같은 시각에 잡힌다
    assert 1500 + vv.ENDPOINT_SILENCE_MS <= ends[0] <= 1500 + vv.ENDPOINT_SILENCE_MS + 60   # 말이 1.5초에 끝남


def test_short_clicks_do_not_confirm():
    v = vv.EnergyVad()
    clicks = []
    for k in range(10):                                   # 100ms 소리 + 400ms 조용 반복(기침·책상 두드림)
        clicks += mix(tone(0.1), noise(0.1, seed=10 + k)) + noise(0.4, seed=30 + k)
    feed(v, noise(0.3) + clicks)
    assert not v.confirmed and not v.endpoint


def test_no_endpoint_without_speech_even_after_long_silence():
    v = vv.EnergyVad()
    feed(v, noise(59))
    assert not v.endpoint


def test_pauses_shorter_than_threshold_do_not_end():
    v = vv.EnergyVad()
    s = noise(0.3)
    for k in range(8):                                    # 말 0.8초 + 숨 고르기 0.7초 반복
        s += mix(tone(0.8), noise(0.8, seed=50 + k)) + noise(0.7, seed=70 + k)
    trace = feed(v, s)
    assert v.confirmed and not any(e for _, _, e in trace)
    feed(v, noise(1.2, seed=99))
    assert v.endpoint


def test_long_continuous_speech_never_endpoints():
    """60초 내내 말해도 플로어가 말소리까지 올라가 침묵으로 오판하지 않는다(말하는 중 종료 금지)."""
    v = vv.EnergyVad()
    feed(v, noise(0.3) + mix(tone(59.0, amp=0.1), noise(59.0, seed=8)))
    assert v.confirmed and not v.endpoint
    assert vv._db(v.floor) < vv._db(0.1 / math.sqrt(2)) - vv.SPEECH_MARGIN_DB


def test_noisy_room_calibrates_floor():
    """-35dBFS 잡음 방: 보정 없이는 잡음이 곧바로 발화로 확정된다. 보정 후엔 잡음은 아니고 말은 발화다."""
    room = 0.018
    v = vv.EnergyVad()
    feed(v, noise(3.0, amp=room, seed=11))
    assert not v.confirmed
    feed(v, mix(tone(1.0, amp=0.3), noise(1.0, amp=room, seed=12)))
    assert v.confirmed
    feed(v, noise(1.2, amp=room, seed=13))
    assert v.endpoint


def test_quiet_speech_below_absolute_floor_is_ignored():
    v = vv.EnergyVad()
    feed(v, [0.0] * (SR // 2) + tone(2.0, amp=0.006))     # 약 -47dBFS: 디지털 무음 직후라도 발화로 보지 않는다
    assert not v.confirmed


def test_calibration_minimum_tolerates_speech_starting_early():
    v = vv.EnergyVad()
    feed(v, noise(0.1) + mix(tone(1.5), noise(1.5, seed=2)))   # 보정 중간에 말 시작
    assert v.confirmed
    feed(v, noise(1.1, seed=3))
    assert v.endpoint


@pytest.mark.parametrize("n", [0, 1, 3, 639, 641])
def test_odd_sized_feeds_are_buffered(n):
    v = vv.EnergyVad()
    v.feed(b"\x00" * n)
    assert v.audio_ms == (n // vv.FRAME_BYTES) * vv.FRAME_MS


def test_noise_step_up_fails_safe_without_endpoint():
    """말한 뒤 잡음이 문턱 위로 계단처럼 커지면 끝점을 내지 않는다(잘못 끊기보다 못 끊는 쪽 — 문서화한 대가)."""
    v = vv.EnergyVad()
    feed(v, noise(0.5) + mix(tone(1.0), noise(1.0, seed=2)) + noise(5.0, amp=0.03, seed=21))
    assert v.confirmed and not v.endpoint
