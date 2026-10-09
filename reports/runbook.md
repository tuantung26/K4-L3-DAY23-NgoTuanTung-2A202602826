# Runbook 1 trang — Region chính down

Runbook phải chạy được lúc 3h sáng bởi người KHÔNG viết nó. Mỗi bước: lệnh copy-paste
được + cách biết bước đó xong.

| # | Bước | Lệnh | Biết là xong khi | Ai làm |
|---|---|---|---|---|
| 1 | Xác nhận outage | `python chaos/kill_region.py status` | `a.ready=false` (hoặc timeout) 3 lần liên tiếp, `b.alive=true` | On-call SRE |
| 2 | Mở incident + bấm giờ RTO | `python dr/runbook.py --primary a --target b --backend fs --auto` | Dòng `step: 2, name: thong_bao_incident` ghi vào `reports/runbook-run.jsonl` | Incident Commander |
| 3 | Restore state ở region phụ | `python state/snapshot.py get --region b --backend fs` | `state/region-b/vectors.sqlite` và `model.bin` tồn tại, log ra `embed_model_version` | Automation / On-call |
| 4 | Scale pool warm→full | `echo full > state/region-b/pool_state && curl -s http://127.0.0.1:8002/readyz` | `/readyz` của port 8002 trả về HTTP 200 `{"ready": true}` sau GPU warmup | Automation / On-call |
| 5 | DNS/LB cutover | `printf b > edge/active_region` | `curl -s localhost:8080/edge/state` trả về `active_region=b` | Automation / On-call |
| 6 | Verify golden signals | `for i in $(seq 1 10); do curl -s "http://127.0.0.1:8080/v1/infer?q=ping" | grep -q "edge_region" && echo "OK $i"; done` | 10/10 requests thành công, p95 < 150ms, error rate = 0% | On-call SRE |
| 7 | Đo RTO + postmortem | `python tools/measure_rto.py --loadgen reports/drill-2-withdr.jsonl --target-rto 300` | Kết quả JSON hiển thị `rto_verdict: PASS` và RTO đo được $\le$ 300s | Incident Commander |

## Rollback (Failover ngược về Region A)

**Điều kiện cần và đủ để trả traffic về Region A:**
1. **Kiểm tra độ ổn định của Region A:** Region A đã được khôi phục mạng/tiến trình (`python chaos/kill_region.py restore --region a --backend bare`), endpoint `/readyz` trả về HTTP 200 liên tục trong ít nhất **30 phút** (không flapping).
2. **Đồng bộ dữ liệu hai chiều (Reverse Sync):** Toàn bộ dữ liệu vector DB phát sinh tại Region B trong thời gian sự cố phải được snapshot và đồng bộ ngược về Region A (`python state/snapshot.py put --region b && python state/snapshot.py get --region a`), đảm bảo RPO ngược = 0s.
3. **Thẩm quyền phê duyệt:** **Chỉ Incident Commander (IC) hoặc Head of Infrastructure** mới có quyền phê duyệt thực hiện rollback. Tuyệt đối **KHÔNG** kích hoạt rollback tự động (§4 Anti-Patterns: full-auto không có circuit breaker sẽ dẫn đến tình trạng hai vùng flap qua lại liên tục, làm sập hoàn toàn hệ thống).
