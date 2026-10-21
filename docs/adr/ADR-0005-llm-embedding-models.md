# ADR-0005 — Claude Opus 5 cho QA/query understanding; bge-m3 + bge-reranker-v2-m3 chạy local

**Trạng thái:** Accepted, đo lại sau eval (PLAN.md D5) · **Ngày:** 2026-09-23 · **Điều chỉnh:** embedding → Gemini (bổ sung 2026-09-24 dưới đây); LLM hỏi đáp → Gemini (**ADR-0011**, 2026-09-23, thay cho Claude Opus 5 ở mục Decision)

## Context
Cần suy luận pháp lý chính xác bằng tiếng Việt, đầu ra JSON có schema để validator kiểm tra, và không gửi văn bản ra ngoài cho bước embedding.

## Decision
- **LLM:** `claude-opus-5` qua Anthropic Python SDK; adaptive thinking; **structured outputs** (`output_config.format`) cho query understanding và answer JSON; **prompt caching** cho system prompt + hướng dẫn định dạng (ổn định, đặt trước breakpoint; evidence và câu hỏi đặt sau); **Batch API** cho tác vụ offline (LLM-judge, đề xuất trích xuất).
- **Không** dùng tính năng citations của API (không dùng chung được với structured outputs); citation do **validator của hệ thống** kiểm tra theo `chunk_id`.
- **Embedding:** `BAAI/bge-m3` (đa ngữ, 1024 chiều) local; **Reranker:** `BAAI/bge-reranker-v2-m3`.
- Sau khi có gold set và eval (Phase 5): đo Sonnet 5 / Haiku 4.5 cho bước phân loại miền nếu chi phí là vấn đề — quyết định bằng số liệu, không đổi trước.

## Consequences
- Cần `ANTHROPIC_API_KEY`; mọi lời gọi ghi `model`, `usage`, `prompt_version` vào audit.
- Extras `ml` kéo torch (~2 GB); Phase 0–1 không cần.

## Bổ sung 2026-09-24 — Embedding chuyển sang Gemini API (PO quyết định)

**Quyết định:** dùng **`gemini-embedding-001`** (GA, text-only, > 100 ngôn ngữ, đầu vào tối đa 2.048 token, Matryoshka Representation Learning) qua SDK `google-genai`, với:
- `output_dimensionality = 1536` — pgvector chỉ đánh index HNSW/IVFFlat cho vector ≤ 2.000 chiều, nên không dùng mặc định 3.072; 1536 là mức Google khuyến nghị (768/1536/3072), giữ chất lượng gần như nguyên vẹn và giảm nửa dung lượng so với 3072.
- `task_type = RETRIEVAL_DOCUMENT` khi embed chunk, `RETRIEVAL_QUERY` khi embed câu hỏi (mô hình bất đối xứng).
- Không chọn `gemini-embedding-2-preview` (đa phương thức, ra 03/2026) vì còn preview; corpus chỉ là văn bản. Có thể nâng cấp sau khi GA — vector phải re-embed toàn bộ (đổi `index_version`).
- Giữ `EMBEDDING_PROVIDER=local` (bge-m3) làm đường dự phòng offline; interface `Embedder` chung.

**Chi phí/giới hạn:** $0,15 / 1M token đầu vào (batch $0,075); free tier ~1.500 request/ngày. Corpus MVP ≈ 1.300 chunk → vài chục nghìn token → gần như miễn phí; câu hỏi người dùng ≈ 30–60 token/lần.

**Hệ quả:**
- Chunk văn bản pháp luật (công khai) được gửi ra Google; không gửi dữ liệu người dùng (bối cảnh cơ sở) — chỉ câu hỏi đã ẩn danh. Nếu dùng free tier, dữ liệu có thể được Google dùng để cải thiện dịch vụ → khuyến nghị bật billing cho dự án Gemini.
- Chunk dài hơn 2.048 token phải cắt phần `context_text` gửi đi (giữ `text` nguyên trong DB) — pipeline kiểm tra độ dài trước khi embed và ghi cảnh báo.
- Cột `evidence_chunks.embedding` là `vector(1536)`; `index_versions` ghi `embedding_model` + `embedding_dim` để không trộn vector khác mô hình.
- Reranker vẫn là `bge-reranker-v2-m3` local (Phase 2); cân nhắc bỏ nếu Recall@10 đã đạt với Gemini + BM25.

**Reranker (D17, 2026-09-23):** không đưa `bge-reranker-v2-m3` vào runtime — hybrid BM25 + Gemini vector + structured lookup đạt Recall@10 = 1,0 trên gold nháp (`docs/retrieval-v1.md`), reranker CPU sẽ vượt ngân sách 1,5 s; xem lại khi có gold set chuyên gia.

**Kết quả lần embed đầu (2026-09-24, `ingest.py embed`):** 1.259 chunk, 732 k ký tự, 40 request (lô 32), 0 retry 429, 0 chunk vượt `EMBEDDING_MAX_CHARS=6000`; vector được chuẩn hóa L2 trước khi ghi (MRL cắt còn 1536 chiều không tự chuẩn hóa). Chỉ chunk chưa có vector hoặc có vector của mô hình khác mới được gửi; mỗi lô commit riêng nên chạy dở có thể tiếp tục; `index_versions` ghi `gemini-embedding-001@1536` (`inactive` cho tới khi admin kích hoạt). Mã: `backend/app/retrieval/{embedder,index}.py`.
