# RTO/RPO Evidence — Lab 23

Quy tắc duy nhất: mỗi con số ở đây phải trỏ được về **một dòng log thật**
(`đường/dẫn.jsonl:số_dòng`). `pytest tests/test_rto_evidence.py` sẽ mở từng file ra kiểm tra.
Con số không có evidence = trượt, bất kể các phần khác.

## 1. Drill 1 — không có DR (baseline)

| Chỉ số | Giá trị | Cách đo | Evidence |
|---|---|---|---|
| t_outage | `2026-10-09T04:16:51` | chaos kill | `chaos/chaos-events.jsonl:1` |
| Request fail đầu tiên | `+0.0s` | dòng `ok:false` đầu tiên sau t_outage | `reports/drill-1-nodr.jsonl:17` |
| Request thành công sau đó | không có | không có dòng `ok:true` nào sau t_outage | `reports/measure-drill-1.json` |
| RTO | `NO_RECOVERY` | `tools/measure_rto.py` | `reports/measure-drill-1.json` |

## 2. Drill 2 — có DR

| Mốc | +giây từ t_outage | Cách đo | Evidence |
|---|---|---|---|
| t_outage (mốc 0) | 0s | `action:kill` | `chaos/chaos-events.jsonl:2` |
| User thấy lỗi đầu tiên | +0.1s | dòng `ok:false` đầu | `reports/drill-2-withdr.jsonl:25` |
| Health check phát hiện | +15.1s | `to:UNHEALTHY, region:a` | `reports/health-events.jsonl:2` |
| Snapshot restore xong | +16.2s | `step:2_restore_snapshot` | `reports/failover-events.jsonl:2` |
| Region phụ ready | +22.6s | `step:4_wait_ready` | `reports/failover-events.jsonl:4` |
| DNS cutover | +22.6s | `step:5_dns_cutover` | `reports/failover-events.jsonl:5` |
| **RTO đo được** | **+28.4s** | dòng `ok:true` đầu sau lỗi | `reports/drill-2-withdr.jsonl:39` |

| Chỉ số | Đo được | Mục tiêu (slide §1) | Verdict |
|---|---|---|---|
| RTO — Inference API | `28.4s` | 300s (5 phút) | PASS |
| RPO — Vector DB | `2.0s` / `1` doc | 300s (5 phút) | PASS |

## 3. RTO của tôi gồm những gì (bắt buộc — đây là phần chấm điểm hiểu bài)

| Thành phần | Giây | Nó đến từ đâu | Giảm được bằng cách nào |
|---|---|---|---|
| Health-check detect floor | 15.0s | `interval_s × threshold` (5.0s × 3) trong `reports/health-events.jsonl:2` | Hạ interval (xuống 2s) hoặc threshold (xuống 2), nhưng đánh đổi tăng nguy cơ flapping khi mạng chập chờn |
| Snapshot restore | 0.3s | 2_restore → 3_scale trong `reports/failover-events.jsonl:2` | Dùng snapshot incremental/delta thay vì full copy, lưu trữ trên NVMe SSD tốc độ cao |
| GPU pool warm-up | 6.35s | `waited_s` ở `4_wait_ready` trong `reports/failover-events.jsonl:4` | Duy trì warm standby instance hoặc pre-load model weights vào GPU VRAM sẵn |
| DNS/LB TTL cache | 5.8s | t_recovered − t_cutover (28.4s − 22.6s) giữa `reports/drill-2-withdr.jsonl:39` và `reports/failover-events.jsonl:5` | Hạ TTL DNS xuống 1s hoặc dùng Global Anycast Load Balancer thay vì DNS failover |
