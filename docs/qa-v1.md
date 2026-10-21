# Hỏi đáp có trích dẫn v1 (Phase 4)

Triển khai PLAN.md §8 (query understanding, evidence assembler, prompt v1, citation validator, abstention, SSE, audit log). Mã: `backend/app/qa/` (`models.py` schema I/O, `prompts.py` prompt v1 + mẫu abstain, `validator.py` 7 quy tắc, `service.py` pipeline), `backend/app/llm/client.py` (Gemini structured output + streaming, `FakeLLM` cho test), API `backend/app/api/questions.py`, UI `frontend/src/app/page.tsx`. Đánh giá: `backend/scripts/qa_eval.py`. Test: `backend/tests/test_qa.py` (không gọi Gemini).

## Luồng một câu hỏi

| Bước | Việc | Không bao giờ |
|---|---|---|
| 1. Hiểu câu hỏi | rule-based (`retrieval/query.py`): miền, ý định, thông số, cột/vùng, lưu lượng, nhiên liệu/công suất, văn bản, `as_of_date`. Chỉ khi miền `unknown`/`cross_domain` hoặc ý định `other` mới gọi `gemini-3.5-flash-lite` (schema `LLMUnderstanding` §8) rồi tìm lại với miền đã chốt | LLM không quyết định thay rule khi rule đã chắc |
| 2. Evidence pack | `Retriever.search` (Phase 2): structured lookup → BM25 ∪ vector → RRF → lọc hiệu lực → graph expansion (thêm mới: **định nghĩa cột/vùng** QCVN 40 Mục 1.3.3.x / QCVN 19 Mục 1.3.13.x khi đã nhận diện cột) → ≤ 18k ký tự, `k = 10` | không có chunk ngoài pack |
| 3. Trả lời theo chính sách (không LLM) | ngoài phạm vi → mẫu abstain; tần suất → **M-01** (D10): abstain + trích Điều 16.3 / 17.5 TT 10/2021 + chỉ dẫn NĐ 08/2022; cần làm rõ → hỏi lại; pack rỗng → abstain | không gọi Gemini cho các trường hợp này |
| 4. Sinh câu trả lời | `gemini-3.8-flash`, `temperature 0`, `response_schema = Answer` (conclusion, basis[{text, citations}], conditions, citations, missing_information, warnings, abstained, abstain_reason); system prompt ổn định (cache), câu hỏi bọc `<question>` như dữ liệu, evidence là các thẻ `<chunk id label document [BẢN DỰ THẢO]>`; `thinking_level=low`, nâng `medium` cho ý định hiệu lực/áp dụng và cross-domain; streaming JSON → sự kiện `delta` | không suy diễn ngưỡng, không đổi đơn vị, không kết luận đạt/không đạt |
| 5. Validator (7 quy tắc §8) | (1) khẳng định phải có citation; (2) mọi `chunk_id` ∈ pack; (3) mọi **số** trong conclusion/basis phải có trong chunk được trích (chuẩn hóa `0,05`/`0.05`, `3.000`→`3000`, `50.0`→`50`; số lặp lại từ câu hỏi/`as_of_date` được phép); (4) trích miền khác → cảnh báo, chỉ có miền khác → reject; (5) văn bản không hiệu lực tại `as_of_date` đã bị loại từ retrieval; (6) trích văn bản `draft` → tự thêm cảnh báo "bản dự thảo"; fact `admin_declared` → cảnh báo "do quản trị viên khai báo"; câu hiệu lực của TT45/TT10 mà chỉ trích nội dung dự thảo (không trích `registry_fact`) → reject; (7) reject → sinh lại **1 lần** kèm danh sách lỗi; vẫn fail → abstain chuẩn, `status=failed`, evidence vẫn hiển thị | không sửa số liệu của mô hình, chỉ chấp nhận/từ chối |
| 6. Audit | `query_logs` (câu hỏi, `as_of_date`, miền/ý định, understanding, chunk đã lấy + nguồn + rank, structured hits, trạng thái, đường đi policy/llm/fallback, prompt version `qa-v1.1` (2026-09-24; trước đó `qa-v1.0`), model, thinking, usage, latency, kết quả validator, số lần sinh), `answers` (JSON), `answer_citations`, `feedback` (👍/👎 + bình luận) | không ghi API key |

Mọi câu trả lời kèm disclaimer: thông tin tra cứu có trích dẫn, không phải kết luận pháp lý, hệ thống không đánh giá đạt/không đạt.

## API

| Method & path | Mục đích |
|---|---|
| `POST /api/v1/questions/ask` | SSE: `stage{understanding}` → `understanding` → `evidence{evidence, structured_hits, warnings}` → `stage{generating}` → `delta{text}`* → (`stage{regenerating}`) → `stage{validating}` → `answer{QAResponse}` → `done`; lỗi → `error{code, query_id, message}` (câu vẫn được ghi `query_logs.status=error`); header `X-Query-Id`, `X-Request-Id` |
| `POST /api/v1/questions/answer` | cùng pipeline, trả `QAResponse` một lần (dùng cho eval/test) |
| `GET /api/v1/questions/{query_id}` | đọc lại câu hỏi/câu trả lời đã ghi |
| `POST /api/v1/feedback` | `{query_id, rating: 1 \| -1, comment?}` |
| `GET /api/v1/history?limit&offset` | lịch sử câu hỏi của trình duyệt gửi `X-Client-Id` (UUID trong localStorage); thiếu header → `[]` |
| `GET /api/v1/me/quota` | `{limit, used, remaining, over_quota, budget{limit_usd, spent_usd, ratio}, enforced: false}` — chỉ để UI hiện thông báo mềm |
| `GET /api/v1/admin/query-logs?status&path&feedback&q&from&to&client_id&limit&offset` | nhật ký có lọc (`{total, items}`), `/export.csv`, `/{query_id}` chi tiết — cần `X-Admin-Token` (xem `docs/dev-setup.md` §5) |

Body: `{question (3–2000 ký tự), as_of_date?, k? (3–20, mặc định 10)}`. `QAResponse` gồm `query_id, understanding, llm_understanding?, structured_hits, evidence[], answer, citations[{chunk_id, citation_label, document_code, text_status}], validation{ok, errors, warnings, rules_checked}, status (answered | abstained | failed), path (policy | llm | fallback), attempts, model, thinking_level, prompt_version, usage, cost_usd, latency_ms, disclaimer`. Mọi yêu cầu nên gửi `X-Client-Id` (UUID do trình duyệt sinh) để câu hỏi gắn với lịch sử và `usage_daily`; không có thì tính theo IP.

## UI chat (`/`)

Trang mặc định của frontend: ô hỏi + thời điểm áp dụng; mỗi lượt hiển thị tiến trình theo sự kiện SSE (hiểu câu hỏi → n đoạn căn cứ → đang soạn (đếm ký tự) → kiểm tra trích dẫn), chip miền/ý định/thông số/cột/lưu lượng, kết luận với chip trích dẫn `[n]`, căn cứ, điều kiện áp dụng, cần thêm thông tin, cảnh báo (dự thảo, quản trị viên khai báo, M-01, miền khác), lỗi validator nếu bị từ chối, model/độ trễ/token, 👍/👎, disclaimer. Panel phải: bảng dòng ngưỡng khớp (structured hits) và danh sách căn cứ; bấm `[n]` hoặc mục căn cứ → xem toàn văn chunk, đường dẫn cấu trúc (`GET /graph/evidence-path/{chunk_id}`) và link sang `/documents/{id}`.

Bổ sung 2026-09-24 (M20): sidebar **Lịch sử** (theo mã trình duyệt ẩn danh, mở lại câu cũ không gọi LLM), nút **Sao chép** (Markdown có kết luận, căn cứ `[n]`, danh sách trích dẫn, disclaimer, mã tra cứu) và **In / PDF** (chỉ lượt đó kèm đoạn trích), nút **Dừng** (AbortController; máy chủ vẫn hoàn tất và ghi log), lỗi hiển thị dạng tiêu đề + gợi ý + mã tra cứu + **Thử lại**, 👎 mở ô bình luận, thông báo mềm khi vượt quota/ngân sách ≥ 80 % (`GET /me/quota`), trên màn hình < 1024 px panel căn cứ và lịch sử là drawer, dòng thông báo xử lý bởi Gemini và lưu nhật ký ẩn danh (M12), `/?q=` điền sẵn câu hỏi.

## Kết quả trên gold nháp (13 câu)

Chạy `python backend/scripts/qa_eval.py` (Gemini thật, ghi vào `query_logs`) — `docs/reports/qa-eval-2026-09-23.md` (lần 1) và `-r2.md` (sau khi sửa thứ tự chính sách, `k=10`, thêm định nghĩa cột vào expansion):

| Chỉ số | Lần 1 | **Lần 2** |
|---|---|---|
| Trả lời / abstain / thất bại | 9 / 4 / 0 | **10 / 3 / 0** |
| Câu khẳng định có citation hợp lệ (validator quy tắc 1–3) | 10/10 | **10/10** |
| Trích đúng nhãn kỳ vọng (câu có nhãn) | 11/11 | **11/11** |
| Validator đạt ngay lần 1 / phải sinh lại | 10/10 / 0 | **10/10 / 0** |
| Abstain đúng chính sách (`must_abstain`) | 12/13 | **13/13** |
| Cảnh báo kỳ vọng có mặt (dự thảo, quản trị viên khai báo, thiếu thông tin, ngoài phạm vi) | – | **6/7** |
| Số liệu kỳ vọng có trong kết luận/căn cứ/điều kiện | 4/10 | 7/10 (3 câu "trượt" do `expected_answer` viết số hiệu điều/ngày theo định dạng khác — câu trả lời đúng) |
| Độ trễ TB / p90 / max | 4,9 / 8,3 / 10,7 s | 4,6 / 7,7 / 11,3 s (policy ≈ 0,5 s; LLM `low` 3–4,6 s; `medium` cho hiệu lực/áp dụng 7,7–11,3 s) |
| Chi phí (giá niêm yết) | 0,0517 USD / 13 câu | 0,0538 USD / 13 câu ≈ **0,0054 USD/câu qua LLM** |

Điểm đáng chú ý lần 2: G-EM-001 ("vùng hạn chế phát thải") trả lời đúng **Cột B = 350 mg/Nm³** và trích thêm định nghĩa Mục 1.3.13.2 (lần 1 liệt kê cả 3 cột và xin làm rõ); G-EFF-002 ("QCVN 40:2011 còn hiệu lực không?") trả lời được nhờ có Điều 3 Khoản 1 TT 06 trong pack (lần 1 abstain vì `k=8`); G-OOS-002 (tần suất không khí xung quanh) nay abstain theo mẫu ngoài phạm vi thay vì M-01. Mọi câu trích TT45/TT10 đều có cảnh báo dự thảo; câu hiệu lực TT45 trích `registry_fact` + cảnh báo "do quản trị viên khai báo".

Test offline (`pytest tests/test_qa.py`, FakeLLM): 9 test — chuẩn hóa số, quy tắc 1–4 và 6, chính sách M-01/ngoài phạm vi không gọi LLM, đường đi LLM (đạt ngay; reject → sinh lại kèm lỗi; reject 2 lần → abstain `failed`), hiệu lực TT45 phải trích Sổ đăng ký, audit log, SSE + feedback + logs.

## Đánh giá nghiên cứu (2026-09-24)

Gold set v2 ba tầng (`data/gold/README.md`) và gói `backend/app/evaluation/` (`scripts/gold.py`, `scripts/evaluate.py`): chân lý neo vào tệp nguồn thô, Tầng A 393 câu tra bảng sinh từ bộ đọc độc lập parser (đối chiếu 1.125 ô: 0 lệch), metric retrieval hai tầng (ranker/pack) trên relevance phân cấp, metric câu trả lời (citation P/R/completeness, số liệu có căn cứ, abstention như bộ phân loại, robustness), LLM-judge + người kiểm 20 %, thống kê (bootstrap cụm, McNemar, Holm), ma trận ablation C0–C8 với `EvalOptions` (validator observe-only, tắt audit). `scripts/eval.py`/`qa_eval.py` chỉ còn là wrapper. Phát hiện đầu tiên trên dev: hybrid C2 hit@10 0,97 nhưng C3/C4 0,92 vì structured lookup trả về mọi dòng thiết bị của thông số (+1,0) đẩy dòng đúng khỏi top-10 ở 6 câu QCVN 19; lỗi `numbers()` coi "0.001" là 1.000 (đã sửa).

## Giới hạn / việc tiếp theo

- Gold nháp 13 câu do nhóm kỹ thuật soạn; các chỉ số trên chưa phải nghiệm thu — cần gold set chuyên gia (120 câu, §12) và LLM-judge faithfulness (Phase 5).
- Thiết bị QCVN 19 chưa có alias (ví dụ "lò hơi đốt trấu" → mô hình liệt kê 2–3 nhóm thiết bị và yêu cầu làm rõ thay vì chọn 1.3) — bổ sung alias thiết bị vào từ điển (Phase 5, ontology CRUD).
- Độ trễ do Gemini (≈ 3–4 s `low`, 8–10 s `medium` cho câu hiệu lực/áp dụng); UI đã stream tiến trình. Nếu cần < 5 s mọi câu: giữ `low` cho hiệu lực và tăng k thay vì thinking.
- Validator kiểm số theo chuỗi, chưa kiểm ngữ nghĩa (ví dụ gán nhầm cột dù số đúng) — dựa vào LLM-judge + chuyên gia ở Phase 5.
- `ask` chạy pipeline trong thread riêng (SSE); chưa có giới hạn tốc độ/đồng thời — Phase 5 deployment.
