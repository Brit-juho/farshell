"""서버 VAD — 적응형 에너지(RMS + 노이즈 플로어)로 발화 시작·끝을 판정한다. 순수 클래스, 외부 의존성 없음.

입력은 16kHz mono PCM16 LE(voice_chunks와 같은 형식)를 **seq 순서대로 이어 붙은 만큼** 받는다. 시간은 벽시계가 아니라
받은 오디오 길이(샘플 수)로 센다 — 네트워크가 막혔다가 조각이 몰려 와도 판정이 같고, 테스트가 결정적이다.

판정(프레임 20ms 단위):
- 처음 CALIBRATION_MS는 보정 구간: 발화로 세지 않고, 그 구간 프레임 RMS의 최솟값을 노이즈 플로어로 삼는다. 시끄러운 방
  (예: -35dBFS 환풍기)에서 초깃값(-50dBFS)이 낮아 잡음을 곧바로 발화로 확정하는 것을 막는다. 최솟값이라 보정 중에
  말을 시작해도 앞쪽 조용한 프레임이 플로어가 된다. 탭하고 말을 시작하기까지 보통 이보다 길다.
- 프레임 RMS가 `노이즈 플로어 + SPEECH_MARGIN_DB` 이상이고 `SPEECH_ABS_MIN_DB` 이상이면 발화 프레임.
- 노이즈 플로어(선형 진폭): 조용해지면 빠르게(FLOOR_FALL_MS) 내려가고, 발화가 아닌 프레임에서만 FLOOR_RISE_MS로
  천천히 올라간다. **발화 프레임에서는 올리지 않는다** — 60초 시정수로 올려도 -23dBFS로 계속 말하면 약 25초 뒤
  플로어가 문턱을 넘어 말하는 중에 끝점이 났다(테스트 test_long_continuous_speech_never_endpoints). 대가: 잡음이
  9dB 넘게 한꺼번에 커지면(문턱 위로 계단 상승) 그 잡음을 계속 발화로 봐 끝점이 나지 않는다 — 사용자가 직접
  종료하면 되는, 안전한 쪽의 실패다. 상·하한은 frontend/js/voice/meter.js와 같다.
- 발화 확정: 발화 프레임이 SPEECH_CONFIRM_MS 이상 쌓이면(사이 끊김이 ONSET_GAP_MS를 넘으면 처음부터) 확정. 한 번 확정되면 유지.
- 끝점: 확정 뒤 비발화 프레임이 ENDPOINT_SILENCE_MS 이상 이어지면 끝점. 발화가 확정된 적 없으면 끝점도 없다
  (말을 안 하면 자동 종료하지 않는다 — 60초 상한은 수집기가 그대로 지킨다). 한 번 끝점이면 유지.

오판의 방향: 잘못 끊는 것(말하는 중 종료)이 못 끊는 것(사용자가 직접 종료)보다 나쁘다. 그래서 값이 애매하면
끝점이 늦어지는 쪽으로 정했다.
"""
from __future__ import annotations

import array
import math
import sys

SAMPLE_RATE = 16000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000          # 320
FRAME_BYTES = FRAME_SAMPLES * 2

# 노이즈 플로어(선형 진폭 0~1). meter.js의 FLOOR_MIN/MAX/INIT과 같은 값 — 두 곳이 같은 방을 같은 수준으로 본다.
FLOOR_MIN = 0.0005            # 약 -66dBFS. 디지털 무음(0)에 플로어가 박혀 작은 잡음이 전부 발화가 되는 것을 막는다
FLOOR_MAX = 0.05              # 약 -26dBFS. 시끄러운 방이라도 이보다 큰 소리는 잡음으로 보지 않는다
FLOOR_INIT = 0.003            # 약 -50dBFS. 녹음 첫 프레임부터 말해도 발화로 잡히는 보통 방 잡음 수준
FLOOR_FALL_MS = 300.0         # 조용해지면 0.3초 시정수로 따라 내려간다(한 프레임 무음에 쏠리지 않을 만큼만)
FLOOR_RISE_MS = 15000.0       # 비발화 프레임에서 잡음이 커지면 15초 시정수로 따라 올라간다(meter.js와 같다)

# 발화 프레임 문턱. 정상 잡음의 프레임 간 RMS 흔들림은 대개 ±3dB 안이고, 가까이서 말한 소리는 방 잡음보다 15~30dB 크다.
# 9dB는 그 사이 — 잡음 흔들림은 넘지 못하고 작게 말한 소리는 넘는다(meter.js가 0으로 보는 6dB보다 조금 엄격).
SPEECH_MARGIN_DB = 9.0
# 플로어와 무관한 하한. 휴대폰 마이크에 팔 길이에서 작게 말해도 -40dBFS 안팎이라 -45는 넘는다. 디지털 무음 직후의
# 아주 작은 소리(플로어 -66 + 9 = -57dB)가 발화로 잡히지 않게 한다.
SPEECH_ABS_MIN_DB = -45.0

CALIBRATION_MS = 200
SPEECH_CONFIRM_MS = 300       # 짧은 소리(기침·책상 두드림·키보드)를 발화로 확정하지 않는 길이 — 한 음절보다 길다
ONSET_GAP_MS = 200            # 확정 전 발화 프레임 사이 이 이상 끊기면 다시 센다(음절 사이 짧은 틈은 허용)
ENDPOINT_SILENCE_MS = 1000    # 확정 뒤 이만큼 조용하면 끝점. 문장 사이 숨 고르기(0.3~0.7초)보다 길다


def _db(x: float) -> float:
    return 20.0 * math.log10(max(x, 1e-9))


def frame_rms(frame: "array.array") -> float:
    """PCM16 샘플 배열의 RMS(0~1). 빈 배열은 0."""
    n = len(frame)
    if not n:
        return 0.0
    return math.sqrt(sum(v * v for v in frame) / n) / 32768.0


class EnergyVad:
    """조각을 순서대로 feed()하면 confirmed(발화 확정)·endpoint(끝점)·speech_ms가 갱신된다."""

    __slots__ = ("floor", "confirmed", "endpoint", "speech_ms", "audio_ms", "_onset_ms", "_gap_ms",
                 "_silence_ms", "_rest", "confirmed_at_ms", "endpoint_at_ms", "_calib")

    def __init__(self):
        self.floor = FLOOR_INIT
        self.confirmed = False
        self.endpoint = False
        self.speech_ms = 0            # 지금까지 발화 프레임 합(부분 인식이 "새 발화가 있었나"를 볼 때 쓴다)
        self.audio_ms = 0             # 처리한 오디오 길이
        self.confirmed_at_ms = -1     # 발화를 확정한 오디오 시각(ms)
        self.endpoint_at_ms = -1      # 끝점을 낸 오디오 시각(ms)
        self._onset_ms = 0
        self._gap_ms = 0
        self._silence_ms = 0
        self._rest = b""              # 프레임을 못 채운 나머지 바이트(다음 조각 앞에 붙인다)
        self._calib: "float | None" = None   # 보정 구간의 최소 RMS

    def feed(self, pcm: bytes) -> None:
        data = self._rest + pcm if self._rest else pcm
        usable = len(data) - len(data) % FRAME_BYTES
        self._rest = bytes(data[usable:])
        if not usable:
            return
        samples = array.array("h")
        samples.frombytes(bytes(data[:usable]))
        if sys.byteorder == "big":           # PCM16 LE를 네이티브 순서로
            samples.byteswap()
        for i in range(0, len(samples), FRAME_SAMPLES):
            self._frame(frame_rms(samples[i:i + FRAME_SAMPLES]))

    def _frame(self, rms: float) -> None:
        dt = FRAME_MS
        self.audio_ms += dt
        if self.audio_ms <= CALIBRATION_MS:
            self._calib = rms if self._calib is None else min(self._calib, rms)
            if self.audio_ms + dt > CALIBRATION_MS:       # 보정 마지막 프레임 — 플로어 확정
                self.floor = min(FLOOR_MAX, max(FLOOR_MIN, self._calib))
            return
        speech = _db(rms) >= max(_db(self.floor) + SPEECH_MARGIN_DB, SPEECH_ABS_MIN_DB)
        # 플로어 갱신은 판정 뒤에 한다 — 이 프레임 자신이 문턱을 끌어올리지 않게.
        if rms < self.floor:
            self.floor = max(FLOOR_MIN, self.floor + (rms - self.floor) * min(1.0, dt / FLOOR_FALL_MS))
        elif not speech:
            self.floor = min(FLOOR_MAX, self.floor + (rms - self.floor) * min(1.0, dt / FLOOR_RISE_MS))

        if speech:
            self.speech_ms += dt
            self._gap_ms = 0
            self._silence_ms = 0
            if not self.confirmed:
                self._onset_ms += dt
                if self._onset_ms >= SPEECH_CONFIRM_MS:
                    self.confirmed = True
                    self.confirmed_at_ms = self.audio_ms
            return
        if not self.confirmed:
            self._gap_ms += dt
            if self._gap_ms > ONSET_GAP_MS:
                self._onset_ms = 0
            return
        self._silence_ms += dt
        if self._silence_ms >= ENDPOINT_SILENCE_MS and not self.endpoint:
            self.endpoint = True
            self.endpoint_at_ms = self.audio_ms
