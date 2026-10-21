# ADR-0002 — PostgreSQL + pgvector là system of record; Neo4j là projection; không Qdrant/OpenSearch trong MVP

**Trạng thái:** Accepted (PLAN.md D2, D6) · **Ngày:** 2026-09-23

## Context
Thiết kế gốc (`software-context.md` §2) đề xuất Postgres + Neo4j + Qdrant + OpenSearch. Corpus MVP ≈ 1.000–1.100 evidence chunk, ≈ 1.150 LimitRule — quá nhỏ để cần cluster tìm kiếm riêng, trong khi đội vận hành nhỏ và dev trên Windows.

## Decision
1. **PostgreSQL 16 + pgvector** giữ toàn bộ dữ liệu: registry, cấu trúc, dòng bảng (JSONB), rule, chunk + embedding (HNSW cosine), review, audit, eval.
2. **BM25 in-process** (`rank_bm25`) trên token đã tách từ + alias; rebuild khi kích hoạt `index_version`.
3. **Neo4j 5 Community** là **projection** được build lại idempotent từ Postgres (`build_graph.py`), dùng từ Phase 3 cho graph expansion và admin graph browser. Phase 2 dùng bảng Postgres qua cùng interface `GraphStore`.
4. Interface `LexicalIndex`, `VectorIndex`, `GraphStore` để thay bằng OpenSearch/Qdrant ở giai đoạn hardening (roadmap P1) mà không đổi tầng trên.

## Consequences
- Admin sửa dữ liệu ở một nơi; graph có thể xóa/rebuild bất kỳ lúc nào.
- Docker Compose chỉ 2 dịch vụ (dùng cho CI/máy khác/production).
- Khi corpus mở rộng (P5) cần đo lại: BM25 in-process > ~50k chunk sẽ chuyển OpenSearch.

## Bổ sung 2026-09-24 — biến thể "native" trên máy dev (PO chọn phương án A, `docs/env-check-2026-09-24.md`)
- **PostgreSQL 18.1 native** (service `postgresql-x64-18`, port 5432, trust trên loopback) thay cho container; **pgvector 0.8.6 được biên dịch từ nguồn** bằng MSVC 14.44 (`nmake /F Makefile.win`, `PGROOT=C:\Program Files\PostgreSQL\18`) và cài vào `lib\vector.dll`, `share\extension\vector*`, `include\server\extension\vector`. Database ứng dụng: `qcvn` (UTF8) với `vector`, `pg_trgm`, `unaccent`.
- **Neo4j Desktop 2** instance `qcvn_env_chatbot` (Neo4j Enterprise 2026.05.0, giấy phép developer) thay cho `neo4j:5-community`; Bolt `localhost:7687`, mật khẩu đặt trong Desktop, đưa vào `backend/.env` (không commit). Driver Python `neo4j 6.1.0`.
- Compose vẫn là đường chuẩn cho CI/production; mọi mã truy cập DB đi qua `DATABASE_URL`/`NEO4J_URI` nên hai biến thể tương đương. Lưu ý PG18 native dùng port 5432 → nếu chạy compose song song phải đặt `POSTGRES_PORT=5433`.
- pgvector build tay phải **biên dịch lại khi nâng cấp major PostgreSQL**; ghi phiên bản trong `docs/env-check-2026-09-24.md`.

## Bổ sung 2026-09-24 — schema v1 và cách nạp dữ liệu
- Schema quản lý bằng **Alembic** (`backend/alembic/`, model SQLAlchemy 2 tại `backend/app/db/models.py`; migration đầu `5e0d0b7a4334` tự tạo extension `vector`, `pg_trgm`, `unaccent`). Nhóm bảng theo PLAN.md §4.1: registry (`documents`, `document_versions`, `registry_facts`, `document_relations` với `origin=registry|text`), cấu trúc (`structure_nodes` + `attrs` JSONB, `tables`, `table_rows` = JSON spec 4.4 theo dòng), rule (`limit_rules`, `transition_rules`), bằng chứng (`evidence_chunks` với `embedding vector(1536)` + HNSW cosine, `index_versions`, `ingestion_runs`). Mọi bảng cấu trúc/rule/chunk mang span (`file_hash`, `line_start/end`, `pdf_page_*`, `printed_page`) theo ADR-0003.
- Database **phản chiếu 1-1 artefact `data/derived/<doc>/*.json`**: `ingest.py load` xóa-và-chèn theo `document_id` trong một transaction (idempotent), upsert toàn bộ registry (kể cả stub ngoài corpus để FK quan hệ resolve) và giữ lại vector của chunk có `context_text` không đổi để không tốn tiền embed lại. Graph Neo4j vẫn là projection dựng lại từ các bảng này.
- Test DB: pytest tự tạo `qcvn_test` trên cùng server và bỏ qua khi không có PostgreSQL (CI không DB vẫn xanh).
