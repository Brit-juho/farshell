"""시스템 사용률(CPU·RAM·네트워크) — OS별 파서를 실제 명령 출력 샘플로 검증한다.

Linux·WSL2 경로는 이 맥에서 실행할 수 없어서(파일이 없다) **샘플 텍스트**로만 확인한다.
macOS 경로는 실제 값으로도 확인한다(마지막 두 테스트)."""

import pytest

import platform_utils
import sysstats as S

PROC_STAT = """\
cpu  4705 150 1120 16250 520 0 45 0 0 0
cpu0 1175 40 280 4060 130 0 10 0 0 0
intr 1462898
"""

PROC_STAT_LATER = """\
cpu  4805 150 1170 16450 520 0 45 0 0 0
"""

MEMINFO = """\
MemTotal:       16384000 kB
MemFree:          500000 kB
MemAvailable:    6384000 kB
Buffers:          200000 kB
Cached:          4000000 kB
SwapTotal:       2097148 kB
"""

PROC_NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 9000      90    0    0    0     0          0         0     9000      90    0    0    0     0       0          0
  eth0: 1000000  5000    0    0    0     0          0         0   250000    3000    0    0    0     0       0          0
 docker0: 500    5    0    0    0     0          0         0     600      6    0    0    0     0       0          0
tailscale0: 700   7    0    0    0     0          0         0     800      8    0    0    0     0       0          0
"""

VM_STAT = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                    45766.
Pages active:                                1317152.
Pages inactive:                              1304748.
Pages speculative:                             12980.
Pages throttled:                                   0.
Pages wired down:                             234869.
Pages purgeable:                               16782.
"Translation faults":                     4555178723.
Pages stored in compressor:                   348326.
Pages occupied by compressor:                 120000.
"""

NETSTAT_IB = """\
Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                        1000     0     500000     1000     0     500000     0
en0        1500  <Link#15>   02:00:00:00:00:00 75986167     0 81732707499 45207534     0 27434579922     0
en0        1500  host-name   fe80:1::1234:abcd 75986167     - 81732707499 45207534     - 27434579922     -
en0        1500  192.168.0.0/24 192.168.0.5    75986167     - 81732707499 45207534     - 27434579922     -
utun3      1380  <Link#20>                       10     0       2000       10     0       2000     0
awdl0      1500  <Link#12>   aa:bb:cc:dd:ee:ff   10     0       3000       10     0       3000     0
en5*       1500  <Link#9>    aa:bb:cc:dd:ee:00   20     0       4000       20     0       5000     0
"""

TOP_OUT = """\
Processes: 800 total
CPU usage: 50.00% user, 30.00% sys, 20.00% idle
Processes: 801 total
CPU usage: 10.99% user, 13.36% sys, 75.64% idle
"""


def test_proc_stat_busy_and_total():
    assert S.parse_proc_stat(PROC_STAT) == (4705 + 150 + 1120 + 45, 4705 + 150 + 1120 + 16250 + 520 + 45)
    assert S.parse_proc_stat("nothing") is None
    assert S.parse_proc_stat("cpu  1 2") is None


def test_cpu_pct_from_two_samples():
    a, b = S.parse_proc_stat(PROC_STAT), S.parse_proc_stat(PROC_STAT_LATER)
    # busy +150(user100+sys50), total +350 → 42.857%
    assert S.cpu_pct(a, b) == pytest.approx(100 * 150 / 350)
    assert S.cpu_pct(a, a) is None, "시간이 안 흘렀으면 값이 없다"
    assert S.cpu_pct(None, b) is None


def test_meminfo_uses_memavailable():
    used, total = S.parse_meminfo(MEMINFO)
    assert total == 16384000 * 1024
    assert used == (16384000 - 6384000) * 1024


def test_meminfo_old_kernel_without_memavailable():
    text = "MemTotal: 1000 kB\nMemFree: 100 kB\nBuffers: 50 kB\nCached: 150 kB\n"
    used, total = S.parse_meminfo(text)
    assert (used, total) == ((1000 - 300) * 1024, 1000 * 1024)
    assert S.parse_meminfo("garbage") is None


def test_proc_net_dev_skips_loopback_virtual_and_tunnel():
    raw = S.parse_proc_net_dev(PROC_NET_DEV)
    assert raw["eth0"] == (1000000, 250000)
    assert set(S.usable_net(raw)) == {"eth0"}


def test_vm_stat_used_is_active_plus_wired_plus_compressor():
    total = 48 * 1024 ** 3
    used, tot = S.parse_vm_stat(VM_STAT, total)
    assert tot == total
    assert used == (1317152 + 234869 + 120000) * 16384
    assert S.parse_vm_stat("garbage", total) is None
    assert S.parse_vm_stat(VM_STAT, 0) is None


def test_netstat_ib_uses_the_link_row_of_en_interfaces_only():
    got = S.parse_netstat_ib(NETSTAT_IB)
    assert got["en0"] == (81732707499, 27434579922), "주소 행이 여럿이어도 링크 행 하나만 쓴다"
    assert got["en5"] == (4000, 5000), "이름 끝의 *(비활성 표시)는 떼고 센다"
    assert "lo0" not in got and "utun3" not in got and "awdl0" not in got


def test_net_rates_sum_common_interfaces_and_handle_counter_reset():
    prev = {"en0": (1000, 100), "en1": (500, 50)}
    cur = {"en0": (3000, 300), "en1": (400, 80), "en2": (9, 9)}     # en1 rx는 줄었다(재설정) → 0
    rx, tx, names = S.net_rates(prev, cur, 2.0)
    assert names == ["en0", "en1"]
    assert rx == pytest.approx(1000.0) and tx == pytest.approx(115.0)
    assert S.net_rates({}, cur, 2.0) is None
    assert S.net_rates(prev, cur, 0) is None


def test_top_cpu_uses_the_last_sample():
    assert S.parse_top_cpu(TOP_OUT) == pytest.approx(100 - 75.64)
    assert S.parse_top_cpu("no cpu line") is None


IOREG_GPU = """+-o AGXAcceleratorG17X  <class AGXAcceleratorG17X, id 0x1, registered, matched, active, busy 0 (0 ms), retain 20>
    {
      "IOClass" = "AGXAcceleratorG17X"
      "model" = "Apple M5 Pro"
      "gpu-core-count" = 16
      "PerformanceStatistics" = {"Tiler Utilization %"=0,"Renderer Utilization %"=0,"Device Utilization %"=99,"In use system memory"=22113107968}
    }
"""

NVIDIA_OUT = """NVIDIA GeForce RTX 4090, 12, 1024, 24564
NVIDIA GeForce RTX 4090, 87, 20480, 24564
"""


def test_ioreg_gpu_reads_utilization_name_and_cores():
    g = S.parse_ioreg_gpu(IOREG_GPU)
    assert g == {"pct": 99.0, "name": "Apple M5 Pro", "cores": 16, "count": 1}
    assert S.parse_ioreg_gpu("") is None
    assert S.parse_ioreg_gpu('+-o X\n  "model" = "y"\n') is None


def test_nvidia_smi_picks_the_busiest_gpu_and_counts():
    g = S.parse_nvidia_smi(NVIDIA_OUT)
    assert g["pct"] == 87.0 and g["count"] == 2 and g["mem_used"] == 20480 * 1024 ** 2
    assert S.parse_nvidia_smi("NVIDIA X, [N/A], [N/A], [N/A]\n") is None
    assert S.parse_nvidia_smi("") is None


def test_snapshot_shape_and_caching(monkeypatch):
    calls = {"net": 0}
    ticks = iter([(10, 100), (20, 200), (30, 300)])

    def fake_net():
        calls["net"] += 1
        return {"eth0": (calls["net"] * 1000, calls["net"] * 100)}

    monkeypatch.setattr(S, "read_cpu_ticks", lambda: next(ticks))
    monkeypatch.setattr(S, "read_net", fake_net)
    monkeypatch.setattr(S, "read_ram", lambda: (4 * 1024 ** 3, 16 * 1024 ** 3))
    monkeypatch.setattr(S, "read_gpu", lambda: {"pct": 42.04, "name": "G", "count": 1})
    monkeypatch.setattr(S, "FIRST_SAMPLE_GAP", 0.0)
    s = S.Sampler()
    a = s.snapshot()
    assert a["ok"] is True and a["platform"] == platform_utils.PLATFORM_NAME
    assert a["cpu"]["pct"] == 10.0 and a["cpu"]["cores"] >= 1
    assert a["ram"] == {"used": 4 * 1024 ** 3, "total": 16 * 1024 ** 3, "pct": 25.0}
    assert a["net"]["interfaces"] == ["eth0"] and a["net"]["rx_bps"] > 0
    assert a["gpu"] == {"pct": 42.0, "name": "G", "count": 1}
    assert s.snapshot() is a, "MIN_INTERVAL 안의 호출은 같은 결과(계산은 하나)"
    assert calls["net"] == 2


def test_missing_sources_become_null_not_errors(monkeypatch):
    monkeypatch.setattr(S, "read_cpu_ticks", lambda: None)
    monkeypatch.setattr(S, "read_net", lambda: {})
    monkeypatch.setattr(S, "read_ram", lambda: None)
    monkeypatch.setattr(S, "read_gpu", lambda: None)
    monkeypatch.setattr(S, "FIRST_SAMPLE_GAP", 0.0)
    monkeypatch.setattr(platform_utils, "IS_MACOS", False)
    a = S.Sampler().snapshot()
    assert a["ok"] is True and a["cpu"] is None and a["ram"] is None and a["net"] is None and a["gpu"] is None


@pytest.mark.skipif(not platform_utils.IS_MACOS, reason="macOS 실측")
def test_macos_real_values_are_sane():
    import time
    s = S.Sampler()
    s.snapshot()
    time.sleep(1.1)
    a = s.snapshot()
    assert 0.0 <= a["cpu"]["pct"] <= 100.0
    assert 0 < a["ram"]["used"] <= a["ram"]["total"] and 0 < a["ram"]["pct"] <= 100
    assert a["net"]["rx_bps"] >= 0 and a["net"]["interfaces"]
    assert a["gpu"] is None or 0.0 <= a["gpu"]["pct"] <= 100.0


def test_route_returns_the_snapshot(monkeypatch):
    import asyncio
    from routes import system as R
    fake = {"ok": True, "platform": "Linux", "cpu": {"pct": 1.5, "cores": 4},
            "ram": {"used": 1, "total": 2, "pct": 50.0},
            "net": {"rx_bps": 1, "tx_bps": 2, "interfaces": ["eth0"]}, "ts": 1.0}
    monkeypatch.setattr(S, "snapshot", lambda: fake)
    assert asyncio.run(R.system_stats()) == fake
    # 응답 모델이 값을 그대로 통과시키고, null 항목도 허용한다
    R.SystemStats(**{**fake, "cpu": None, "net": None})


@pytest.mark.parametrize("gpu", [None,
    {"pct": 12.5, "name": "Sample GPU", "count": 1, "cores": 16},
    {"pct": 75.0, "name": "Sample GPU", "count": 2, "mem_used": 1024, "mem_total": 4096},
])
def test_http_response_preserves_gpu(monkeypatch, gpu):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import system as R
    snapshot = {"ok": True, "platform": "test", "gpu": gpu, "ts": 1.0}
    monkeypatch.setattr(S, "snapshot", lambda: snapshot)
    app = FastAPI()
    app.include_router(R.router)
    response = TestClient(app).get("/api/system/stats")
    assert response.status_code == 200
    actual = response.json()["gpu"]
    if gpu is None:
        assert actual is None
    else:
        assert {key: actual[key] for key in gpu} == gpu
