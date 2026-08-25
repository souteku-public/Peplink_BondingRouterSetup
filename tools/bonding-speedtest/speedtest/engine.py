"""ボンディング回線スループット測定 — 測定エンジン。

標準ライブラリのみで動作する。
  * ダウンロード: 多並列HTTP(S) GET で回線を飽和させ、0.25秒刻みで受信バイトを採取
  * アップロード: 多並列HTTP(S) POST(送信側計測)
  * RTT: TCP接続確立時間(3-wayハンドシェイク)を定期プローブ
          無負荷時と転送中(負荷時)の両方を測る — 差がバッファブロートの指標になる

スループットの算出:
  * peak  … 0.25秒サンプルの1秒間ローリング合計の最大値(瞬間最大)
  * avg   … 立ち上がり(先頭20%)を除いた定常区間の平均
"""

from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

CHUNK = 64 * 1024
SAMPLE_INTERVAL = 0.25
RTT_INTERVAL = 0.3


class ByteCounter:
    """スレッド安全な累積バイトカウンタ。上限(limit)到達で満杯を報告する。"""

    def __init__(self, limit: int | None = None):
        self._lock = threading.Lock()
        self._total = 0
        self.limit = limit

    def add(self, n: int) -> bool:
        """加算し、まだ上限に達していなければ True を返す。"""
        with self._lock:
            self._total += n
            return self.limit is None or self._total < self.limit

    @property
    def total(self) -> int:
        with self._lock:
            return self._total


# ---------------------------------------------------------------------------
# RTT
# ---------------------------------------------------------------------------


def rtt_probe(host: str, port: int, timeout: float = 3.0) -> float | None:
    """TCP接続確立にかかった時間をミリ秒で返す(失敗は None)。"""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.perf_counter() - start) * 1000.0
    except OSError:
        return None


class RttSampler(threading.Thread):
    """停止されるまでRTTをプローブし続ける。"""

    def __init__(self, host: str, port: int):
        super().__init__(daemon=True)
        self.host, self.port = host, port
        self.samples: list[float] = []
        self.failures = 0
        # 注: threading.Thread は内部で self._stop() を使うため、その名前は使わない
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            ms = rtt_probe(self.host, self.port)
            if ms is None:
                self.failures += 1
            else:
                self.samples.append(ms)
            self._stop_event.wait(RTT_INTERVAL)

    def stop(self) -> None:
        self._stop_event.set()


def rtt_stats(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return {"min": None, "avg": None, "p95": None, "max": None, "count": 0}
    ordered = sorted(samples)
    return {
        "min": round(ordered[0], 1),
        "avg": round(sum(ordered) / len(ordered), 1),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 1),
        "max": round(ordered[-1], 1),
        "count": len(ordered),
    }


def measure_idle_rtt(host: str, port: int, seconds: float = 3.0) -> dict:
    sampler = RttSampler(host, port)
    sampler.start()
    time.sleep(seconds)
    sampler.stop()
    sampler.join(timeout=2)
    return rtt_stats(sampler.samples)


# ---------------------------------------------------------------------------
# 転送ワーカー
# ---------------------------------------------------------------------------


def _ssl_context(verify: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _opener(verify: bool, use_proxy: bool):
    handlers: list[Any] = []
    if not use_proxy:
        handlers.append(urllib.request.ProxyHandler({}))
    handlers.append(urllib.request.HTTPSHandler(context=_ssl_context(verify)))
    return urllib.request.build_opener(*handlers)


def _download_worker(url: str, counter: ByteCounter, deadline: float, stop: threading.Event,
                     errors: list[str], verify: bool, use_proxy: bool) -> None:
    opener = _opener(verify, use_proxy)
    while time.time() < deadline and not stop.is_set():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "bonding-speedtest/1.0"})
            with opener.open(req, timeout=15) as res:
                while time.time() < deadline and not stop.is_set():
                    chunk = res.read(CHUNK)
                    if not chunk:
                        break
                    if not counter.add(len(chunk)):
                        stop.set()  # データ量上限到達
                        return
        except Exception as exc:  # noqa: BLE001 計測は続行し、エラーは記録だけする
            if len(errors) < 5:
                errors.append(f"download: {type(exc).__name__}: {exc}")
            if stop.wait(0.5):
                return


def _upload_worker(url: str, counter: ByteCounter, deadline: float, stop: threading.Event,
                   errors: list[str], verify: bool, body_size: int = 16 * 1024 * 1024) -> None:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    payload = bytes(CHUNK)  # 内容は不問(TLSで圧縮はされない)

    while time.time() < deadline and not stop.is_set():
        conn: http.client.HTTPConnection | None = None
        try:
            if parsed.scheme == "https":
                conn = http.client.HTTPSConnection(host, port, timeout=15, context=_ssl_context(verify))
            else:
                conn = http.client.HTTPConnection(host, port, timeout=15)
            conn.putrequest("POST", path)
            conn.putheader("Content-Length", str(body_size))
            conn.putheader("Content-Type", "application/octet-stream")
            conn.putheader("User-Agent", "bonding-speedtest/1.0")
            conn.endheaders()
            sent = 0
            while sent < body_size:
                if time.time() >= deadline or stop.is_set():
                    return  # 途中打ち切り(送信済み分は計上済み)
                n = min(CHUNK, body_size - sent)
                conn.sock.sendall(payload[:n])
                sent += n
                if not counter.add(n):
                    stop.set()
                    return
            conn.getresponse().read()
        except Exception as exc:  # noqa: BLE001
            if len(errors) < 5:
                errors.append(f"upload: {type(exc).__name__}: {exc}")
            if stop.wait(0.5):
                return
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass


# ---------------------------------------------------------------------------
# 転送測定本体
# ---------------------------------------------------------------------------


@dataclass
class TransferResult:
    direction: str                      # download / upload
    seconds: float = 0.0
    bytes: int = 0
    avg_mbps: float | None = None       # 定常区間平均
    peak_mbps: float | None = None      # 1秒ピーク
    loaded_rtt: dict = field(default_factory=dict)
    samples: list[tuple[float, int]] = field(default_factory=list)  # (経過秒, 区間バイト)
    errors: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "direction": self.direction,
            "seconds": round(self.seconds, 2),
            "bytes": self.bytes,
            "avg_mbps": self.avg_mbps,
            "peak_mbps": self.peak_mbps,
            "loaded_rtt": self.loaded_rtt,
            "errors": self.errors,
        }


def _run_transfer(direction: str, worker, worker_args: tuple, *, streams: int,
                  duration: float, counter: ByteCounter,
                  rtt_host: str, rtt_port: int) -> TransferResult:
    stop = threading.Event()
    errors: list[str] = []
    deadline = time.time() + duration

    rtt = RttSampler(rtt_host, rtt_port)
    rtt.start()
    threads = [
        threading.Thread(target=worker, args=(*worker_args, counter, deadline, stop, errors),
                         daemon=True)
        for _ in range(streams)
    ]
    start = time.time()
    for t in threads:
        t.start()

    samples: list[tuple[float, int]] = []
    prev = 0
    while time.time() < deadline and not stop.is_set():
        time.sleep(SAMPLE_INTERVAL)
        total = counter.total
        samples.append((round(time.time() - start, 2), total - prev))
        prev = total
    stop.set()
    for t in threads:
        t.join(timeout=3)
    rtt.stop()
    rtt.join(timeout=2)
    elapsed = time.time() - start

    result = TransferResult(direction=direction, seconds=elapsed, bytes=counter.total,
                            samples=samples, errors=errors,
                            loaded_rtt=rtt_stats(rtt.samples))

    if samples:
        # 1秒ピーク: 連続する4サンプル(=1秒)の合計の最大
        window = max(1, int(1.0 / SAMPLE_INTERVAL))
        sums = [sum(b for _, b in samples[i:i + window]) for i in range(max(1, len(samples) - window + 1))]
        result.peak_mbps = round(max(sums) * 8 / 1e6, 1)
        # 定常平均: 先頭20%(TCPスロースタート)を除外
        skip = max(1, int(len(samples) * 0.2))
        steady = samples[skip:] or samples
        steady_secs = len(steady) * SAMPLE_INTERVAL
        if steady_secs > 0:
            result.avg_mbps = round(sum(b for _, b in steady) * 8 / steady_secs / 1e6, 1)
    return result


def measure_download(cfg: dict, limit_bytes: int | None) -> TransferResult:
    url = cfg["download_url"].replace("{bytes}", str(cfg.get("download_object_bytes", 67108864)))
    counter = ByteCounter(limit_bytes)
    verify = bool(cfg.get("verify_tls", True))
    use_proxy = bool(cfg.get("use_proxy", False))

    def worker(u, c, dl, st, er):
        _download_worker(u, c, dl, st, er, verify, use_proxy)

    return _run_transfer(
        "download", worker, (url,),
        streams=int(cfg.get("streams", 8)), duration=float(cfg.get("duration", 12)),
        counter=counter, rtt_host=cfg["rtt_host"], rtt_port=int(cfg.get("rtt_port", 443)),
    )


def measure_upload(cfg: dict, limit_bytes: int | None) -> TransferResult:
    counter = ByteCounter(limit_bytes)
    verify = bool(cfg.get("verify_tls", True))

    def worker(u, c, dl, st, er):
        _upload_worker(u, c, dl, st, er, verify)

    return _run_transfer(
        "upload", worker, (cfg["upload_url"],),
        streams=int(cfg.get("upload_streams") or cfg.get("streams", 8)),
        duration=float(cfg.get("duration", 12)),
        counter=counter, rtt_host=cfg["rtt_host"], rtt_port=int(cfg.get("rtt_port", 443)),
    )
