# Runbook sự cố — QCVN Chatbot (M15)

Cảnh báo được tính mỗi `ALERT_INTERVAL_S` (mặc định 60 s) trên cửa sổ `ALERT_WINDOW_MIN` (15 phút), gửi tới `ALERT_WEBHOOK_URL` và hiện ở `/admin` (bảng "Cảnh báo đang mở"). Một cảnh báo chỉ bắn lại sau `ALERT_COOLDOWN_MIN` (60 phút) và tự chuyển "đã xử lý" khi điều kiện hết (trừ `budget_*`, `client_quota_exceeded`, `test`). Mọi dòng log là JSON có `request_id`; log của một câu hỏi có thêm `query_id` — tìm bằng `grep <query_id>` hoặc mở `/admin/logs/<query_id>`.

| Cảnh báo | Nghĩa | Kiểm tra trước | Xử lý |
|---|---|---|---|
| `gemini_consecutive_errors` (crit) | ≥ N lần gọi Gemini lỗi liên tiếp | `/admin/health?probe=true` → `gemini.last_error`; log `llm.error` | 401/403: khóa sai hoặc hết hạn → thay `GEMINI_API_KEY`, khởi động lại. 429: hết quota Google → nâng hạn mức hoặc chờ; hạ `QUOTA_QUESTIONS_PER_CLIENT_DAY`. 5xx/timeout: sự cố Google → chờ, tăng `GEMINI_TIMEOUT_S` nếu chỉ chậm. Câu theo chính sách (tần suất, ngoài phạm vi) vẫn chạy khi Gemini hỏng |
| `error_rate` (crit) | tỷ lệ `status=error` > `ALERT_ERROR_RATE` với ≥ `ALERT_MIN_SAMPLES` câu | `/admin/logs?status=error` → cột lỗi; `grep '"level": "ERROR"'` | Lỗi Gemini → như trên. Lỗi DB (`OperationalError`) → kiểm PostgreSQL, dung lượng đĩa. Lỗi code (traceback) → ghi issue kèm `query_id`, rollback bản trước nếu vừa triển khai |
| `abstain_rate` (warn) | tỷ lệ "không kết luận" > `ALERT_ABSTAIN_RATE` | `/admin/logs?status=abstained` đọc `abstain_reason`; dashboard `index_active`/`bm25_loaded` | Index không active hoặc BM25 chưa nạp → `/admin/documents` kích hoạt lại index (dựng lại BM25). Toàn câu ngoài phạm vi (tần suất, nước thải sinh hoạt) → không phải sự cố, ghi nhận cho S8 mở rộng corpus. Câu hợp lệ mà abstain → kiểm `validator` trong chi tiết log; báo AI engineer (prompt/retrieval) |
| `validator_reject_rate` (warn) | > `ALERT_VALIDATOR_REJECT_RATE` câu dùng LLM bị validator từ chối lần đầu (sinh lại) hoặc fallback | `/admin/logs?path=fallback`, chi tiết → khối "Bộ kiểm tra trích dẫn" | Thường do model hoặc prompt đổi. Kiểm `GEMINI_ANSWER_MODEL` đúng bản pin (ADR-0011); chạy `python scripts/qa_eval.py` để so với báo cáo trước; tạm tăng `GEMINI_ANSWER_THINKING=medium` nếu cần |
| `p95_latency` (warn) | p95 độ trễ > `ALERT_P95_LATENCY_MS` | dashboard p50/p95; `latency_ms` trong chi tiết log (understand / retrieve / generate) | `generate` chậm → Gemini chậm, xét hạ thinking. `retrieve` chậm → PostgreSQL (kiểm `EXPLAIN` truy vấn vector, `VACUUM ANALYZE evidence_chunks`). Toàn bộ chậm → CPU/RAM máy chủ, số SSE đồng thời (chưa có rate limit — M10) |
| `budget_80` / `budget_100` (warn / crit) | chi phí Gemini hôm nay ≥ 80 % / 100 % `QUOTA_GLOBAL_USD_PER_DAY` | dashboard "Top client hôm nay"; `/admin/usage?group=client` | Không chặn (quyết định PO). Một client bất thường → liên hệ hoặc chờ M10 rate limit. Sử dụng thật tăng → nâng `QUOTA_GLOBAL_USD_PER_DAY`, báo PO |
| `client_quota_exceeded` (info) | một trình duyệt vượt `QUOTA_QUESTIONS_PER_CLIENT_DAY` | dashboard "Client vượt quota" | Chỉ theo dõi; nhiều client cùng vượt trong ngày → xem lại hạn mức hoặc nghi lạm dụng |
| `health_degraded` (crit) | DB lỗi hoặc không có index active | `GET /api/v1/health` | DB: kiểm dịch vụ PostgreSQL, kết nối `DATABASE_URL`. Không có index active: `/admin/documents` → kích hoạt; nếu không có phiên bản nào: `python scripts/ingest.py embed` rồi kích hoạt |
| `test` (info) | cảnh báo thử từ dashboard | – | Bấm "Đã xử lý" |

## Thao tác thường gặp

- **Xem chi tiết một câu hỏi**: `/admin/logs/<query_id>` (câu hỏi, căn cứ, câu trả lời, validator, phản hồi, token/chi phí/độ trễ, lỗi).
- **Xuất nhật ký**: `/admin/logs` → "Xuất CSV (theo bộ lọc)" (UTF-8 BOM, ≤ 10.000 dòng).
- **Kích hoạt lại index / dựng lại BM25**: `/admin/documents` → "Kích hoạt" (có xác nhận, ghi audit).
- **Xóa nhật ký cũ (M12)**: `python scripts/purge_logs.py --days 365 --yes` (mặc định theo `QUERY_LOG_RETENTION_DAYS`).
- **Đổi ngưỡng cảnh báo**: sửa `ALERT_*` trong `.env`, khởi động lại backend; tắt toàn bộ bằng `ALERTS_ENABLED=false`.
- **Đánh dấu đã xử lý**: dashboard → "Đã xử lý" (ghi `audit_events` với người bấm).
- **Khởi động lại backend** làm mất bộ đếm Gemini trong bộ nhớ (`llm_calls`, `consecutive_errors`) nhưng không mất `alert_events`, `usage_daily`, `query_logs`.

## Leo thang

| Mức | Khi nào | Ai |
|---|---|---|
| Kỹ sư trực | mọi cảnh báo `crit` chưa tự resolve sau 15 phút | Backend |
| AI engineer | `validator_reject_rate`, `abstain_rate` kéo dài > 1 giờ không do index/ngoài phạm vi | AI |
| PO | `budget_100`, sự cố kéo dài > 4 giờ, nghi lạm dụng | PO |
