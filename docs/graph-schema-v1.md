# Neo4j projection v1

Dựng lại toàn bộ từ PostgreSQL (system of record) bằng `python backend/scripts/build_graph.py`; xem số liệu bằng `--counts`. Idempotent: mọi node/edge `MERGE` theo `id` duy nhất, gắn `projection_version`; sau khi ghi, node/edge mang version cũ bị xóa. Mã: `backend/app/graph/build_graph.py`; truy vấn dùng chung qua `app/graph/store.py` (`GraphStore`: `PostgresGraphStore` luôn có, `Neo4jGraphStore` khi graph đã dựng).

## Node

| Label | id | Thuộc tính chính | Nguồn |
|---|---|---|---|
| `LegalDocument` + `QCVN` / `Circular` / `ExternalStub` | `doc:<document_id>` | document_code, title, domain, text_status | `documents` |
| `DocumentVersion` | `ver:<id>` | file_name, sha256, page_count, is_current | `document_versions` |
| `RegistryFact` | `fact:<id>` | fact_type, value, source, declared_by, declared_at | `registry_facts` |
| `Structure` + kind (`Chapter`, `Section`, `Article`, `Clause`, `Point`, `Appendix`, `Table`, `Footnote`, `Definition`, …) | `node_id` | number, title, citation_label, scope, domain, order, trang, text (≤ 2000 ký tự) | `structure_nodes` (bỏ Boilerplate) |
| `TableRow` | `row_id` | table_code, row_no, row_path, parameter, cells (JSON), citation_label | `table_rows` |
| `LimitRule` | `rule_code` | zone, op, value/min/max, unit_id, ref_o2, review_status | `limit_rules` |
| `Condition` | `cond:<hash>` (dedupe theo type/op/value/unit/text) | type ∈ {flow_regime, receiving_body, fuel_type, capacity, co_processing, sector_scope}, op, value, unit, text | conditions của LimitRule (kiểu lạ → build dừng) |
| `MonitoringCondition` | `mcond:<hash>` | type (tự do), op, value, unit, text | conditions của MonitoringRequirement |
| `Exception` | `exc:<hash>` | text | exceptions |
| `TransitionRule` | `rule_id` | kind, text, dates, relative_dates | `transition_rules` |
| `MonitoringRequirement` | `requirement_id` | requirement_type, domain, summary, text, review_status | `monitoring_requirements` |
| `EvidenceChunk` | `chunk_id` | chunk_type, citation_label, text và context_text (toàn văn), text_status, `embedding` (LIST<FLOAT> 1536 chiều), embedding_model | `evidence_chunks` |
| `Parameter`, `Unit`, `Zone`, `IndustrySector`, `EquipmentType`, `FuelType`, `ReceivingBodyType`, `EnvironmentalDomain`, `Method` | `param:`, `unit:`, `zone:<domain>:<code>`, `sector:`, `equip:`, `fuel:`, `rbt:`, `domain:`, `method:<code>` | theo từ điển v1 | `data/dictionaries/*.yaml`, phương pháp từ bảng PL1/PL4 |
| `ApplicabilityRule` | `arule:<W-xx>` | rule_code, domain, title, action, version | `data/rules/applicability_v1.yaml` |

## Edge

`CONTAINS` (Document → node gốc, node cha → con, Table → TableRow), `HAS_VERSION`, `HAS_REGISTRY_FACT`, `HAS_DEFINITION`, `GOVERNS_DOMAIN`, `PROMULGATES` / `PROMULGATED_BY` / `REPLACES` / `REPEALS` / `REFERENCES` (thuộc tính scope, target_part, condition, origin, evidence), `HAS_LIMIT_RULE`, `DEFINED_BY_TABLE_ROW`, `HAS_EVIDENCE` (LimitRule / TransitionRule / MonitoringRequirement / RegistryFact / Document → EvidenceChunk), `EXTRACTED_FROM` (EvidenceChunk → Structure | TableRow), `FOR_PARAMETER`, `IN_UNIT`, `IN_ZONE`, `APPLIES_TO` (LimitRule → EquipmentType), `APPLIES_WHEN` (→ Condition, thuộc tính inherited_from), `EXCEPT_WHEN`, `QUALIFIED_BY` (TableRow → Footnote), `HAS_METHOD` (Parameter → Method: order, stage, document_id, row_id), `HAS_CHARACTERISTIC_PARAMETER` (IndustrySector → Parameter: condition_text), `PARENT_OF`, `USES_FUEL`, `HAS_UNIT`, `HAS_TRANSITION_RULE`, `HAS_MONITORING_REQUIREMENT`, `SUPPORTED_BY` (ApplicabilityRule → EvidenceChunk).

Ràng buộc: `id` UNIQUE trên mọi label; index `EvidenceChunk.citation_label`, `Parameter.parameter_id`, `LimitRule.parameter_id`.

## Schema v1.2 (2026-10-05) — dọn trùng lặp, provenance trên cạnh

Sáu thay đổi so với bảng Edge ở trên (mã: `backend/app/graph/build_graph.py`):

1. **Một loại cạnh bằng chứng.** `SOURCED_FROM` và `SUPPORTED_BY` gộp vào `HAS_EVIDENCE`, phân biệt bằng thuộc tính `role`: `limit` (LimitRule), `transition`, `monitoring`, `registry_fact`, `definition` (bản ghi từ điển → điều khoản nguồn), `support` (ApplicabilityRule), `document` (LegalDocument → mọi chunk, bị loại khỏi PPR và đường đi). `EXTRACTED_FROM` (chunk → vị trí nguồn) giữ nguyên.
2. **Thiết bị chỉ ở `EquipmentType`.** Điều kiện `equipment_path` không còn là node `Condition`; dùng `(LimitRule)-[:APPLIES_TO]->(EquipmentType)` (đường dẫn số nằm ở `EquipmentType.path`). Cột cũng vậy: `IN_ZONE`, không có Condition `zone`.
3. **`FOR_PARAMETER` chỉ từ LimitRule, TableRow, MonitoringRequirement.** Chunk không nối thẳng tới Parameter nữa (đi qua `EXTRACTED_FROM` → TableRow).
4. **Phương pháp truy được về dòng phụ lục.** `(TableRow)-[:SPECIFIES_METHOD {order, stage}]->(Method)`; không còn node Method cô lập. Đường trích dẫn: `(Parameter)-[:HAS_METHOD]->(Method)<-[:SPECIFIES_METHOD]-(TableRow)<-[:EXTRACTED_FROM]-(EvidenceChunk)`.
5. **Kiểu điều kiện đóng.** `Condition.type` thuộc `LIMIT_CONDITION_TYPES`; kiểu ngoài tập làm build dừng. Điều kiện của quy định quan trắc tách sang label `MonitoringCondition`.
6. **Mọi cạnh có `method` và `source`.** `method` ∈ {dictionary, registry, structure_parser, table_parser, entity_resolution, rule_extraction, relation_extraction, chunker, citation_label, curated}; `source` là id của dòng / node / tệp mà cạnh được đọc ra. `Projection.edge()` từ chối cạnh thiếu một trong hai.

Sau thay đổi: 5.744 node, 16.905 cạnh. Kiểm hồi quy trên dev: luật 8 (bộ nhiễu P0–P5) và PPR tầng M cho số giống hệt trước thay đổi.

## Schema v1.3 (2026-10-05) — nối văn xuôi với tầng ngữ nghĩa

Lượt thứ hai của `build_graph.py` (`backend/app/graph/enrich.py`), sinh tất định, không LLM. Mọi cạnh mang `method`, `source`; cạnh suy ra có thêm `derived: true`.

| Cạnh | Từ → đến | Cách sinh (`method`) | Số cạnh |
|---|---|---|---|
| `MENTIONS {surface}` | EvidenceChunk (không phải dòng bảng) → Parameter / Zone / FuelType / ReceivingBodyType / IndustrySector / LegalDocument khác | `alias_match`: AliasIndex của phía câu hỏi; bỏ alias tiếng Việt một từ ("hồ", "đồng", "màu") vì trùng "hồ sơ", "đồng xử lý", "hoa màu" | 231 |
| `REFERS_TO {surface}` | EvidenceChunk → Structure cùng văn bản | `reference_pattern`: "Bảng 1", "Mục 2.1", "khoản 2 Điều 3", "Phụ lục 4.1"; bỏ dẫn chiếu sang văn bản khác ("Điều 111 Luật …") | 127 |
| `USES_TERM {surface}` | EvidenceChunk → Definition cùng văn bản | `term_match`, thuật ngữ ≥ 8 ký tự | 60 |
| `STATED_IN {relation, side}` | LegalDocument (cả hai đầu quan hệ) → chunk nêu quan hệ thay thế / bãi bỏ | `relation_extraction` | 96 |
| `INHERITS_FROM` | TableRow → TableRow mang giá trị | `table_parser` | 305 |
| `HAS_PROVISION {role, via}` | LegalDocument → chunk; `role` ∈ scope, subjects, definitions, effectivity, transition, roadmap, implementation, responsibility, method, management, preamble, registry | `heading_role` (tiêu đề mục gần nhất); QCVN nhận thêm điều khoản hiệu lực / chuyển tiếp / lộ trình của thông tư ban hành (`derived`, `via`) | 238 |
| `ABOUT_TOPIC` | MonitoringRequirement → `Topic` (`topic:<miền>:<loại yêu cầu>`) | `curated` | 35 |
| `TABLE_CONTEXT {kind}` | Table → chunk: chú thích của bảng (`footnote`), điều khoản dẫn tới bảng hoặc phụ lục chứa bảng (`reference`) | `derived` | 107 |
| `EFFECTIVE_UNDER {kind}` | Table có LimitRule → chunk điều khoản chuyển tiếp / lộ trình / cột mặc định và registry fact ngày hiệu lực của văn bản và thông tư ban hành | `derived` | 26 |

Sau thay đổi: 5.753 node, 18.541 cạnh. `gds.RELS` (PPR) không đổi; luật 8 dùng thêm các cạnh này cho đường căn cứ (`graph_verify.PATH_RELS`).

### Tập ứng viên theo quan hệ (`backend/app/retrieval/graph_candidates.py`, chỉ dùng cho đánh giá)

1. `core`: chunk trong 2 bước quanh thực thể của câu hỏi (cạnh PPR + `MENTIONS` + `STATED_IN`; thêm hạt giống Condition dải lưu lượng / nhiên liệu).
2. `context`: với mỗi bảng có dòng trong `core` → `TABLE_CONTEXT` và `EFFECTIVE_UNDER`.
3. `provisions`: `HAS_PROVISION` của văn bản trong phạm vi (văn bản được nêu, văn bản thay thế nó, QCVN của miền) với `role` theo ý định (`ROLES_BY_INTENT`).
4. `topics`: yêu cầu quan trắc theo miền cho ý định lấy mẫu / phương pháp / tần suất (`TOPICS_BY_INTENT`).

Đo bằng `python scripts/evaluate.py coverage` (recall_must@10; baseline = BM25 + vector không graph; `--exclude` để bỏ một họ cạnh):

| Tập | Split | n | Cỡ trung vị | Độ phủ | Lọc | Ưu tiên | Baseline |
|---|---|---|---|---|---|---|---|
| 2 bước, schema v1.2 | dev | 114 | 81 | 0,893 | 0,854 | 0,889 | 0,915 |
| 2 bước, schema v1.3 | dev | 114 | 96 | 0,944 | 0,885 | 0,911 | 0,915 |
| `typed` | dev | 114 | 138 | 1,000 | 0,928 | 0,928 | 0,915 |
| `typed` | **test** (A 312 + M 92, chạy một lần `20261005-1758-coverage-test-e6`) | 404 | 135 | 0,999 | 0,900 | 0,901 | 0,895 |

Trên test, lọc theo `typed` hơn baseline ở 4 câu, kém ở 1 câu (dấu p = 0,38): không kém hơn, nhưng cũng không tăng recall có ý nghĩa; lợi ích là thu không gian tìm kiếm còn khoảng 11 % corpus (trung vị; p90 = 425 chunk). Tập test chưa có tầng B (văn xuôi viết tay), nên kết luận chỉ áp dụng cho câu tra bảng và câu nhiều bước. Bỏ `MENTIONS` hoặc `STATED_IN` không đổi tập `typed` trên dev (chúng chỉ nâng tập 2 bước); bỏ `TABLE_CONTEXT` + `EFFECTIVE_UNDER` hoặc `HAS_PROVISION` + `ABOUT_TOPIC` làm độ phủ dev xuống 0,974 / 0,975.

**Chạy thật trong Neo4j (2026-10-05).** `graph_candidates.neo4j_search` gói bốn bước trên vào một truy vấn Cypher (`CYPHER_SEARCH`) và xếp hạng ứng viên bằng `vector.similarity.cosine` trên embedding lưu ở node. `python scripts/evaluate.py graph-first` trên dev (114 câu, run `20261005-2102-graph-first-dev`): tập Cypher trùng tập tính trong bộ nhớ ở 114/114 câu; recall_must@10: hybrid toàn corpus 0,915 · vector toàn corpus (Neo4j) 0,885 · ứng viên + cosine 0,927 · ứng viên + RRF(cosine, BM25 trong tập) 0,942 (hơn hybrid ở 4 câu, không kém câu nào); một round trip p50 38 ms, p95 330 ms. Chưa chạy trên test; các cạnh được thiết kế trên chính tập dev này.

Kèm theo: nhận diện cột xử lý phủ định (`query._resolve_zone`: "không thuộc Mức A, Mức B" → C). Trên dev, luật 8 chặn nhầm 4 % → 0 %, bắt sai cột 86 % → 90 %; retrieval C3 không đổi.

## Schema v1.4 (2026-10-06) — lớp fact trong graph, mã node theo khóa tự nhiên

Dựng từ `qcvn_v2` vào database Neo4j `qcvnv2`; graph v1.3 (database `neo4j`, dựng từ `qcvn`) được giữ nguyên và khóa ghi. Cách làm và số liệu: `docs/reports/nhat-ky-dung-lai-graph.md`.

| Thay đổi | Chi tiết |
|---|---|
| Label phụ `Fact` | Gắn lên node **đã có** của khẳng định: `LimitRule` (1.125), `MonitoringRequirement` (29), `Structure` loại định nghĩa (17), `TransitionRule` (14), `RegistryFact` ngày hiệu lực (4). Không tạo node mới: `LimitRule` chính là fact giới hạn. |
| Thuộc tính fact | `fact_id`, `fact_kind` (= `facts.fact_type`; đổi tên vì `RegistryFact` đã có `fact_type` riêng), `trust`, `valid_from`, `valid_to`, `address` (JSON địa chỉ pháp lý), `address_label`, `fact_chunk_id`, `fact_method` |
| `FACT_STATED_IN` | Fact → `EvidenceChunk` mà nó được trích dẫn; mỗi node `Fact` có đúng một cạnh (1.189). `method = fact_layer`, `source` = `fact_id`. Tên khác `STATED_IN` của v1.3 (văn bản → điều khoản nêu quan hệ) và **không** nằm trong danh sách cạnh của PPR hay tập ứng viên, nên các phép đo hiện có không đổi. |
| Fact trên cạnh | Phương pháp thử (`SPECIFIES_METHOD`, 940 fact trên 761 cạnh) và quan hệ văn bản (48 fact trên 48 cạnh) vốn là cạnh: cạnh mang `fact_ids` (danh sách), `fact_kind`, `trust` (mức yếu nhất), `valid_from`, `valid_to`, `address`, `address_label`, `fact_chunk_id`. |
| `LimitRule` | thêm `column_key`, `cell_raw` (nguyên văn ô), `zone_scheme` |
| `Zone` | mã `zone:{zone_scheme}:{code}` (ví dụ `zone:QCVN_40_2025:B`), thuộc tính `scheme`; `zones.yaml` có thêm trường `scheme`. Trước đây `zone:{domain}:{code}`. |
| `Condition` | dựng từ bảng `rule_conditions`; mã `cond:{dimension}:{hash}`, thêm `dimension`, `scope`. Danh sách chiều lấy từ `condition_dimensions.yaml` (chiều có `linked_entity` thành cạnh `IN_ZONE` / `APPLIES_TO`), hằng `LIMIT_CONDITION_TYPES` trong mã đã bỏ. |
| Mã theo khóa tự nhiên | `RegistryFact`: `regfact:{document_id}:{fact_type}`; `DocumentVersion`: `ver:{document_id}:{sha256[:12]}`. Trước đây dùng số tự tăng của PostgreSQL nên đổi theo mỗi lần nạp. |
| Cạnh do nhiều bản ghi nêu | Gộp theo thứ tự cố định: giữ thuộc tính của bản ghi đầu, thêm `sources` (mọi bản ghi) và `n_records`. Trước đây bản ghi ghi sau cùng thắng, phụ thuộc thứ tự dòng PostgreSQL trả về. 439 bản ghi được gộp. |
| Cạnh thiếu đầu mút | Không ghi được thì liệt kê ở `Meta.dangling_edges`, không còn bị bỏ qua im lặng. Hiện có 2 (xem nhật ký). |
| Ghi đè thuộc tính | `SET n = props` thay cho `+=`: thuộc tính nguồn đã bỏ không còn sót lại sau khi dựng lại. |
| `Meta` | thêm `schema_version`, `source_database`, `source_revision` (Alembic), `fact_nodes`, `fact_edges`, `edge_facts`, `dangling_edges` |

Kiểm tra graph còn là bản chiếu của database hay không: `python backend/scripts/build_graph.py --check` (18 bất biến, `backend/app/graph/check.py`). So hai lần dựng: `python backend/scripts/graph_fingerprint.py`.

Số liệu (5 văn bản): 5.753 node, 19.289 cạnh (18.100 cạnh của v1.3 + 1.189 `FACT_STATED_IN`), 1.259 vector.

Truy vấn mẫu:

- Ngưỡng kèm nguyên văn ô, địa chỉ và đoạn trích dẫn: `MATCH (r:LimitRule:Fact {parameter_id: 'BOD5'})-[:IN_ZONE]->(z:Zone {scheme: 'QCVN_40_2025', code: 'B'}) MATCH (r)-[:FACT_STATED_IN]->(c:EvidenceChunk) RETURN r.cell_raw, r.address_label, r.trust, r.valid_from, c.id`
- Mọi khẳng định được trích từ một đoạn: `MATCH (f:Fact)-[:FACT_STATED_IN]->(c:EvidenceChunk {id: $chunk}) RETURN f.fact_kind, f.fact_id, f.address_label`
- Fact còn hiệu lực tại một ngày: `MATCH (f:Fact) WHERE f.valid_from <= $day AND (f.valid_to IS NULL OR f.valid_to >= $day) RETURN f.fact_kind, count(*)`

## Vector trong node (2026-10-05)

Vector index là tính năng lõi của Neo4j (từ 5.11), không cần plugin. `build_graph.py` chép `evidence_chunks.embedding` sang thuộc tính `EvidenceChunk.embedding` bằng `db.create.setNodeVectorProperty` và tạo index `evidence_embedding` (HNSW, cosine, tắt quantization, kèm thuộc tính lọc `domain`). Mã: `backend/app/graph/vectors.py`, `Neo4jVectorIndex` trong `backend/app/retrieval/vector.py`.

- Truy vấn: Neo4j ≥ 2026.01 dùng mệnh đề `SEARCH … (VECTOR INDEX evidence_embedding FOR $v WHERE n.domain = d LIMIT $n) SCORE AS score` (lọc trong index, mỗi miền một lần tìm); bản 5.x dùng `db.index.vector.queryNodes` rồi lọc sau (lấy 1.000 láng giềng, chỉ đủ khi corpus nhỏ).
- Điểm Neo4j trả là `(1 + cos) / 2`; mã đổi lại về cosine để cùng thang với pgvector.
- Bật cho retriever: `VECTOR_BACKEND=neo4j` (mặc định `pgvector`); đánh giá: config `C1-neo4j`, `C3-neo4j`. Projection lỗi thời hoặc không có vector → nhánh vector bị bỏ và có cảnh báo, như khi API embedding lỗi.
- PostgreSQL vẫn là nơi ghi embedding; sau mỗi lần `ingest.py embed` phải chạy lại `build_graph.py`.

## Câu hỏi graph trả lời được (Phase 3 → dùng ở Phase 2/4)

- Đường bằng chứng của một chunk: `(c:EvidenceChunk)-[:EXTRACTED_FROM]->(src)<-[:CONTAINS*]-(root)` → Document → Section → Table → TableRow (`Neo4jGraphStore.path_for_chunk`).
- Mọi ngưỡng của một thông số ở các cột: `(p:Parameter {parameter_id})<-[:FOR_PARAMETER]-(r:LimitRule)-[:IN_ZONE]->(z)`.
- Thiết bị → ngưỡng → điều kiện: `(e:EquipmentType)<-[:APPLIES_TO]-(r)-[:APPLIES_WHEN]->(c:Condition)`.
- Quy chuẩn bị thay thế và điều kiện chuyển tiếp: `(q:QCVN)-[rel:REPLACES]->(old)`, `(tt:Circular)-[:HAS_TRANSITION_RULE]->(t)`.
- Rule applicability và căn cứ: `(a:ApplicabilityRule)-[:SUPPORTED_BY]->(c:EvidenceChunk)`.

## Trạng thái

- 2026-09-23: mã và test (`tests/test_graph.py`, tự bỏ qua khi Neo4j chưa chạy) đã có; instance Neo4j Desktop `qcvn_env_chatbot` khi đó đang tắt.
- 2026-09-24: **đã dựng graph thật** (`python backend/scripts/build_graph.py`, ≈ 30 s): **5.913 node, 17.106 edge** — 1.197 Structure, 1.217 TableRow, 1.259 EvidenceChunk, 1.125 LimitRule, 231 Condition, 29 MonitoringRequirement, 14 TransitionRule, 26 ApplicabilityRule; cạnh: 2.414 CONTAINS, 2.434 HAS_EVIDENCE, 2.147 FOR_PARAMETER, 1.342 APPLIES_WHEN, 48 REPLACES + 48 REPEALS. Sửa 2 lỗi phát hiện khi chạy thật: `Projection.node()` va chạm tên tham số với thuộc tính `label` của Structure (điểm a/b/c) → đổi tên tham số vị trí; `Neo4jGraphStore.path_for_chunk` trả thêm node `LegalDocument`/Boilerplate so với Postgres → lọc trong Cypher. `test_graph.py` pass (rebuild idempotent, đường bằng chứng Neo4j = Postgres). Lưu ý: test dựng lại projection từ DB `qcvn_test` vào cùng database `neo4j` → sau khi chạy test cần `build_graph.py` lại để có graph đầy đủ.
- Graph expansion của retrieval vẫn dùng `PostgresGraphStore` (cùng câu trả lời; chuyển sang Neo4j khi cần truy vấn nhiều bước ở Phase 5).
- 2026-10-06: schema v1.4 trong database Neo4j `qcvnv2` (dựng từ `qcvn_v2`); database `neo4j` (v1.3) khóa ghi làm mốc. `NEO4J_DATABASE=qcvnv2` trong `backend/.env`.
