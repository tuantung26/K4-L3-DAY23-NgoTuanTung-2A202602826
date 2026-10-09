"""BƯỚC 3a — SINH VIÊN VIẾT. Health checker cho 2 region.

Yêu cầu (đọc §4 "Kiến Trúc Health-Check-Based Failover" + §2 "DNS Failover"):
  1. Poll /readyz của CẢ HAI region mỗi `interval` giây (mặc định 5s).
     Dùng /readyz, KHÔNG dùng /healthz. /healthz chỉ nói "process còn sống" —
     region có process sống nhưng vector DB rỗng thì vẫn không serve được.
  2. Chỉ đổi trạng thái sau `threshold` lần fail LIÊN TIẾP (mặc định 3).
     Một lần fail không phải outage. Đây là chống flapping (§4 Anti-Patterns).
  3. Ghi 1 dòng JSONL MỖI LẦN ĐỔI TRẠNG THÁI (không ghi mỗi lần poll — log sẽ ngập).
     Dòng bắt buộc có: ts, region, to (HEALTHY|UNHEALTHY), reason,
     interval_s, threshold. Thiếu interval_s/threshold thì tools/measure_rto.py
     không tính được detect floor -> mất điểm.

Chạy:  python dr/health_checker.py --interval 5 --threshold 3 --duration 300 \
              --out reports/health-events.jsonl
"""
import argparse
import json
import pathlib
import time

import httpx

URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def probe(region: str, timeout: float) -> tuple[bool, str]:
    """Trả về (ready, reason). Timeout PHẢI có — netblock làm request treo mãi."""
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.get(f"{URL[region]}/readyz")
            if r.status_code == 200:
                return True, "ok"
            try:
                data = r.json()
                reasons = data.get("reasons", [f"status_{r.status_code}"])
                return False, ",".join(reasons)
            except Exception:
                return False, f"status_{r.status_code}"
    except httpx.TimeoutException:
        return False, "timeout"
    except httpx.ConnectError:
        return False, "connect_error"
    except Exception as e:
        return False, str(type(e).__name__)


def run(interval: float, timeout: float, threshold: int, duration: float, out: pathlib.Path):
    """Vòng lặp poll + phát hiện transition + ghi JSONL."""
    out.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "a": {"status": "HEALTHY", "consecutive_fails": 0, "consecutive_success": 0},
        "b": {"status": "HEALTHY", "consecutive_fails": 0, "consecutive_success": 0},
    }
    end_time = time.time() + duration
    with out.open("a", encoding="utf-8") as f:
        while time.time() < end_time:
            loop_start = time.time()
            for r in ("a", "b"):
                ok, reason = probe(r, timeout)
                st = state[r]
                if ok:
                    st["consecutive_success"] += 1
                    st["consecutive_fails"] = 0
                    if st["status"] != "HEALTHY" and st["consecutive_success"] >= threshold:
                        st["status"] = "HEALTHY"
                        rec = {
                            "event": "state_change",
                            "ts": time.time(),
                            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "region": r,
                            "to": "HEALTHY",
                            "reason": reason,
                            "interval_s": interval,
                            "threshold": threshold,
                            "consecutive_success": st["consecutive_success"],
                        }
                        f.write(json.dumps(rec) + "\n")
                        f.flush()
                        print("HEALTH", json.dumps(rec))
                else:
                    st["consecutive_fails"] += 1
                    st["consecutive_success"] = 0
                    if st["status"] != "UNHEALTHY" and st["consecutive_fails"] >= threshold:
                        st["status"] = "UNHEALTHY"
                        rec = {
                            "event": "state_change",
                            "ts": time.time(),
                            "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "region": r,
                            "to": "UNHEALTHY",
                            "reason": reason,
                            "interval_s": interval,
                            "threshold": threshold,
                            "consecutive_fails": st["consecutive_fails"],
                        }
                        f.write(json.dumps(rec) + "\n")
                        f.flush()
                        print("HEALTH", json.dumps(rec))
            elapsed = time.time() - loop_start
            sleep_time = max(0.0, interval - elapsed)
            time.sleep(sleep_time)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--timeout", type=float, default=2.0)
    p.add_argument("--threshold", type=int, default=3)
    p.add_argument("--duration", type=float, default=300)
    p.add_argument("--out", default="reports/health-events.jsonl")
    a = p.parse_args()
    run(a.interval, a.timeout, a.threshold, a.duration, pathlib.Path(a.out))
