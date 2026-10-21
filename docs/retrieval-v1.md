# Hybrid retrieval v1 (Phase 2)

Triển khai PLAN.md §6. Mã: `backend/app/retrieval/` — `tokenize.py` (pyvi + alias), `query.py` (định tuyến miền, ý định, thực thể), `lexical.py` (BM25), `vector.py` (pgvector), `structured.py` (fast path ngưỡng), `expand.py` (graph expansion), `retriever.py` (RRF, bộ lọc, evidence pack). API: `POST /api/v1/search`, `GET /api/v1/index-versions`, `POST /api/v1/admin/index-versions/{label}/activate`. Đánh giá: `backend/scripts/eval.py`.

## Luồng một câu hỏi

| Bước | Việc | Ghi chú |
|---|---|---|
| 1. Hiểu câu hỏi (rule-based) | chuẩn hóa NFC; alias dài-nhất-trước từ từ điển (thông số, đơn vị, cột/vùng, nhiên liệu, nguồn tiếp nhận, loại hình, mã văn bản kể cả stub bị thay thế); regex lưu lượng `m³/ngày`, công suất `tấn hơi/giờ · MW · Kcal/giờ`; miền = từ khóa + miền của thông số/văn bản (wastewater · emission · cross_domain · out_of_scope · unknown); ý định theo thứ tự ưu tiên frequency → effectivity → method → sampling → definition → applicability → threshold → scope | `frequency` gắn chính sách **M-01** (D10); `needs_llm` khi miền unknown/cross hoặc ý định other (Phase 4 mới gọi Gemini Flash-Lite) |
| 2. Structured lookup | ý định ngưỡng + thông số → `limit_rules` lọc cột, nhóm lưu lượng, dòng T-P theo nguồn tiếp nhận, nhiên liệu/công suất (dùng chung `applicability/matching.py`); xếp dòng đặc hiệu trước; tối đa 8 dòng | chunk của dòng được cộng +1,0 vào điểm RRF → luôn đứng đầu evidence |
| 3. BM25 | token pyvi của `context_text` + token chuẩn (`param_bod5`, `unit_mg_l`, `doc_qcvn_40_2025`, `ctype_table_row`); truy vấn = token câu hỏi + token chuẩn từ bước 1; top-50, lọc miền (`{miền, both}`) | in-process `rank_bm25`, cache `data/derived/index/bm25-<sig>.pkl`, chữ ký = md5 nội dung mọi chunk → tự rebuild sau ingest |
| 4. Vector | Gemini `RETRIEVAL_QUERY` @1536 → pgvector cosine top-50, lọc miền | lỗi API → tự chuyển BM25-only kèm cảnh báo |
| 5. RRF + boost | `1/(60+rank)` mỗi nguồn; nhân hệ số theo ý định (ngưỡng → table_row 1,3; hiệu lực → registry_fact 1,4…) | |
| 6. Bộ lọc hiệu lực | `as_of_date` trước ngày hiệu lực (registry fact) → loại chunk của văn bản đó, trừ ý định hiệu lực/áp dụng | §8 quy tắc 5 |
| 7. Graph expansion (≤ k/2) | chú thích cùng bảng, khoản cha, định nghĩa thuật ngữ trong câu hỏi, điều khoản chuyển tiếp + registry fact + Lời nói đầu của văn bản liên quan (kể cả văn bản thay thế văn bản được hỏi), 2 khoản dẫn chiếu M-01 | qua `PostgresGraphStore` (Neo4j tương đương khi dựng) |
| 8. Evidence pack | ≤ 18.000 ký tự (≈ 6k token); mỗi mục: `chunk_id`, nhãn trích dẫn, văn bản, loại, miền, `text_status`, điểm, nguồn (bm25/vector/structured/expansion) | cảnh báo: văn bản dự thảo, ngoài phạm vi, M-01, chưa có index active |

Reranker `bge-reranker-v2-m3` **không dùng** (D17): Recall@10 đã đạt 1,0 trên gold nháp và reranker CPU sẽ vượt ngân sách 1,5 s.

## Kết quả (gold nháp 13 câu, 2026-09-23 — `docs/reports/retrieval-eval-2026-09-23-*.md`)

| Cấu hình | Recall@5 | Recall@10 | Định tuyến miền | Chính sách abstain | Độ trễ TB / max |
|---|---|---|---|---|---|
| BM25 + structured + expansion (không vector) | 0,909 | 0,909 | 13/13 | 3/3 | 31 / 70 ms |
| **Hybrid (BM25 ∪ Gemini vector) + structured + expansion** | **1,0** | **1,0** | **13/13** | **3/3** | 706 / 2.395 ms (max = lần gọi embedding đầu, client lạnh) |

Trước khi sửa, 5 câu "trượt" là do nhãn gold viết khác định dạng hệ thống (thiếu "Chương IV", "(CO)" thay vì "(Cacbon monoxit)", định dạng registry fact) và nhãn Lời nói đầu in tiếng Anh "Preamble" — đã sửa gold (ghi chú trong file) và grammar. Bài học cho gold set chính thức: **nhãn kỳ vọng phải copy đúng `citation_label` của hệ thống** (xem `data/gold/README.md`).

## Kích hoạt index (UC-08)

`python backend/scripts/ingest.py activate --label gemini-embedding-001@1536` hoặc `POST /api/v1/admin/index-versions/{label}/activate` — một index `active` duy nhất; lệnh cũng rebuild BM25 (`ingest.py bm25` chỉ rebuild). Backend nạp BM25 + alias lúc khởi động (`app.main` startup) nên `/search` đầu tiên không tốn thời gian dựng.

## Giới hạn / việc tiếp theo

- Gold nháp chỉ 13 câu do nhóm kỹ thuật soạn; Recall@10 = 1,0 là trên tập nhỏ — đánh giá lại với gold set chuyên gia (cuối Phase 4).
- Alias thiết bị QCVN 19 chưa có (chọn theo nhiên liệu/công suất); câu hỏi nêu tên thiết bị lạ (ví dụ "lò nung vôi") dựa vào BM25/vector.
- Câu hỏi cross-domain / unknown chưa gọi LLM hiểu câu hỏi (Phase 4).
- Độ trễ vector phụ thuộc Gemini (≈ 0,4–0,5 s/câu ấm); nếu cần < 0,3 s cân nhắc cache embedding câu hỏi phổ biến.
