# Architecture Decision Records

Mỗi ADR ghi một quyết định đã chốt trong PLAN.md §15, theo mẫu Context → Decision → Consequences.
Trạng thái: `Accepted` (PO đã chốt), `Proposed` (chờ bằng chứng/spike), `Superseded`.

| ADR | Quyết định | Trạng thái |
|---|---|---|
| [0001](ADR-0001-backend-python-fastapi.md) | Backend Python 3.12 + FastAPI, quản lý bằng uv | Accepted |
| [0002](ADR-0002-datastores-postgres-neo4j.md) | PostgreSQL + pgvector là system of record; Neo4j là projection; không Qdrant/OpenSearch trong MVP | Accepted |
| [0003](ADR-0003-provenance-sourcespan-citation-label.md) | Provenance bằng SourceSpan + citation label cấu trúc; số trang chỉ với PDF | Accepted |
| [0004](ADR-0004-draft-documents-registry-facts.md) | Văn bản dự thảo và registry fact do quản trị viên khai báo | Accepted |
| [0005](ADR-0005-llm-embedding-models.md) | Claude Opus 5 cho QA; bge-m3 + bge-reranker-v2-m3 local | Accepted (đo lại sau eval) |
| [0006](ADR-0006-frontend-auth-mvp.md) | Next.js 15 + shadcn/ui; auth MVP tối giản | Accepted |
| [0007](ADR-0007-frequency-out-of-corpus.md) | Không nạp NĐ 08/2022 — câu hỏi tần suất luôn abstain có chỉ dẫn | Accepted |
| [0008](ADR-0008-tt10-ingest-partition.md) | TT 10/2021 chỉ ingest phần nước thải/khí thải | Accepted |
| [0009](ADR-0009-pdf-extraction-docling.md) | Trích xuất PDF bằng docling (OCR tắt, theo lô trang) | Proposed → cập nhật sau spike |
