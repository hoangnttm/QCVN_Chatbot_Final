# Chạy môi trường dev (backend + frontend)

Hướng dẫn cho máy dev Windows 11 (PowerShell) — lệnh tương đương trên Linux/macOS chỉ khác cách đặt biến môi trường. Trạng thái hạ tầng máy dev hiện tại xem `docs/env-check-2026-09-24.md`.

## 0. Yêu cầu

| Thành phần | Máy dev hiện tại | Tối thiểu |
|---|---|---|
| Python | Miniconda 3.13 (`C:\Miniconda3\python.exe`), gói đã cài sẵn bằng pip | ≥ 3.12; hoặc `uv` ≥ 0.4 để tạo `.venv` |
| PostgreSQL + pgvector | PostgreSQL 18.1 native, service `postgresql-x64-18`, port 5432, pgvector 0.8.6 tự biên dịch | PostgreSQL ≥ 16 có extension `vector` (hoặc Docker `pgvector/pgvector:pg16`) |
| Node.js | Node 22.16 + npm 11 | Node ≥ 20 |
| Neo4j | Neo4j Desktop 2, instance `qcvn_env_chatbot` | Tùy chọn; bắt buộc cho graph-first / PPR / validator luật 8 (README bước 6) |
| Docker Desktop | có, engine tắt | tùy chọn, thay cho PostgreSQL/Neo4j native |
| Khóa API | `GEMINI_API_KEY` (embedding), `ANTHROPIC_API_KEY` (QA, Phase 4) | Gemini bắt buộc cho bước `embed` |

## 1. Lấy mã nguồn và cấu hình

```powershell
git clone git@github.com:hoangnttm/QCVN_Chatbot.git
cd QCVN_Chatbot
Copy-Item infra\.env.example backend\.env      # file này gitignored — không commit
```

Sửa `backend/.env` cho máy dev native (PostgreSQL loopback dùng trust, không mật khẩu):

```dotenv
DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:5432/qcvn
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<mật khẩu đặt trong Neo4j Desktop>
EMBEDDING_PROVIDER=gemini
GEMINI_API_KEY=<khóa Gemini>                 # dùng chung cho embedding và hỏi đáp (ADR-0005, ADR-0011)
ADMIN_TOKEN=<chuỗi ngẫu nhiên>               # bắt buộc để mở /admin và /api/v1/admin/* (Phase 5)
ALERT_WEBHOOK_URL=                           # tùy chọn: Slack/Teams/Google Chat incoming webhook
LLM_PROVIDER=gemini
GEMINI_ANSWER_MODEL=gemini-3.8-flash
GEMINI_ANSWER_THINKING=low
GEMINI_UNDERSTANDING_MODEL=gemini-3.5-flash-lite
ANTHROPIC_API_KEY=                           # chỉ khi LLM_PROVIDER=anthropic
HF_HUB_DISABLE_SYMLINKS=1
HF_HUB_DISABLE_SYMLINKS_WARNING=1
```

Nếu dùng Docker thay cho PostgreSQL native: `Copy-Item infra\.env.example infra\.env`, đặt `POSTGRES_PORT=5433` (5432 đang bị PG18 chiếm), chạy `docker compose -f infra/docker-compose.yml up -d postgres`, và `DATABASE_URL=postgresql+psycopg://qcvn:qcvn_dev_password@localhost:5433/qcvn`.

## 2. Môi trường Python

**Cách A — dùng Miniconda sẵn có (máy dev hiện tại):** mọi gói đã được cài (`fastapi uvicorn sqlalchemy alembic psycopg[binary] pgvector pydantic-settings pyyaml neo4j rank-bm25 pyvi anthropic google-genai openpyxl docling sentence-transformers pytest ruff`). Chỉ cần `cd backend` và gọi `python …` trực tiếp như các lệnh dưới đây.

**Cách B — tái lập bằng uv (máy khác / CI):**

```powershell
cd backend
uv sync --group dev --extra pdf          # core + pytest/ruff + docling (tải torch ≈ 2,5 GB)
# thêm --extra ml nếu muốn embedding local bge-m3 thay cho Gemini
```

Sau đó thay `python` bằng `uv run python` (hoặc kích hoạt `.venv\Scripts\Activate.ps1`).

## 3. Database và schema

```powershell
# PostgreSQL native: kiểm tra service đang chạy
Get-Service postgresql-x64-18
# tạo database (một lần); extension vector/pg_trgm/unaccent do migration tự tạo
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h 127.0.0.1 -c "CREATE DATABASE qcvn ENCODING 'UTF8';"

cd backend
python -m alembic upgrade head           # 13 bảng + monitoring_requirements + review_decisions
python -m alembic current                # phải in: f5a6b7c8d9e0 (head)
```

Lần sau chỉ cần `alembic upgrade head` mỗi khi kéo code có migration mới (`backend/alembic/versions/`).

## 4. Nạp dữ liệu (chạy theo thứ tự, từ `backend/`)

`data/derived/` gitignored, nên máy mới phải chạy lại toàn bộ:

| Bước | Lệnh | Ghi chú |
|---|---|---|
| 4.1 Trích PDF | `python scripts/ingest.py pdf` | Chỉ cho TT 45/2024 (≈ 2 phút) và TT 10/2021 (192 trang, **≈ 35 phút CPU**, cache theo lô 30 trang — chạy dở có thể chạy tiếp). Lần đầu docling tải model từ Hugging Face; khi đã có model có thể đặt `$env:HF_HUB_OFFLINE="1"` để khỏi gọi mạng |
| 4.2 Parse | `python scripts/ingest.py all` | ≈ 1 phút; 5 dòng phải là `[OK ]` (coverage 100 %, 0 unresolved) → `data/derived/<doc>/*.json` |
| 4.3 Nạp DB | `python scripts/ingest.py load` | idempotent theo văn bản; in số dòng từng bảng |
| 4.4 Embedding | `python scripts/ingest.py embed --dry-run` rồi `python scripts/ingest.py embed` | cần `GEMINI_API_KEY`; 1.259 chunk ≈ 40 request ≈ $0,04, 1–2 phút; chạy dở tiếp tục được; kết quả `index_versions` = `inactive` |
| 4.5 Kiểm tra | `python scripts/ingest.py db` | `evidence_chunks_embedded` phải bằng `evidence_chunks` |
| 4.6 Kích hoạt index | `python scripts/ingest.py activate --label gemini-embedding-001@1536` | một index `active` (UC-08); lệnh cũng dựng cache BM25 `data/derived/index/` |
| 4.7 Đo retrieval | `python scripts/eval.py` (thêm `--no-vector` để không gọi Gemini) | Recall@5/@10, định tuyến, độ trễ → `docs/reports/retrieval-eval-<ngày>.md` |
| 4.6 (tùy chọn) | `python scripts/review_pack.py` | hồ sơ kiểm chuyên gia → `data/review/` |

## 5. Backend API

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

- Kiểm tra: `http://127.0.0.1:8000/api/v1/health` → `{"status":"ok","database":true}`.
- Swagger: `http://127.0.0.1:8000/docs`.
- Endpoint đọc: `/api/v1/documents`, `/api/v1/documents/{id}`, `/api/v1/documents/{id}/tree`, `/api/v1/documents/{id}/monitoring`, `/api/v1/tables/{table_code}`, `/api/v1/evidence/{chunk_id}`.
- CORS chỉ mở cho `http://localhost:3000` và `http://127.0.0.1:3000` (`app/main.py`).
- Hỏi đáp: `POST /api/v1/questions/ask` (SSE: `stage` → `understanding` → `evidence` → `delta`* → `answer` → `done`), `POST /api/v1/questions/answer` (JSON một lần), `POST /api/v1/feedback`, `GET /api/v1/questions/{query_id}`, `GET /api/v1/history`, `GET /api/v1/me/quota` (ba endpoint cuối gắn theo header `X-Client-Id`). Cần `GEMINI_API_KEY` trong `backend/.env`; câu hỏi tần suất/ngoài phạm vi trả lời theo chính sách, không gọi Gemini.
- Quản trị (`/api/v1/admin/*`, **cần header `X-Admin-Token` = `ADMIN_TOKEN`** trong `.env`; chưa đặt → 503, sai → 401): `ping`, `health?probe`, `metrics?window`, `usage`, `alerts` (+ `/{id}/resolve`, `/evaluate`, `/test`), `stats`, `audit-events`, `query-logs` (+ `/export.csv`, `/{id}`), `review/summary|tables/{code}|rows/{row_id}|decisions|tables/{code}/bulk-approve|items?type=`, `documents` (+ `/{id}/ingestion-runs`), `index-versions` (+ `/{label}/activate`).
- Giám sát: log stdout dạng JSON (`LOG_JSON=true`) có `request_id`/`query_id`; cảnh báo chạy nền mỗi `ALERT_INTERVAL_S` và gửi `ALERT_WEBHOOK_URL` (Slack/Teams/Google Chat, body `{"text": ...}`); tắt bằng `ALERTS_ENABLED=false`. Hạn mức `QUOTA_*` chỉ cảnh báo, không chặn. Ý nghĩa từng cảnh báo: `docs/runbook-incidents.md`.

## 6. Frontend (Next.js 16)

```powershell
cd frontend
npm install                              # một lần
npm run dev                              # http://localhost:3000 = chat hỏi đáp; /documents, /applicability, /admin (nhập ADMIN_TOKEN)
```

- API mặc định `http://localhost:8000`; đổi bằng file `frontend/.env.local` (`NEXT_PUBLIC_API_URL=...`, mẫu ở `frontend/.env.local.example`).
- Muốn cổng khác: `npx next dev -p 3001` (không dùng `npm run dev -- --port 3001`: npm 11 nuốt tham số) và thêm origin đó vào CORS backend.
- Kiểm tra build production: `npm run build` rồi `npm start`.
- Khu vực quản trị `/admin` (dashboard health/KPI/ngân sách/cảnh báo), `/admin/logs`, `/admin/review`, `/admin/documents`: đăng nhập bằng `ADMIN_TOKEN` (lưu trong localStorage tới khi đăng xuất; M9 sẽ thay bằng tài khoản). Lịch sử chat gắn với `qcvn.client_id` trong localStorage — xóa dữ liệu site là mất lịch sử.
- Kiểm thử di động: DevTools → 400 px; panel căn cứ/lịch sử thành drawer, menu ☰.
- `next dev` tự sinh `frontend/AGENTS.md`/`CLAUDE.md` — giữ trong repo.

## 7. Chạy hằng ngày

Mở 2 terminal (PostgreSQL service đã chạy nền):

```powershell
# terminal 1
cd backend; python -m uvicorn app.main:app --reload --port 8000
# terminal 2
cd frontend; npm run dev
```

Mở `http://localhost:3000` (chat) hoặc `http://localhost:3000/documents`.

## 8. Test và lint

```powershell
cd backend
python -m ruff check .; python -m ruff format --check .
python -m pytest -q                      # tự tạo DB qcvn_test trên cùng server; bỏ qua test DB nếu không có PostgreSQL
$env:QCVN_LIVE_TESTS="1"; python -m pytest tests/test_embed.py -q   # thêm test gọi Gemini thật (tốn vài token)
python scripts/qa_eval.py                # hỏi đáp thật trên gold set (≈ 13 lần gọi Gemini, vài cent) → docs/reports/qa-eval-<ngày>.md
$env:HF_HUB_DISABLE_SYMLINKS="1"; $env:HF_HUB_OFFLINE="1"; python -m pytest tests/test_tt10.py -q   # cần cache docling TT10

cd ../frontend
npm run lint; npm run build
```

Đánh giá nghiên cứu (gold set v2, `data/gold/README.md`, `docs/reports/eval/README.md`):

```powershell
cd backend
python scripts/gold.py validate --tier all            # bộ gold: Tầng A sinh từ nguồn (393), Tầng B soạn tay, Tầng C từ nhật ký
python scripts/evaluate.py ablation --configs C0,C2,C3,C4 --split dev   # retrieval, không ghi query_logs
python scripts/evaluate.py qa --config C4 --split dev --tier B --repeats 1   # Gemini thật ≈ 0,005 USD/câu
python scripts/evaluate.py judge --run <run_id>       # LLM-judge, cache; rồi human-sheet / agreement
```

## 9. Sự cố thường gặp

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| `database not reachable: …` | service PostgreSQL chưa chạy, sai port hoặc `DATABASE_URL`; thử `psql -U postgres -h 127.0.0.1 -d qcvn -c "select 1"` |
| Alembic báo `Target database is not up to date` / bảng thiếu | `python -m alembic upgrade head` |
| `pdf_extract cache missing — run ingest.py pdf` | chưa chạy bước 4.1 cho văn bản PDF |
| docling `WinError 1314` (symlink) | thiếu `HF_HUB_DISABLE_SYMLINKS=1` (đặt trong `.env` hoặc `$env:`) |
| docling lỗi SSL/kết nối tới huggingface.co | model đã có trong cache → `$env:HF_HUB_OFFLINE="1"` |
| docling `std::bad_alloc` | OCR đã tắt mặc định (ADR-0009); không bật `do_ocr` |
| Gemini `429 RESOURCE_EXHAUSTED` | embedder tự retry/backoff; free tier giới hạn token/phút — chạy `embed --limit 200` nhiều lần nếu cần |
| Frontend `fetch failed` / `ECONNREFUSED 8000` | backend chưa chạy hoặc `NEXT_PUBLIC_API_URL` sai |
| Trình duyệt báo CORS | frontend chạy ở origin khác `localhost:3000` → thêm vào `allow_origins` trong `backend/app/main.py` |
| `npm run dev -- --port 3001` chạy `next dev 3001` rồi lỗi | dùng `npx next dev -p 3001` |
| pytest lỗi `permission denied to create database` | tài khoản trong `DATABASE_URL` cần quyền `CREATEDB` (máy dev dùng superuser `postgres`) |
| Cổng 5432 bận khi dùng Docker | đặt `POSTGRES_PORT=5433` trong `infra/.env` và `DATABASE_URL` |
| Tên file PDF TT10 có dấu (NFD) không mở được bằng công cụ khác | registry loader tự xử lý NFC/NFD; không đổi tên file trong `sources/` (hash sẽ lệch) |
