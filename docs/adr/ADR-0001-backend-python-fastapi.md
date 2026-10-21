# ADR-0001 — Backend Python 3.12 + FastAPI, quản lý phụ thuộc bằng uv

**Trạng thái:** Accepted (PLAN.md D1) · **Ngày:** 2026-09-23

## Context
Pipeline cần: tách từ tiếng Việt (`pyvi`), embedding/rerank local (`sentence-transformers`), trích xuất PDF (`docling`), driver Neo4j, SDK Anthropic. Đội nhỏ (2–3 kỹ sư), chạy dev trên Windows 11.

## Decision
- Python 3.12+, FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic.
- `uv` quản lý phụ thuộc; phụ thuộc nặng tách thành extras `ml` (torch, sentence-transformers) và `pdf` (docling) để CI và Phase 0–1 không phải kéo torch.
- Cấu trúc module theo PLAN.md §3 (`registry`, `ingestion`, `ontology`, `graph`, `retrieval`, `qa`, `applicability`, `review`, `audit`, `evaluation`, `api/v1`).

## Consequences
- Một ngôn ngữ cho toàn bộ pipeline dữ liệu và API; frontend tách riêng (ADR-0006).
- Máy dev hiện có Miniconda Python 3.13 với hầu hết thư viện; `uv sync` tạo venv riêng khi cần tái lập.
