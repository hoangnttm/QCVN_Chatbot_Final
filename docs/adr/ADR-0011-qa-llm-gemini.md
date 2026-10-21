# ADR-0011 — LLM hỏi đáp chuyển sang Gemini API: `gemini-3.8-flash` (thinking low) trả lời, `gemini-3.5-flash-lite` hiểu câu hỏi

**Trạng thái:** **Accepted** (PO chốt 2026-09-23: `gemini-3.8-flash`, `thinking_level=low`; billing Gemini đã bật cho dự án thật) · **Ngày:** 2026-09-23 · Thay thế phần LLM của ADR-0005 (Claude Opus 5); PLAN.md D5, §8

## Context

PO quyết định dùng chung `GEMINI_API_KEY` (đã dùng cho embedding, ADR-0005) cho bước hỏi đáp thay vì Anthropic. Yêu cầu của PLAN §8 với LLM: structured output đúng schema, chỉ dùng evidence, mọi số phải nằm trong chunk được trích, abstain khi thiếu căn cứ, cảnh báo bản dự thảo, tiếng Việt, streaming, chi phí/độ trễ hợp lý. Có ba vai trò: (1) query understanding (domain/intent/entity → JSON), (2) sinh câu trả lời có trích dẫn, (3) LLM-judge cho evaluation (Phase 5).

## Dữ liệu phân tích (2026-09-23)

### Model text khả dụng với key hiện tại (`models.list`, hỗ trợ `generateContent`, 1M token vào / 65k ra)

| Model | Trạng thái (docs Google) | Giá / 1M token vào → ra (kể cả thinking) | Caching | Free tier |
|---|---|---|---|---|
| `gemini-3.8-flash` | **New Stable** — Google khuyến nghị cho dự án mới | $0,75 → $3,75 **đến 31/12/2026**, sau đó $1,50 → $7,50 | $0,075 | có |
| `gemini-3.7-flash`, `gemini-3.6-flash` | Stable | như 3.8 | $0,075 | có |
| `gemini-3.5-flash` | Stable | $1,50 → $9,00 | $0,15 | có |
| `gemini-3.5-flash-lite` | Stable — Google khuyến nghị cho dự án mới | $0,30 → $2,50 | không | có |
| `gemini-3.1-flash-lite` | Stable | $0,25 → $1,50 | $0,025 | có |
| `gemini-3.1-pro-preview` | **Preview**, chưa có Pro stable | $2,00 → $12,00 (≤ 200k) | $0,20 | **không** |
| `gemini-2.5-pro` / `-flash` / `-flash-lite` | đang **hạn chế truy cập** cho người dùng mới | $1,25 → $10 / $0,30 → $2,50 / $0,10 → $0,40 | có | có |
| `gemini-flash-latest`, `gemini-pro-latest` | alias trỏ tới bản mới nhất (đổi ngầm) | theo bản đích | — | — |

Thinking: Gemini 3.x có `thinking_level` low / medium / high (mặc định medium; 3.6 thêm minimal), **không tắt hẳn được**; giá đầu ra tính cả token thinking. Implicit caching bật mặc định nhưng chỉ khi prefix ≥ 4.096 token (3.x) — prompt hỏi đáp của ta ≈ 2.000 token nên không hưởng caching; không cần thiết kế cache. Rate limit xem trong AI Studio; `gemini-3.1-pro-preview` (không free tier) gọi được → project đang ở **bậc trả phí** (cần PO xác nhận billing trong AI Studio). Bậc trả phí: prompt không dùng để cải thiện sản phẩm (theo điều khoản Gemini API paid services) — free tier thì có.

### Benchmark nội bộ (`backend/scripts/llm_bench.py`, báo cáo `docs/reports/llm-bench-2026-09-23-r2.md`)

13 câu gold nháp (ngưỡng, phương pháp, hiệu lực, lấy mẫu, tần suất phải abstain, ngoài phạm vi), evidence pack = top-8 chunk theo cosine (Gemini embedding, lọc miền), structured output theo schema §8, temperature 0.

| Cấu hình | JSON hợp lệ | Citation ⊆ pack | Trích đúng nhãn kỳ vọng (khi pack có) | Số liệu ⊆ chunk trích | Abstain đúng | Cảnh báo dự thảo | Độ trễ TB | Chi phí TB/câu |
|---|---|---|---|---|---|---|---|---|
| **`gemini-3.8-flash` @low** | 13/13 | 13/13 | **6/6** | **13/13** | **11/13** | 3/3 | **3,1 s** | **$0,0030** |
| `gemini-3.8-flash` (medium) | 13/13 | 13/13 | 6/6 | 13/13 | 9/13 | 3/3 | 4,9 s | $0,0064 |
| `gemini-3.5-flash-lite` | 13/13 | 13/13 | 5/6 | 13/13 | 11/13 | 2/2 | 2,1 s | $0,0013 |
| `gemini-3.5-flash` | 13/13 | 13/13 | 5/6 | 13/13 | 9/13 | 3/3 | 6,4 s | $0,0142 |
| `gemini-3.1-pro-preview` | 13/13 | 13/13 | 5/6 | 13/13 | 11/13 | 1/1 | 8,0 s | $0,0144 |
| `gemini-2.5-flash` / `-pro` (lần 1) | 13/13 | 13/13 | — | 11/13 | 11/13 | 3/3 | 5,9 s / ≈ 12 s | $0,0033 / ≈ $0,02 |

Nhận xét:
- Mọi model đều tuân thủ schema và chỉ trích chunk trong pack; sau khi bỏ số lặp từ câu hỏi, **không model nào bịa số**.
- 2/13 "abstain sai" chung cho mọi model (G-WW-005 phương pháp trọng tài, G-EFF-001 ngày hiệu lực QCVN 19) thực chất là **retrieval thiếu chunk** (Mục 3.1 và registry fact không vào top-8) → abstain là hành vi đúng theo §8. `gemini-3.8-flash` mức medium/high còn abstain thêm 2 câu khi pack chỉ đủ một phần (thận trọng quá mức); ở mức **low** nó trả lời đúng cả hai (CO lò hơi ≥ 20 t/h nhiên liệu rắn; QCVN 40:2011 đã bị thay thế) — nhanh gấp 1,6 lần và rẻ một nửa.
- `gemini-3.5-flash-lite` nhanh/rẻ nhất nhưng một câu trả lời **mất dấu tiếng Việt** ("Gia tri gioi han bui…") → không dùng cho văn bản hiển thị, đủ tốt cho JSON hiểu câu hỏi.
- `gemini-3.1-pro-preview` không hơn Flash trên bộ này, trả lời cộc (điều kiện F/cột không nêu trong `conclusion`), chậm 2,6 lần, đắt 5 lần, còn preview và không có free tier.
- Họ 2.5 đang bị hạn chế truy cập cho dự án mới → không chọn.
- **Điểm nghẽn là retrieval, không phải LLM**: chunk kỳ vọng chỉ có trong top-8 ở 6/11 câu có nhãn kỳ vọng. Phase 2 (structured lookup cho câu ngưỡng, BM25 + alias, RRF) là đòn bẩy chính cho chất lượng trả lời.

## Decision (đề xuất)

| Vai trò | Model | Cấu hình | Lý do |
|---|---|---|---|
| Sinh câu trả lời (`/questions/ask`) | **`gemini-3.8-flash`** | `thinking_level=low`, temperature 0, `response_schema` = Answer §8, streaming SSE | Stable mới nhất, đúng nhất và tuân thủ tốt nhất trong bộ thử, 3 s, ≈ $0,003/câu (≈ $0,006 từ 2027) |
| Câu hỏi khó (cross-domain, applicability, hiệu lực nhiều mốc) | `gemini-3.8-flash` | `thinking_level=medium` (nâng theo intent) | cùng model, chỉ đổi mức thinking — không đổi prompt |
| Query understanding / routing | **`gemini-3.5-flash-lite`** | JSON schema §8, temperature 0; chỉ chạy khi rule-based extractor thiếu | 2 s, $0,0013; đầu ra là JSON nội bộ nên lỗi dấu không ảnh hưởng |
| LLM-judge (Phase 5) | `gemini-3.8-flash` | `thinking_level=high` (Batch nếu có) | tránh preview; nếu cần Pro thì `gemini-3.1-pro-preview` sau cờ cấu hình |
| Dự phòng | `anthropic` (`claude-opus-5`) qua `LLM_PROVIDER` | interface `LLMClient` chung | giữ khả năng đổi nhà cung cấp, không đổi tầng trên |

Quy tắc kèm theo:
1. **Ghim model id** (`gemini-3.8-flash`), không dùng alias `-latest`; mỗi câu trả lời ghi `model_id`, `thinking_level`, `prompt_version` vào audit (`answers`) để so sánh eval giữa các phiên bản.
2. **Validator §8 giữ nguyên** (citation ⊆ pack, số ⊆ chunk trích, cảnh báo dự thảo, tần suất abstain) — LLM chỉ là một tầng, không phải nguồn sự thật.
3. Cấu hình trong `backend/app/core/config.py` / `.env`: `LLM_PROVIDER`, `GEMINI_ANSWER_MODEL`, `GEMINI_ANSWER_THINKING`, `GEMINI_UNDERSTANDING_MODEL`, `GEMINI_JUDGE_MODEL`, `GEMINI_JUDGE_THINKING`.
4. Ngân sách: 1.000 câu/tháng ≈ $3 (trả lời) + $1,3 (hiểu câu hỏi) + embedding câu hỏi ≈ không đáng kể; đánh giá lại khi giá 3.8 Flash tăng gấp đôi từ 01/01/2027 hoặc khi có Pro stable.
5. Chạy lại `llm_bench.py` mỗi khi đổi model/prompt và trên gold set đầy đủ (cuối Phase 4) — báo cáo lưu `docs/reports/llm-bench-<ngày>.md`.

## Consequences

- Toàn bộ AI (embedding + LLM) phụ thuộc một nhà cung cấp; giảm rủi ro bằng interface `LLMClient`/`Embedder` và cấu hình provider.
- Câu hỏi người dùng (đã ẩn danh, không kèm dữ liệu cơ sở) và chunk văn bản công khai được gửi ra Google; cần bật billing (bậc trả phí) để dữ liệu không dùng cải thiện sản phẩm — PO xác nhận trong AI Studio.
- Thinking không tắt được → luôn có token thinking trong giá đầu ra; mức low giữ chi phí ≈ 400 token ra/câu.
- ADR-0005 mục Decision (Claude Opus 5) hết hiệu lực cho phần LLM; PLAN.md D5 đã ghi điều chỉnh.
