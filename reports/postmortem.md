# Postmortem — DR Drill Lab 23

Theo đúng template §4 "Sau Failover: Blameless Postmortem". Blameless: câu hỏi là
"hệ thống/process nào cho phép chuyện này", không phải "ai làm sai".

## 1. Timeline (mọi dòng phải có evidence path:line)

| ISO time | Sự kiện | Evidence |
|---|---|---|
| 2026-10-09T04:18:46Z | Outage bắt đầu (netblock Region A) | `chaos/chaos-events.jsonl:2` |
| 2026-10-09T04:18:46Z | User đầu tiên bị ảnh hưởng (503 ReadTimeout) | `reports/drill-2-withdr.jsonl:25` |
| 2026-10-09T04:19:01Z | Health check alert (`a` sang `UNHEALTHY`) | `reports/health-events.jsonl:2` |
| 2026-10-09T04:19:02Z | Operator confirm cutover (Runbook step 2) | `reports/runbook-run.jsonl:2` |
| 2026-10-09T04:19:14Z | Resolved (request đầu tiên 200 OK từ region phụ `b`) | `reports/drill-2-withdr.jsonl:39` |

## 2. RTO/RPO đo được vs mục tiêu — gap ở bước nào?

- RTO mục tiêu: 300s · đo được: `28.4s` · gap: `-271.6s` (đạt mục tiêu, nhanh hơn yêu cầu 271.6s)
- RPO mục tiêu: 300s · đo được: `2.0s` (`1` doc bị mất) · gap: `-298.0s` (đạt mục tiêu)
- **Bước tốn nhiều giây nhất:** `Health-check detect floor` (15.0s, chiếm 52.8% tổng RTO) — vì hệ thống phải chờ đủ `threshold=3` lần thất bại liên tiếp với `interval=5.0s` để chống hiện tượng flapping (§4 Anti-Patterns).

## 3. Root cause (5 whys)

1. *Tại sao user nhận 503?* Vì Region A không phản hồi yêu cầu suy luận inference.
2. *Tại sao Region A không phản hồi?* Cổng mạng của Region A bị ngắt kết nối (mô phỏng sự cố phân vùng mạng / AZ outage).
3. *Tại sao Region B không thay thế ngay lập tức?* Vì hệ thống DR cần thời gian xác thực outage (15s), khôi phục dữ liệu snapshot (0.3s), khởi động GPU pool (6.35s) và chờ DNS TTL cache (5.8s).
4. *Nếu đây là outage thật, bước nào trong runbook của tôi sẽ thất bại?* 
   - Bước `2_restore_snapshot` có thể thất bại nếu bucket snapshot chưa hoàn tất nhân bản xuyên vùng (cross-region replication lag) hoặc embedding model version bị lệch so với vector DB snapshot.
   - Bước `4_wait_ready` có thể timeout nếu dung lượng model weights thực tế lớn và quá trình GPU warmup vượt quá thời gian timeout dự kiến.
5. *Làm thế nào để hệ thống bền vững hơn?* Cần có kiểm tra tương thích phiên bản tự động (manifest verification) và duy trì pre-warmed instance ở vùng dự phòng.

## 4. Action items (có owner + deadline)

| # | Action | Owner | Deadline | Giảm RTO/RPO bao nhiêu giây |
|---|---|---|---|---|
| 1 | Tối ưu hóa chu kỳ health check xuống 3s (threshold=3) kết hợp probe song song | SRE Team | 2026-11-01 | Giảm ~6s RTO |
| 2 | Duy trì warm standby GPU pool sẵn sàng ở Region B (Active-Warm) | ML Platform Team | 2026-11-15 | Giảm ~6s RTO (bỏ qua warmup) |
| 3 | Tăng tần suất replication snapshot từ 30s xuống 10s hoặc dùng CDC streaming | Data Eng Team | 2026-12-01 | Giảm RPO về < 1s |

## 5. Ba câu hỏi bắt buộc trả lời

1. **`interval × threshold` của bạn là bao nhiêu giây? Nó chiếm bao nhiêu % RTO?**
   - Cấu hình: `interval = 5.0s`, `threshold = 3` → Detection floor là `15.0s`.
   - Tỷ lệ: Chiếm `15.0s / 28.4s ≈ 52.8%` tổng RTO đo được.
2. **Nếu hạ interval xuống 1s, RTO giảm mấy giây — và bạn trả giá gì (§4 flapping)?**
   - Detection floor sẽ giảm từ 15s xuống 3s, giúp RTO giảm được `12.0s` (RTO mới ~16.4s).
   - Cái giá phải trả là nguy cơ **flapping** rất cao: khi mạng chỉ bị nghẽn tạm thời (jitter) hoặc server bị GC pause nhẹ trong 3s, hệ thống sẽ kích hoạt nhầm failover sang Region B, gây gián đoạn kép cho người dùng và lãng phí chi phí chuyển vùng.
3. **Nếu outage kéo dài 6 giờ và region chính mất dữ liệu vĩnh viễn, `docs_lost` của bạn có nghĩa gì với khách hàng?**
   - `docs_lost = 1` có nghĩa là khách hàng bị mất đúng 1 tài liệu/giao dịch đã gửi vào hệ thống trong khoảng 2 giây trước sự cố mà chưa kịp replicate sang Region B.
   - Với khách hàng, tài liệu này cần được re-ingest lại từ log giao dịch hoặc client retry; việc mất mát bị giới hạn ở mức 1 document giúp giảm thiểu tối đa rủi ro thất thoát dữ liệu nghiệp vụ.
