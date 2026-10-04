"""STT 서비스·라우트 테스트 공용 가짜 엔진 — 호출을 기록하고 네트워크를 쓰지 않는다."""
import threading
import time

from stt_engines.base import Engine, NotReady, Requirement
from stt_engines.env import HostEnv

MAC_ARM = HostEnv(os="macos", arch="arm64", macos_version=(26, 0), ram_mb=16384)
MAC_X86 = HostEnv(os="macos", arch="x86_64", macos_version=(15, 5), ram_mb=16384)
LINUX_X86 = HostEnv(os="linux", arch="x86_64", macos_version=None, ram_mb=16384)


class FakeEngine(Engine):
    id = "fake"
    label = "가짜"
    keys: tuple = ()                     # 지원하는 env.key (비면 전부)
    min_macos = None

    def __init__(self, calls=None):
        self.calls = calls if calls is not None else []
        self.present: set = set()
        self.reqs: list = []
        self._loaded = None
        self.text = "안녕하세요"
        self.delay = 0.0
        self.active = 0
        self.max_active = 0
        self.gate = None                 # threading.Event — transcribe가 풀릴 때까지 대기
        self.download_error = None
        self._g = threading.Lock()

    def supported(self, env):
        if self.keys and env.key not in self.keys:
            return False, f"{self.label}은(는) {env.key}에서 쓸 수 없습니다"
        if self.min_macos and (env.macos_version or (0, 0)) < self.min_macos:
            return False, "macOS 26 이상 필요"
        return True, ""

    def requirements(self, env):
        return list(self.reqs)

    def install_requirement(self, req_id, progress):
        self.calls.append((self.id, "install_requirement", req_id))
        progress(5, 10)

    def model_present(self, spec):
        return spec.id in self.present

    def download_model(self, spec, progress):
        self.calls.append((self.id, "download_model", spec.id))
        progress(3, 6)
        if self.download_error:
            raise RuntimeError(self.download_error)
        self.present.add(spec.id)

    def delete_model(self, spec):
        self.calls.append((self.id, "delete_model", spec.id))
        if self._loaded == spec.id:
            self.unload()
        self.present.discard(spec.id)
        return 123

    def load(self, spec):
        self.calls.append((self.id, "load", spec.id))
        if spec.id not in self.present:
            raise NotReady(f"모델 '{spec.label}'을(를) 아직 받지 않았습니다")
        self._loaded = spec.id

    def unload(self):
        self.calls.append((self.id, "unload"))
        self._loaded = None

    def loaded(self):
        return self._loaded

    def transcribe(self, wav, language):
        self.calls.append((self.id, "transcribe"))
        with self._g:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.gate is not None:
                self.gate.wait(5)
            if self.delay:
                time.sleep(self.delay)
            return self.text
        finally:
            with self._g:
                self.active -= 1

    def rss_mb(self):
        return 77 if self._loaded else 0


def make_engines(calls=None):
    """실제 세 엔진 id에 해당하는 가짜. mlx=macos-arm64만, llamacpp=맥·리눅스 전부, apple=macOS 26+."""
    calls = calls if calls is not None else []

    def mk(eid, label, keys=(), min_macos=None):
        cls = type(f"Fake_{eid}", (FakeEngine,), {"id": eid, "label": label, "keys": keys, "min_macos": min_macos})
        return cls(calls)
    return {
        "mlx_qwen3": mk("mlx_qwen3", "MLX", keys=("macos-arm64",)),
        "llamacpp_qwen3": mk("llamacpp_qwen3", "llama.cpp",
                             keys=("macos-arm64", "macos-x86_64", "linux-x86_64", "linux-arm64")),
        "apple_speech": mk("apple_speech", "Apple", keys=("macos-arm64", "macos-x86_64"), min_macos=(26, 0)),
    }
