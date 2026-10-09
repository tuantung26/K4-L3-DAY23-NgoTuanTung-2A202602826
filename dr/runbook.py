"""BƯỚC 3c — SINH VIÊN VIẾT. Tự động hoá runbook §4 "Runbook: Region Chính Down".

7 bước trên slide, mỗi bước 1 dòng log có ts. Log này CHÍNH LÀ timeline của postmortem.
  1 xac_nhan_outage          — probe cả 2 region, đừng tin 1 lần fail (dùng nhiều lần
                              hoặc gọi health_checker.probe nếu đã viết xong 3a)
  2 thong_bao_incident       — ts của dòng này là mốc "operator biết tin", LUÔN LUÔN
                              SAU t_outage trong chaos-events (không thể trùng — operator
                              không thể biết ngay giây outage xảy ra). Ghi cả 2 ts vào
                              log để postmortem tính được "độ trễ thông báo".
  3 scale_gpu_pool           — gọi HÀM `failover.failover(...)` MỘT LẦN DUY NHẤT. Hàm
                              đó tự làm đủ 5 bước con (verify/restore/scale/wait/cutover)
                              và tự ghi log riêng vào reports/failover-events.jsonl.
  4 verify_state_replica     — KHÔNG gọi lại failover — chỉ ĐỌC kết quả (vector count +
                              weights ở region phụ) từ dict mà bước 3 trả về, để log vào
                              runbook-run.jsonl cho postmortem đọc 1 chỗ duy nhất.
  5 dns_cutover              — cũng chỉ đọc lại: kết quả cutover có ok hay không.
  6 verify_golden_signals    — 10 request thật vào region phụ: p95 latency + error rate
  7 post_incident            — elapsed_s + lệnh đo RTO

BÁN TỰ ĐỘNG, KHÔNG FULL-AUTO (§4: "failover đầu tiên nên là bán tự động — alert +
1-click confirm — tránh flapping gây failover 2 chiều liên tục"). Mặc định phải hỏi
người vận hành confirm; --auto chỉ dùng trong CI/khi chấm điểm.

Chạy:  python dr/runbook.py --primary a --target b --backend fs
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, ".")
from dr import failover as fo  # noqa: E402
from dr import health_checker as hc  # noqa: E402

LOG = pathlib.Path("reports/runbook-run.jsonl")
URL = {"a": "http://127.0.0.1:8001", "b": "http://127.0.0.1:8002"}


def step(n, name, **kw):
    """Ghi 1 dòng {ts, iso, step, name, ...} vào LOG."""
    rec = {
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "step": n,
        "name": name,
        **kw,
    }
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"RUNBOOK step {n} ({name}):", json.dumps(rec))
    return rec


def confirm(auto: bool, msg: str) -> bool:
    """auto=True -> True; ngược lại hỏi y/N. Đừng bỏ hàm này đi."""
    if auto:
        return True
    try:
        ans = input(f"{msg} [y/N]: ").strip().lower()
        return ans in ("y", "yes")
    except EOFError:
        return False


def run(primary: str, target: str, backend: str, auto: bool) -> dict:
    """7 bước ở trên."""
    t_start = time.time()

    # Confirm
    if not confirm(auto, f"Xác nhận kích hoạt quy trình failover từ region {primary} sang region {target}?"):
        print("Operator hủy lệnh failover.")
        return {"ok": False, "aborted": True}

    # Bước 1: xac_nhan_outage (chống flapping: xác nhận primary fail >= 3 lần liên tiếp)
    fails = 0
    p_ready, p_reason = True, "init"
    while fails < 3:
        p_ready, p_reason = hc.probe(primary, timeout=2.0)
        if not p_ready:
            fails += 1
        else:
            fails = 0
        if fails < 3:
            time.sleep(1.0)

    # Đảm bảo health-checker đã kịp ghi nhận UNHEALTHY (để t_cutover > t_detect, tránh warning)
    health_path = pathlib.Path("reports/health-events.jsonl")
    for _ in range(20):
        if health_path.exists():
            try:
                lines = [json.loads(l) for l in health_path.read_text(encoding="utf-8").splitlines() if l.strip()]
                if any(e.get("to") == "UNHEALTHY" and e.get("region") == primary for e in lines):
                    break
            except Exception:
                pass
        time.sleep(1.0)

    t_ready, t_reason = hc.probe(target, timeout=2.0)
    step(1, "xac_nhan_outage",
         primary=primary, primary_ready=p_ready, primary_reason=p_reason,
         target=target, target_ready=t_ready, target_reason=t_reason,
         consecutive_fails=fails)

    # Bước 2: thong_bao_incident
    chaos_path = pathlib.Path("chaos/chaos-events.jsonl")
    t_outage = None
    if chaos_path.exists():
        kills = [json.loads(l) for l in chaos_path.read_text(encoding="utf-8").splitlines()
                 if l.strip() and json.loads(l).get("action") == "kill"]
        if kills:
            t_outage = kills[-1].get("ts")
    t_incident = time.time()
    step(2, "thong_bao_incident",
         t_outage=t_outage, t_incident=t_incident,
         notification_delay_s=round(t_incident - t_outage, 2) if t_outage else None)

    # Bước 3: scale_gpu_pool (gọi fo.failover một lần duy nhất)
    fo_result = fo.failover(target=target, backend=backend, wait=60.0)
    step(3, "scale_gpu_pool", ok=fo_result.get("ok"), fo_result=fo_result)
    if not fo_result.get("ok"):
        return {"ok": False, "step_failed": 3, "fo_result": fo_result,
                "elapsed_s": round(time.time() - t_start, 2)}

    # Bước 4: verify_state_replica (đọc từ kết quả bước 3)
    target_state = fo_result.get("target_state", {})
    step(4, "verify_state_replica",
         target=target,
         vector_count=target_state.get("count"),
         weights=target_state.get("weights"),
         rpo_seconds=fo_result.get("rpo_seconds"),
         docs_lost=fo_result.get("docs_lost"),
         embed_model_version=fo_result.get("embed_model_version"))

    # Bước 5: dns_cutover (đọc kết quả cutover)
    active_file = pathlib.Path("edge/active_region")
    current_active = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else ""
    step(5, "dns_cutover",
         target=target,
         active_region=current_active,
         ok=(current_active == target))

    # Bước 6: verify_golden_signals (10 request thật vào region phụ)
    latencies = []
    errors = 0
    with httpx.Client(timeout=3.0) as client:
        for i in range(10):
            t0 = time.time()
            try:
                res = client.get(f"{URL[target]}/v1/infer", params={"q": f"golden-signal-test-{i}"})
                if res.status_code != 200:
                    errors += 1
            except Exception:
                errors += 1
            latencies.append((time.time() - t0) * 1000)
    latencies.sort()
    p95 = round(latencies[-1], 1)
    error_rate = round(errors / 10.0, 2)
    step(6, "verify_golden_signals", p95_ms=p95, error_rate=error_rate, total_requests=10)

    # Bước 7: post_incident
    elapsed = round(time.time() - t_start, 2)
    cmd_measure = "python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300"
    step(7, "post_incident", elapsed_s=elapsed, measure_cmd=cmd_measure)

    return {
        "ok": True,
        "elapsed_s": elapsed,
        "fo_result": fo_result,
        "golden_signals": {"p95_ms": p95, "error_rate": error_rate},
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--primary", default="a")
    p.add_argument("--target", default="b")
    p.add_argument("--backend", default="fs", choices=["fs", "minio"])
    p.add_argument("--auto", action="store_true")
    a = p.parse_args()
    print(json.dumps(run(a.primary, a.target, a.backend, a.auto), indent=2))
