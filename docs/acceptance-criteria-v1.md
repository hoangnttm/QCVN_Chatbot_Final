# Tiêu chí nghiệm thu v1

Kế thừa `software-context.md` §9, cụ thể hóa theo corpus thực tế và các quyết định D1–D13.

## Nghiệm thu MVP (cuối Phase 5)

| Hạng mục | Tiêu chí | Cách đo |
|---|---|---|
| Corpus | 5 văn bản trong registry có hash, `text_status`, version; cả 5 `official` (PO xác nhận 2026-09-24); ngày TT45/TT10 là registry fact có audit | `pytest tests/test_registry.py`; kiểm tra admin UI |
| Cấu trúc | 100 % điều/khoản/điểm/mục/phụ lục của phần in-scope có structural path và span; PDF có `pdf_page` | Coverage report của ingest = 100 %; 0 dòng không thuộc node |
| Bảng | 100 % dòng logic của 9 bảng QCVN + bảng in-scope TT10 có JSON theo spec; 0 ô không parse im lặng; chuyên gia kiểm 30 dòng/bảng ngưỡng: 0 sai | Snapshot test + biên bản kiểm của chuyên gia |
| Citation | 100 % câu trả lời khẳng định có ≥ 1 citation hợp lệ (chunk_id thuộc evidence pack); mọi số trong câu trả lời có trong chunk được cite | Validator log trên gold set; Citation precision ≥ 0,95 |
| Retrieval | Recall@10 ≥ 0,85 trên gold set (theo `citation_label`) | `scripts/eval.py` |
| Hiệu lực | Không dùng văn bản/rule không hiệu lực tại `as_of_date` khi đã biết `EffectivePeriod`; câu hỏi ngày ký/hiệu lực TT45/TT10 cite `registry_fact` + cảnh báo | 15 câu hiệu lực trong gold set đúng 100 % |
| Domain routing | Phân biệt đúng nước thải/khí thải/cross/out-of-scope trên gold set ≥ 0,95; không lấy tần suất môi trường nền cho câu hỏi cơ sở | Confusion matrix từ eval run |
| Abstention | 15 câu thiếu căn cứ/ngoài phạm vi abstain đúng 100 %; mọi câu tần suất abstain có chỉ dẫn (M-01) | Eval run |
| Applicability | 20 kịch bản do chuyên gia soạn cho đúng QCVN ứng viên + missing info; không xuất "đạt/không đạt" | Test kịch bản |
| Admin | Xem/sửa trạng thái, duyệt LimitRule/TransitionRule/REPLACES/REPEALS; bulk approve theo bảng kèm kiểm mẫu ≥ 10 % | UAT checklist |
| Audit | Mỗi câu trả lời có query, evidence từng tầng, prompt/model version, usage, citations, validator result, timestamp | Kiểm tra `query_logs`/`answers` |
| Cảnh báo nguồn ngày | 100 % câu trả lời về ngày ký/hiệu lực TT45/TT10 trích chunk Sổ đăng ký và có warning "quản trị viên khai báo"; warning "bản dự thảo" chỉ khi corpus có văn bản `draft` (từ 2026-09-24: không có) | Validator rule 6 |

## Nghiệm thu Phase 1 (tuần 2–3) — trạng thái 2026-09-24

| Tiêu chí | Trạng thái |
|---|---|
| Pipeline `register → sanitize → split → parse_structure → parse_tables → extract_rules → extract_relations → chunk → resolve_entities` | ✅ `backend/app/ingestion/`, CLI `backend/scripts/ingest.py {pdf,structure,tables,all,report}`, artefact JSON tại `data/derived/<doc>/` |
| Coverage 100 % dòng, grammar-driven | ✅ TT06, QCVN40, QCVN19 (Markdown), TT45 (docling) — test `test_structure.py`, `test_pdf_documents.py` |
| Bảng → JSON mỗi dòng (spec 4.4), 0 ô không parse | ✅ 9 bảng QCVN: 4 + 64 + 67 + 64 / 253 + 113 + 76 + 17 + 16 dòng; giá trị kiểm tay (CO 1.2.1 B = 350/O₂ 6 %, T-P 4.2 A = 8,0, pH 6–9, Clorua ngoại lệ…) — `test_tables.py` |
| LimitRule | ✅ 213 (QCVN 40) + 912 (QCVN 19) = 1.125, mỗi rule mang zone, flow/equipment/fuel/capacity/receiving-body/sector conditions, citation label tới Cột |
| Quan hệ pháp lý & chuyển tiếp trích từ văn bản, đối chiếu registry | ✅ 0 lệch: TT06 11 REPEALS, QCVN40 11 REPLACES, QCVN19 7 + 5 partial, TT45 7 + 5 partial; 6 TransitionRule/TT; ngày hiệu lực TT06 = 01/9/2025 từ text; TT45 (dự thảo) không lấy ngày từ text |
| Entity resolution | ✅ 0 chưa resolve trên 4 văn bản (thông số, đơn vị, thiết bị, ngành + thông số đặc trưng/điều kiện cột 4–5 PL2) |
| EvidenceChunk | ✅ 23 + 234 + 462 + 29 = 748 chunk (kể cả 4 `registry_fact` của TT45), `context_text` gồm label + cha + header bảng + điều kiện kế thừa |
| PDF → text (docling, OCR tắt, lô 30 trang, cache, `HF_HUB_OFFLINE` khi đã có model) | ✅ TT45 (3 trang) và TT10 (192 trang, 7 lô, ~35 phút CPU) — marker khoản/điểm được giữ, page map cho mọi node |
| TT10: grammar docling (heading `## `, tiêu đề tách dòng, đảo thứ tự Điều 1/Chương I, khoản in thành heading, bảng tiếp trang) + scope partition | ✅ coverage 100 % (1.627 dòng), 10 Chương / 47 Điều đúng chỗ, 27 Phụ lục, 62 bảng (17 bảng tiếp trang được gộp), 505 node `structure_only` không chunk, 511 chunk in-scope (wastewater 121 / emission 176 / both 191); quan hệ khớp registry (TT 24/2017, Chương III TT 43/2015) — `test_tt10.py` |
| MonitoringRequirement từ TT10 (Đ3, Đ16–18, Đ22, PL5, PL6) | ✅ 2026-09-23: 29 yêu cầu (method_precedence 9, sampling_condition 7, sampling_port 5, direct_measurement 8… ) từ bản đồ curated `data/grammars/tt_10_2021.monitoring.yaml` neo vào node đã parse (anchor nguyên văn phải khớp, nếu không → cảnh báo chặn); `text` nguyên văn + `summary`/`conditions` có số liệu (≥ 50 % công suất, B ≥ 2D, lỗ 90–110 mm, ≥ 3 phép đo × 15 phút, hệ số ppm→mg/Nm³…); bảng `monitoring_requirements`, `review_status=pending` chờ chuyên gia (sheet 10). Chưa có loại `cems` (Chương VII) — sheet Phát hiện — `test_tt10.py` |
| Báo cáo parser quality | ✅ `docs/reports/ingestion-<ngày>.md` (`ingest.py report`) |
| Lưu artefact vào PostgreSQL (system of record, ADR-0002) | ✅ 2026-09-24: schema Alembic `5e0d0b7a4334` (13 bảng, `vector(1536)` + HNSW cosine, extension vector/pg_trgm/unaccent); `ingest.py load` nạp idempotent theo văn bản: 44 documents (5 + 39 stub), 5 versions, 15 registry facts (7 admin_declared), 109 quan hệ (registry + text, 0 target không resolve), 1.226 node, 71 bảng / 1.217 dòng, 1.125 LimitRule (0 orphan), 14 TransitionRule, 1.259 chunk — `test_db_load.py` |
| Embedding → pgvector + `index_versions` (ADR-0005) | ✅ 2026-09-24: `ingest.py embed` — 1.259/1.259 chunk bằng `gemini-embedding-001` @1536 (40 request, 0 retry, 0 chunk bị cắt, ≈ 0,24 M token ≈ $0,04); `index_versions` `gemini-embedding-001@1536` = `inactive` (kích hoạt thủ công UC-08, Phase 2); reload giữ vector của chunk không đổi — `test_embed.py`. Thử 5 câu hỏi tiếng Việt (BOD5 cột B, bụi lò hơi than, hiệu lực TT45, lấy mẫu nước thải, điều khoản chuyển tiếp): top-1 cosine đều đúng nguồn (Bảng 1 TT 1 QCVN 40; Bảng 2 Bụi thiết bị 1.2.1 QCVN 19; registry_fact TT45; Bảng 25 PL 4.1 TT10; TT06 Đ3.1) |
| Chuyên gia kiểm 30 dòng/bảng ngưỡng | 🟡 hồ sơ sẵn sàng, **chờ chuyên gia**: `data/review/review-pack-v1-2026-09-23.xlsx` (16 sheet: 4 bảng ngưỡng 94 dòng mẫu ↔ 1.125 rule, phương pháp 142, ngành 54, TEF 33, quan hệ 109, chuyển tiếp 14, sự kiện đăng ký 15, ánh xạ từ điển 230, quan trắc TT10 29, phát hiện 6) sinh bằng `review_pack.py` (mẫu chọn theo rủi ro, deterministic); nhập lại bằng `review_import.py` → `review_status` + `review_decisions` + bulk-approve theo bảng khi mẫu 0 sai (§11). Quy trình: `docs/review/quy-trinh-kiem-tra-chuyen-gia.md` — `test_review_pack.py` |
| Trang `/documents/[id]` (frontend) | ✅ 2026-09-23: API đọc `GET /api/v1/documents`, `/documents/{id}` (sổ đăng ký, quan hệ, chuyển tiếp, đếm, trạng thái duyệt), `/documents/{id}/tree`, `/documents/{id}/monitoring`, `/tables/{code}` (dựng từ `table_rows` + rule), `/evidence/{chunk_id}` — `test_api.py`; frontend Next.js 16 (`frontend/`): `/documents`, `/documents/[id]` (nhãn dự thảo, sổ đăng ký với nguồn khai báo, quan hệ, chuyển tiếp, yêu cầu quan trắc, cây cấu trúc có trang), `/documents/[id]/tables/[code]` (bảng dựng lại từ dữ liệu dòng, điều kiện/ngoại lệ/chú thích, trạng thái duyệt). Chưa có auth/admin (Phase 5) |

## Nghiệm thu Phase 2 (hybrid retrieval) — trạng thái 2026-09-23

| Tiêu chí (PLAN §13) | Trạng thái |
|---|---|
| Tokenizer tiếng Việt + alias expansion | ✅ pyvi + `AliasIndex` từ toàn bộ từ điển (thông số, đơn vị, cột/vùng, nhiên liệu, nguồn tiếp nhận, loại hình, mã văn bản kể cả stub bị thay thế); token chuẩn `param_*`, `unit_*`, `doc_*` gắn cả phía tài liệu — `docs/retrieval-v1.md` |
| BM25 | ✅ in-process `rank_bm25`, cache theo chữ ký nội dung, 31 ms/câu |
| Embedding → pgvector | ✅ (Phase 1b) — vector Gemini @1536, cosine top-50 |
| RRF, filters, structured lookup | ✅ RRF k=60 + boost theo ý định; bộ lọc miền và `as_of_date`; fast path `limit_rules` (cột, lưu lượng, nguồn tiếp nhận, nhiên liệu/công suất, xếp dòng đặc hiệu trước) |
| Graph expansion (tạm dùng bảng Postgres) | ✅ chú thích bảng, khoản cha, định nghĩa, chuyển tiếp/registry fact/Lời nói đầu qua quan hệ PROMULGATES/REPLACES, dẫn chiếu M-01 |
| Reranker | ⛔ không dùng (D17): Recall@10 = 1,0 đã đạt, reranker CPU vượt ngân sách độ trễ |
| `/search`, `/evidence/{id}` | ✅ `POST /api/v1/search` (SearchResult gồm understanding, structured_hits, evidence, warnings, latency từng bước); `/evidence/{chunk_id}` (Phase 1) |
| index_versions + activate | ✅ `POST /admin/index-versions/{label}/activate`, `ingest.py activate --label …`; `gemini-embedding-001@1536` đang **active** |
| Recall@10 ≥ 0,85 trên gold | ✅ **1,0** (11/11 câu có nhãn; BM25-only 0,909); định tuyến miền 13/13; abstain policy 3/3 — `scripts/eval.py`, `docs/reports/retrieval-eval-2026-09-23-hybrid.md`. Lưu ý: gold mới 13 câu nháp; 4 nhãn gold + nhãn "Preamble" đã sửa cho khớp citation_label hệ thống |
| Latency `/search` < 1,5 s | ✅ TB 0,7 s; lần gọi đầu 2,4 s do khởi tạo client Gemini (cần warm-up khi khởi động) |

## Nghiệm thu Phase 3 (knowledge graph & applicability) — trạng thái 2026-09-23

Bắt đầu sớm theo quyết định PO 2026-09-23 (dùng dữ liệu hiện có; gold set retrieval dời về cuối Phase 4).

| Tiêu chí (PLAN §13) | Trạng thái |
|---|---|
| Neo4j schema + `build_graph.py` rebuild idempotent | ✅ mã: `backend/app/graph/build_graph.py` (21 label, ≈ 30 loại edge theo ontology v1, `projection_version` + xóa node cũ), CLI `scripts/build_graph.py`, `GraphStore` (Postgres + Neo4j) — `docs/graph-schema-v1.md`; ✅ 2026-09-24 **đã dựng graph thật** trên instance `qcvn_env_chatbot` (5.913 node / 17.106 edge: 1.259 EvidenceChunk, 1.125 LimitRule, 231 Condition, 29 MonitoringRequirement, 14 TransitionRule, 26 ApplicabilityRule; 48 REPLACES/REPEALS, 2.434 HAS_EVIDENCE); `test_graph.py` pass: rebuild idempotent, đường bằng chứng Neo4j = Postgres |
| Nạp REPLACES/REPEALS/TransitionRule/Definition vào graph | ✅ trong projection (quan hệ có scope/target_part/condition; TransitionRule, Definition, MonitoringRequirement, RegistryFact, ApplicabilityRule → evidence) |
| Context schema 2 miền | ✅ 14 trường nước thải, 12 trường khí thải trong `applicability_v1.yaml`, có `required_for` từng rule; API `GET /applicability/schema/{domain}` sinh option từ từ điển (54 loại hình, 94 thiết bị) |
| Rule engine + `applicability_v1.yaml` | ✅ 26 rule (W-01…W-13, E-01…E-09, M-01…M-04), evidence resolve 100 % thành chunk; xác định, không LLM; thiếu thông tin → `missing_information`; cảnh báo dự thảo / unverified / pending — ADR-0010, `docs/applicability-rules-v1.md` |
| `POST /applicability-assessments`, `GET …/{id}` | ✅ lưu bảng `applicability_assessments`; thêm `GET /dictionaries/{name}`, `GET /graph/evidence-path/{chunk_id}`, `GET /graph/nodes/{id}/neighbors` |
| Form UI | ✅ `/applicability` (Next.js): form theo schema, kết quả gồm ứng viên, cột, chuyển tiếp, thông số, bảng ngưỡng có trích dẫn + trạng thái duyệt, rule đã xét + căn cứ, còn thiếu, disclaimer |
| 20 kịch bản do chuyên gia soạn cho kết quả đúng (kể cả missing-info) | 🟡 17 kịch bản nháp (`data/rules/applicability_scenarios_v1.yaml`) chạy đúng kỳ vọng nội bộ (`test_applicability_scenarios.py`) và in ra sheet **12-Kịch bản áp dụng** của hồ sơ kiểm — **chờ chuyên gia xác nhận/bổ sung** |
| Sửa lỗi phát hiện khi làm Phase 3 | ✅ dòng COD/TOC Bảng 1 QCVN 40 từng bị gán chung `parameter_id=TOC` (logical row dùng chung dict) → tách đúng COD/TOC, đã ingest + nạp lại |

## Nghiệm thu Phase 4 (QA, citation, guardrail) — trạng thái 2026-09-24

| Tiêu chí (PLAN §13, §8) | Trạng thái |
|---|---|
| Query understanding (rule-based trước, LLM bổ sung) | ✅ rule-based Phase 2; `gemini-3.5-flash-lite` (schema §8) chỉ khi miền unknown/cross hoặc ý định other; câu làm rõ → abstain "cần làm rõ" |
| Evidence assembler | ✅ `Retriever` Phase 2, `k=10`, ≤ 18k ký tự; thêm định nghĩa cột/vùng (QCVN 40 Mục 1.3.3.x, QCVN 19 Mục 1.3.13.x) vào expansion |
| Prompt v1 + structured output | ✅ `app/qa/prompts.py` (`qa-v1.1` từ 2026-09-24 — quy tắc 5 khóa theo chunk Sổ đăng ký), `response_schema=Answer`, `gemini-3.8-flash` `low` (medium cho hiệu lực/áp dụng/cross) — ADR-0011 |
| Citation validator 7 quy tắc | ✅ `app/qa/validator.py`; test unit từng quy tắc; sinh lại 1 lần rồi abstain chuẩn |
| Abstention templates | ✅ ngoài phạm vi, M-01 tần suất (trích Điều 16.3/17.5 TT10 + chỉ dẫn NĐ 08/2022), thiếu evidence, cần làm rõ, validator từ chối |
| SSE `/questions/ask` + chat UI + evidence panel | ✅ SSE 7 loại sự kiện; UI `/` (Next.js) với chip trích dẫn `[n]`, panel căn cứ, toàn văn chunk + đường dẫn cấu trúc + link văn bản, 👍/👎 |
| Audit log + feedback | ✅ migration `a1b2c3d4e5f6`: `query_logs`, `answers`, `answer_citations`, `feedback`; `GET /admin/query-logs`, `GET /questions/{id}` |
| 100 % câu khẳng định trên gold có citation hợp lệ; 0 số liệu ngoài evidence | ✅ trên gold nháp 13 câu: 10/10 câu qua LLM đạt validator ngay lần 1, 11/11 trích đúng nhãn kỳ vọng (`docs/reports/qa-eval-2026-09-23-r2.md`) — **chờ gold set chuyên gia** để nghiệm thu chính thức |
| 15 câu abstain đúng | 🟡 gold nháp mới có 3 câu abstain (13/13 abstain đúng chính sách); 15 câu thiếu căn cứ/ngoài phạm vi thuộc gold set chuyên gia (cuối Phase 4 theo quyết định PO 2026-09-23) |
| Test | ✅ `tests/test_qa.py` 9 test offline (FakeLLM); toàn bộ backend 66 pass / 2 skip (Gemini live, hybrid live); frontend `tsc` + `eslint` + `next build` sạch |

## Nghiệm thu Phase 0 (tuần 1)

| Tiêu chí | Trạng thái 2026-09-23 |
|---|---|
| Repo + cấu trúc thư mục + `.gitignore` + CI | ✅ |
| Docker Compose (Postgres+pgvector, Neo4j) viết xong | ✅ file; ⚠️ **chưa kiểm được `compose up` — máy dev không có Docker** |
| `documents.yaml` với hash, `text_status`, registry fact + test | ✅ |
| Grammar YAML cho 5 văn bản + scope TT10 | ✅ |
| Dictionaries v1 load không lỗi (`pytest tests/test_dictionaries.py`) | ✅ 8/8 test pass; 110 thông số, 54 loại hình, 110 thiết bị, 20 đơn vị; sinh bằng `backend/scripts/bootstrap_dictionaries.py` |
| Ontology v1, Data dictionary v1, citation spec, table-row JSON spec | ✅ |
| ADR-0001..0009 | ✅ (0009 Accepted sau spike) |
| Spike docling: khôi phục Bảng 25/26 TT10 | ✅ lần 2 (OCR tắt, tr. 105–133): 28 bảng, Bảng 25/26 đúng cột — `docs/spikes/docling-spike.md` |
| Gold set: template + câu mẫu; chuyên gia bắt đầu viết | ✅ template |
| Bản ký chính thức TT45/TT10 | ⏳ chờ PO |
