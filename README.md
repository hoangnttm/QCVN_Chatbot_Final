# QCVN Chatbot — hỏi đáp có trích dẫn về quy chuẩn môi trường (GraphRAG)

> **Bản nộp mã nguồn cuối cùng.** Repo này chỉ gồm mã nguồn (`backend/`, `frontend/`, `infra/`), dữ liệu cấu hình và văn bản nguồn (`data/`, `sources/`), dữ liệu kiểm thử (`data/gold/`, `backend/tests/`) và tài liệu kỹ thuật tối thiểu (`docs/adr`, `docs/api`, các spec trong `docs/`). Kế hoạch, báo cáo, hồ sơ duyệt, UAT, sơ đồ và tài nguyên báo cáo không được đưa vào; các liên kết tới `PLAN.md`, `software-context.md`, `docs/plans/`, `docs/reports/`, `docs/diagrams/`, `report-resources/` trong tài liệu này trỏ về repo phát triển [hoangnttm/QCVN_Chatbot](https://github.com/hoangnttm/QCVN_Chatbot). Xuất từ commit `577d1ea` (nhánh `feature/generalization-v2`) ngày 2026-10-21.

Trợ lý hỏi đáp tiếng Việt, **mọi câu trả lời đều kèm căn cứ pháp lý** (Chương / Điều / Khoản / Điểm / Bảng, dòng, cột) và **từ chối trả lời khi không đủ căn cứ** (abstain). Phạm vi hiện tại: QCVN 40:2025/BTNMT (nước thải công nghiệp), QCVN 19:2024/BTNMT (khí thải công nghiệp), TT 06/2025, TT 45/2024, TT 10/2021.

- Thiết kế gốc: [software-context.md](software-context.md) · Kế hoạch triển khai: [PLAN.md](PLAN.md) · Kiến trúc tổng quát đang theo: [docs/plans/2026-10-06-kien-truc-tong-quat.md](docs/plans/2026-10-06-kien-truc-tong-quat.md)
- Quyết định kiến trúc: [docs/adr/](docs/adr/) · Báo cáo cuối môn: [docs/reports/bao-cao-cuoi-mon/](docs/reports/bao-cao-cuoi-mon/)
- Hướng dẫn chi tiết cho máy dev Windows (PostgreSQL / Neo4j cài native): [docs/dev-setup.md](docs/dev-setup.md)

## Mục lục

1. [Kiến trúc tóm tắt](#1-kiến-trúc-tóm-tắt)
2. [Yêu cầu hệ thống](#2-yêu-cầu-hệ-thống)
3. [Chạy nhanh (15 lệnh)](#3-chạy-nhanh-15-lệnh)
4. [Hướng dẫn từng bước](#4-hướng-dẫn-từng-bước)
5. [Chạy hằng ngày](#5-chạy-hằng-ngày)
6. [Kiểm thử và lint](#6-kiểm-thử-và-lint)
7. [Đánh giá (evaluation) và ablation](#7-đánh-giá-evaluation-và-ablation)
8. [Cấu trúc thư mục](#8-cấu-trúc-thư-mục)
9. [Sự cố thường gặp](#9-sự-cố-thường-gặp)
10. [Nguyên tắc không thương lượng](#10-nguyên-tắc-không-thương-lượng)
11. [Bảng thuật ngữ](#11-bảng-thuật-ngữ)

## 1. Kiến trúc tóm tắt

```text
sources/*.md, *.pdf ──► ingest.py (docling → structure → tables → facts → chunks)
                                │
                                ▼
                 PostgreSQL 16+ (+ pgvector)  ◄── hệ thống gốc: văn bản, bảng, limit_rules,
                                │                 fact layer, evidence_chunks (+ embedding Gemini)
                                ├──► build_graph.py ──► Neo4j (projection: cấu trúc văn bản,
                                │                        thực thể, fact, vector index trên chunk)
                                ▼
                 FastAPI  /api/v1  ── hiểu câu hỏi (rule + LLM) → structured lookup
                                      → BM25 ∪ pgvector → RRF → graph expansion / graph-first
                                      → evidence pack → Gemini (JSON) → validator 8 luật → trả lời
                                │
                                ▼
                 Next.js 16  http://localhost:3000  (chat, tra cứu văn bản, applicability, admin)
```

- **Backend**: Python 3.12+, FastAPI, SQLAlchemy 2, Alembic, `google-genai` (Gemini cho embedding, hiểu câu hỏi, sinh câu trả lời, LLM-judge).
- **Datastore**: PostgreSQL là hệ thống gốc; Neo4j là bản dẫn xuất dựng lại được, dùng cho graph-first search, Personalized PageRank (PPR) và kiểm chứng trích dẫn theo đường đi (validator luật 8). Không có Neo4j, hệ thống vẫn chạy chat cơ bản (các tính năng graph tự tắt).
- **Frontend**: Next.js 16 + React 19 + Tailwind 4.
- **Đánh giá**: bộ gold set 3 tầng (`data/gold/`), `scripts/evaluate.py` cho retrieval / QA / judge / ablation, báo cáo ghi vào `docs/reports/eval/<run_id>/`.

## 2. Yêu cầu hệ thống

| Thành phần | Tối thiểu | Ghi chú |
|---|---|---|
| Python | ≥ 3.12 | Máy dev dùng 3.13. Khuyên dùng [uv](https://docs.astral.sh/uv/) để tạo `.venv`; hoặc conda/pip với các gói trong `backend/pyproject.toml` |
| Node.js + npm | Node ≥ 20 | Máy dev: Node 22, npm 11 |
| PostgreSQL | ≥ 16, có extension `vector` (pgvector) | Docker `pgvector/pgvector:pg16` trong `infra/docker-compose.yml`, hoặc cài native (máy dev: PG 18 + pgvector 0.8) |
| Neo4j | 5.x hoặc 2026.x, **tùy chọn** | Bắt buộc cho graph-first / PPR / luật 8 và cấu hình `S1g`. Docker `neo4j:5-community` kèm plugin APOC + GDS đã khai báo sẵn trong compose |
| Docker Desktop | tùy chọn | Thay cho việc cài PostgreSQL / Neo4j native |
| Khóa API | `GEMINI_API_KEY` | Bắt buộc cho bước embedding và cho hỏi đáp. Có thể lấy tại Google AI Studio. Chi phí: embedding toàn bộ corpus ≈ 0,04 USD; mỗi câu hỏi ≈ 0,005 USD |
| Đĩa / RAM | ≈ 6 GB đĩa cho docling + torch, 8 GB RAM | Chỉ cần docling (`--extra pdf`) khi trích xuất 2 PDF (TT 45/2024, TT 10/2021) |

Hệ điều hành: hướng dẫn viết cho **Windows 11 + PowerShell**. Trên Linux/macOS thay `Copy-Item` bằng `cp`, `$env:X="1"` bằng `export X=1`, dấu `\` trong đường dẫn bằng `/`.

## 3. Chạy nhanh (15 lệnh)

Đường ngắn nhất, dùng Docker cho PostgreSQL + Neo4j và `uv` cho Python. Mỗi lệnh được giải thích ở mục 4.

```powershell
git clone https://github.com/hoangnttm/QCVN_Chatbot.git
cd QCVN_Chatbot
Copy-Item infra\.env.example infra\.env          # cấu hình container (mật khẩu, cổng)
Copy-Item infra\.env.example backend\.env        # cấu hình ứng dụng: điền GEMINI_API_KEY và ADMIN_TOKEN
docker compose -f infra/docker-compose.yml up -d # PostgreSQL :5432, Neo4j :7474/:7687

cd backend
uv sync --group dev --extra pdf                  # ≈ 2,5 GB (torch); bỏ --extra pdf nếu không trích PDF
uv run alembic upgrade head                      # tạo schema (head: f5a6b7c8d9e0)
$env:HF_HUB_DISABLE_SYMLINKS = "1"               # Windows + docling
uv run python scripts/ingest.py pdf              # TT 45 ≈ 2 phút, TT 10 ≈ 35 phút (chỉ lần đầu, có cache)
uv run python scripts/ingest.py all              # parse 5 văn bản → data/derived/
uv run python scripts/ingest.py load             # nạp PostgreSQL
uv run python scripts/ingest.py embed            # Gemini embedding ≈ 0,04 USD
uv run python scripts/ingest.py activate --label gemini-embedding-001@1536   # bật index + cache BM25
uv run python scripts/build_graph.py             # dựng Neo4j (tùy chọn nhưng khuyên dùng)
uv run python -m uvicorn app.main:app --reload --port 8000

# terminal thứ hai
cd frontend
npm install
npm run dev                                      # http://localhost:3000
```

Nếu dùng Miniconda/pip đã cài sẵn gói (máy dev hiện tại), bỏ tiền tố `uv run` và gọi `python …` trực tiếp.

## 4. Hướng dẫn từng bước

### Bước 1 — Lấy mã nguồn và tạo file cấu hình

```powershell
git clone https://github.com/hoangnttm/QCVN_Chatbot.git
cd QCVN_Chatbot
Copy-Item infra\.env.example backend\.env        # gitignored, không commit
```

Mở `backend/.env` và sửa các biến dưới đây. Mọi biến khác giữ mặc định của `infra/.env.example` (xem đầy đủ trong `backend/app/core/config.py`).

| Biến | Giá trị khi dùng Docker | Giá trị khi cài native (máy dev) | Ý nghĩa |
|---|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://qcvn:qcvn_dev_password@localhost:5432/qcvn` | `postgresql+psycopg://postgres@127.0.0.1:5432/qcvn_v2` | PostgreSQL. Máy dev dùng `qcvn_v2` (DB làm việc); `qcvn` là baseline đóng băng, chỉ đọc |
| `NEO4J_URI` | `bolt://127.0.0.1:7687` | `bolt://127.0.0.1:7687` | Dùng `127.0.0.1`, không dùng `localhost` (Windows thử IPv6 `::1` rồi treo) |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `qcvn_dev_neo4j` | theo Neo4j Desktop | Mật khẩu ≥ 8 ký tự |
| `NEO4J_DATABASE` | `neo4j` | `qcvnv2` | Bản Community chỉ có một database tên `neo4j`; bản Enterprise (Neo4j Desktop) dùng database riêng cho mỗi lần nạp |
| `GRAPH_BACKEND` | `neo4j` | `neo4j` | Đặt `off` nếu không cài Neo4j |
| `GEMINI_API_KEY` | khóa của bạn | khóa của bạn | Bắt buộc cho `embed` và hỏi đáp |
| `ADMIN_TOKEN` | chuỗi ngẫu nhiên | chuỗi ngẫu nhiên | Mở `/admin` và `/api/v1/admin/*` (header `X-Admin-Token`). Chưa đặt → API trả 503 |
| `HF_HUB_DISABLE_SYMLINKS` | `1` | `1` | Chỉ cần trên Windows khi chạy docling |
| `UNDERSTANDING_ENGINE` | `v1` | `v1` | `v2` = hiểu câu hỏi theo khung slot, ghim dòng bảng và hop (xem bước 9) |
| `GRAPH_FIRST_MODE` | `off` | `off` | `fuse` = tìm vùng ứng viên trong Neo4j trước rồi hợp nhất với RRF (xem bước 9) |
| `GRAPH_VERIFY_MODE` | `observe` | `observe` | `enforce` = validator luật 8 bác bỏ trích dẫn không nối được trên đồ thị |

Nếu dùng Docker, tạo thêm `infra/.env` (copy cùng file mẫu) để compose đọc mật khẩu và cổng. Cổng 5432 đang bị PostgreSQL native chiếm thì đặt `POSTGRES_PORT=5433` và sửa `DATABASE_URL` tương ứng.

### Bước 2 — Cài môi trường Python

**Cách A — uv (máy mới, CI):**

```powershell
cd backend
uv sync --group dev --extra pdf      # core + pytest/ruff + docling
# thêm --extra review nếu dùng review_pack.py / review_import.py (openpyxl)
# thêm --extra ml nếu muốn embedding local bge-m3 thay cho Gemini
```

Từ đây mọi lệnh Python chạy bằng `uv run python …` (hoặc kích hoạt `.venv\Scripts\Activate.ps1` một lần rồi gọi `python` trực tiếp).

**Cách B — conda/pip sẵn có:** cài các gói trong `backend/pyproject.toml` (`fastapi uvicorn sqlalchemy alembic psycopg[binary] pgvector pydantic-settings pyyaml neo4j rank-bm25 pyvi anthropic google-genai pypdf python-multipart docling openpyxl pytest ruff`) rồi gọi `python …` từ thư mục `backend/`.

Kiểm tra: `python -c "import app.core.config"` không báo lỗi (chạy từ `backend/`).

### Bước 3 — Khởi động PostgreSQL và Neo4j

**Cách A — Docker:**

```powershell
Copy-Item infra\.env.example infra\.env
docker compose -f infra/docker-compose.yml up -d
docker compose -f infra/docker-compose.yml ps     # cả hai service phải "healthy"
```

Compose tạo PostgreSQL (`pgvector/pgvector:pg16`, user `qcvn`, DB `qcvn`) và Neo4j 5 Community với plugin APOC + Graph Data Science (GDS). Giao diện Neo4j Browser: `http://localhost:7474`.

**Cách B — cài native (máy dev):** PostgreSQL ≥ 16 với extension `vector`, `pg_trgm`, `unaccent` (migration tự `CREATE EXTENSION`); tạo database trống:

```powershell
& "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h 127.0.0.1 -c "CREATE DATABASE qcvn_v2 ENCODING 'UTF8';"
```

Neo4j: mở instance trong Neo4j Desktop (hoặc chạy headless `bin\neo4j.bat console` với `JAVA_HOME` trỏ tới JDK 21 đi kèm Desktop; không chạy cả hai cùng lúc vì khóa store). Với bản Enterprise, tạo database làm việc trong Browser: `CREATE DATABASE qcvnv2`.

### Bước 4 — Tạo schema PostgreSQL

```powershell
cd backend
python -m alembic upgrade head
python -m alembic current          # phải in: f5a6b7c8d9e0 (head)
```

Mỗi lần kéo code mới có migration trong `backend/alembic/versions/`, chạy lại `alembic upgrade head`. Không bao giờ chạy migration lên database baseline `qcvn` đã đóng băng; thay đổi schema làm đổi dữ liệu thì nạp sang database mới và mang vector qua bằng `scripts/carry_embeddings.py --source <URL cũ>` (không tốn API).

### Bước 5 — Nạp dữ liệu (chạy từ `backend/`, theo đúng thứ tự)

`data/derived/` gitignored nên máy mới phải chạy đủ các bước. Các bước 5.1–5.3 không cần `.env`; từ 5.4 cần `DATABASE_URL`, từ 5.5 cần `GEMINI_API_KEY`.

| Bước | Lệnh | Kết quả mong đợi |
|---|---|---|
| 5.1 Trích PDF | `$env:HF_HUB_DISABLE_SYMLINKS="1"; python scripts/ingest.py pdf` | `data/derived/<doc>/pdf_extract/` cho TT 45/2024 (≈ 2 phút) và TT 10/2021 (192 trang, ≈ 35 phút CPU, cache theo lô 30 trang, chạy dở tiếp được). Lần đầu docling tải model từ Hugging Face |
| 5.2 Parse | `python scripts/ingest.py all` | ≈ 1 phút. 5 dòng `[OK ]` (coverage 100 %, 0 unresolved) → `structure / tables / rules / facts / chunks / report.json` |
| 5.3 Kiểm chất lượng (tùy chọn) | `python scripts/ingest.py verify` rồi `python scripts/ingest.py quality` | `verify`: đọc độc lập lần hai các bảng ngưỡng → `verification.json`; `quality`: cổng chất lượng lớp fact → `docs/reports/fact-quality-<ngày>.md` |
| 5.4 Nạp DB | `python scripts/ingest.py load` | Idempotent theo văn bản, giữ vector đã có; in số dòng từng bảng |
| 5.5 Embedding | `python scripts/ingest.py embed --dry-run` rồi `python scripts/ingest.py embed` | ≈ 1.300 chunk ≈ 40 request Gemini ≈ 0,04 USD, 1–2 phút; chạy dở tiếp được; `index_versions` = `inactive` |
| 5.6 Kiểm tra | `python scripts/ingest.py db` | `evidence_chunks_embedded` bằng `evidence_chunks` |
| 5.7 Kích hoạt index | `python scripts/ingest.py activate --label gemini-embedding-001@1536` | Một index `active`; lệnh cũng dựng cache BM25 tại `data/derived/index/` (`ingest.py bm25` để dựng lại riêng) |

Chỉ nạp lại một văn bản: thêm `--doc QCVN_19_2024` (lặp được) vào `all / load / embed`, rồi `ingest.py bm25` và bước 6.

### Bước 6 — Dựng đồ thị Neo4j (bỏ qua nếu `GRAPH_BACKEND=off`)

Chạy **sau** bước 5.5 vì script sao chép embedding lên node `EvidenceChunk` và tạo vector index trong Neo4j.

```powershell
cd backend                                   # phải chạy từ backend/, chạy từ gốc repo sẽ lỗi xác thực
python scripts/build_graph.py                # dựng lại toàn bộ projection (idempotent), schema v1.4
python scripts/build_graph.py --check        # bất biến giữa Neo4j và PostgreSQL, mọi dòng phải ok
python scripts/build_graph.py --counts       # đếm node / quan hệ
```

Lưu ý: `--check` và `--counts` **không** dựng lại; muốn rebuild thì chạy không tham số. Đổi database đích bằng `--database <tên>` hoặc `NEO4J_DATABASE`. So sánh hai lần dựng: `python scripts/graph_fingerprint.py`.

### Bước 7 — Chạy backend API

```powershell
cd backend
python -m uvicorn app.main:app --reload --port 8000
```

Kiểm tra:

- `http://127.0.0.1:8000/api/v1/health` → `{"status":"ok","database":true,"index_active":"gemini-embedding-001@1536","bm25_loaded":true,...}`
- Swagger: `http://127.0.0.1:8000/docs`
- Gửi thử một câu hỏi (JSON một lần):

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/questions/answer -ContentType "application/json; charset=utf-8" -Body '{"question": "Giới hạn BOD5 trong nước thải công nghiệp xả ra nguồn nước dùng cho cấp nước sinh hoạt là bao nhiêu?"}'
```

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/questions/answer -H "Content-Type: application/json" \
  -d '{"question": "Giới hạn BOD5 trong nước thải công nghiệp xả ra nguồn nước dùng cho cấp nước sinh hoạt là bao nhiêu?"}'
```

Các nhóm endpoint chính (chi tiết: [docs/api/](docs/api/), [docs/qa-v1.md](docs/qa-v1.md)):

| Nhóm | Endpoint |
|---|---|
| Hỏi đáp | `POST /api/v1/questions/ask` (SSE: `stage → understanding → evidence → delta* → answer → done`), `POST /api/v1/questions/answer`, `GET /api/v1/questions/{query_id}`, `POST /api/v1/feedback`, `GET /api/v1/history`, `GET /api/v1/me/quota` (theo header `X-Client-Id`) |
| Tra cứu | `GET /api/v1/documents`, `/documents/{id}`, `/documents/{id}/tree`, `/documents/{id}/monitoring`, `/tables/{code}`, `/evidence/{chunk_id}`, `POST /api/v1/search` |
| Áp dụng quy chuẩn | `POST /api/v1/applicability-assessments` |
| Quản trị (cần `X-Admin-Token`) | `/api/v1/admin/health`, `metrics`, `usage`, `alerts`, `query-logs`, `review/*`, `documents`, `index-versions` |

Body của `questions/*`: `{"question": "...", "as_of_date": "YYYY-MM-DD" (tùy chọn), "k": 10}`.

### Bước 8 — Chạy frontend

```powershell
cd frontend
npm install                      # một lần
npm run dev                      # http://localhost:3000
```

| Đường dẫn | Màn hình |
|---|---|
| `/` | Chat hỏi đáp, panel căn cứ, lịch sử theo trình duyệt |
| `/documents`, `/documents/{id}`, `/documents/{id}/tables/{code}` | Tra cứu văn bản, cây cấu trúc, bảng ngưỡng |
| `/applicability` | Xác định quy chuẩn / cột áp dụng theo kịch bản |
| `/admin`, `/admin/logs`, `/admin/review`, `/admin/documents` | Dashboard, nhật ký, duyệt bảng, registry và ingest; đăng nhập bằng `ADMIN_TOKEN` |

API mặc định `http://localhost:8000`; đổi bằng `frontend/.env.local` (`NEXT_PUBLIC_API_URL=...`, mẫu ở `frontend/.env.local.example`). Muốn cổng khác: `npx next dev -p 3001` và thêm origin đó vào `CORS_ORIGINS` trong `backend/.env`. Build production: `npm run build` rồi `npm start`.

### Bước 9 — Bật cấu hình cải tiến (tùy chọn)

Mặc định backend chạy cấu hình **C4** (baseline của báo cáo): hiểu câu hỏi v1, không graph-first, luật 8 chỉ quan sát. Để chạy cấu hình **S1g** (hệ thống cải tiến trong báo cáo cuối môn), đặt trong `backend/.env` rồi khởi động lại uvicorn:

```dotenv
UNDERSTANDING_ENGINE=v2        # khung slot + tách câu + hop (app/retrieval/frame.py, hops.py)
UNDERSTANDING_V2_SHADOW=false  # true = vẫn chạy v1 nhưng ghi lại kết quả v2 để so sánh
GRAPH_FIRST_MODE=fuse          # tìm vùng ứng viên trong Neo4j trước, hợp nhất vào RRF (cần bước 6)
GRAPH_VERIFY_MODE=enforce      # validator luật 8: trích dẫn phải nối được trên đồ thị
```

PPR (cấu hình C12 / S1) chỉ bật theo từng request đánh giá và cần plugin GDS trong Neo4j. Reranker LLM (C9 / C10) cũng chỉ dùng trong đánh giá.

## 5. Chạy hằng ngày

PostgreSQL và Neo4j đã chạy nền (service hoặc `docker compose … up -d`). Mở hai terminal:

```powershell
# terminal 1
cd backend; python -m uvicorn app.main:app --reload --port 8000
# terminal 2
cd frontend; npm run dev
```

Mở `http://localhost:3000`. Khi kéo code mới: `git pull`, `uv sync` (nếu `pyproject.toml` đổi), `alembic upgrade head`, `npm install` (nếu `package.json` đổi). Khi sửa dữ liệu nguồn hoặc từ điển: chạy lại 5.2 → 5.7 cho văn bản liên quan rồi bước 6.

## 6. Kiểm thử và lint

```powershell
cd backend
python -m ruff check .; python -m ruff format --check .
python -m pytest -q                              # tự tạo DB qcvn_test trên cùng server (cần quyền CREATEDB); bỏ qua test DB nếu không có PostgreSQL
python -m pytest tests/test_frame.py tests/test_graph_first_retrieval.py -q   # chạy riêng một nhóm
$env:QCVN_LIVE_TESTS="1"; python -m pytest tests/test_embed.py -q   # gọi Gemini thật (vài token)

cd ../frontend
npm run lint; npm run build
```

`tests/test_graph.py` dựng projection vào database Neo4j riêng tên `qcvntest` (Enterprise). Trên bản Community chỉ có một database, test này ghi đè projection của bạn, sau đó chạy lại `python scripts/build_graph.py`. CI (`.github/workflows/ci.yml`) chạy lint, pytest và cổng hồi quy retrieval `C4-offline` trên split dev với ba văn bản Markdown.

## 7. Đánh giá (evaluation) và ablation

Bộ gold set và công cụ: [data/gold/README.md](data/gold/README.md), [docs/reports/eval/README.md](docs/reports/eval/README.md). Cấu hình trong `backend/app/evaluation/configs.py`: `C0` BM25, `C1` dense, `C2` hybrid RRF, `C3` + structured, **`C4` production**, `C4-u2` (+ hiểu câu hỏi v2), `C4-gf` (+ graph-first), `C12` (+ PPR), **`S1g`** (= C4 + v2 + graph-first + luật 8) và các nhánh leave-one-out `S1g-nov2 / S1g-nogf / S1g-nov8`.

```powershell
cd backend
python scripts/gold.py validate --tier A,B,M --resolver db        # bộ gold v3 hợp lệ, 0 lỗi
python scripts/evaluate.py gold-check --tier all

# Retrieval (không tốn Gemini ngoài embedding câu hỏi)
python scripts/evaluate.py ablation --configs C2,C4,C4-u2,C4-gf,C4-u2-gf --split dev --tier A,B,M
python scripts/evaluate.py retrieval --config S1g --split val --tier A,M
python scripts/evaluate.py understanding --engine v2 --split dev,val --tier A,B,M   # slot F1 của bước hiểu câu hỏi
python scripts/evaluate.py graph-first --split dev --tier A,M                       # độ phủ và độ trễ của graph-first

# QA đầu-cuối (Gemini thật, ≈ 0,005 USD/câu) rồi LLM-judge (≈ 0,01 USD/câu, có cache)
python scripts/evaluate.py qa --config C4  --split dev --tier A,B,M --repeats 3 --no-cache --warmup
python scripts/evaluate.py qa --config S1g --split dev --tier A,B,M --repeats 3 --no-cache --warmup
python scripts/evaluate.py judge --run <run_id>
python scripts/evaluate.py human-sheet --run <run_id> --frac 0.2        # phiếu chấm mù 20 %
python scripts/evaluate.py agreement --run <run_id> --sheet <phiếu>.csv
python scripts/evaluate.py compare --family BT-E --runs C4=<run>,S1g=<run>
python scripts/evaluate.py report --run <run_id>                        # render lại report.md
```

Mỗi lượt chạy tạo `docs/reports/eval/<YYYYMMDD-HHMM>-<config>-<split>[-rN]/` với `manifest.json` (commit, hash gold), `retrieval.jsonl`, `qa.jsonl`, `judge.jsonl`, `summary.json`, `report.md`. Lượt chạy trên **split test** cần thêm `--confirm-test` và được ghi vào `test_runs.log`; tập test niêm phong chỉ chạy một lần cho báo cáo. Kịch bản ablation đầy đủ: [docs/plans/2026-10-21-kich-ban-ablation.md](docs/plans/2026-10-21-kich-ban-ablation.md); danh sách run dùng trong báo cáo: [docs/reports/eval/bt-cuoi-runs.md](docs/reports/eval/bt-cuoi-runs.md); dựng lại bảng và hình của báo cáo: `python report-resources/_scripts/build_bt_cuoi.py`.

## 8. Cấu trúc thư mục

```text
sources/              Văn bản gốc, bất biến (SHA-256 ghi trong data/registry/documents.yaml)
data/registry/        Document Registry: mã, hash, ngày, trạng thái, quan hệ giữa văn bản
data/grammars/        Grammar cấu trúc per-document cho parser (kế thừa được)
data/dictionaries/    Ontology dạng dữ liệu: thông số, đơn vị, cột/vùng, ngành, thiết bị, alias,
                      chiều điều kiện, ý định (intents.yaml), hop nhiều bước (hops.yaml), routing
data/rules/           Rule applicability (YAML, có phiên bản)
data/gold/            Gold set v2 (đóng băng) và v3 (dev / val / test), mẫu câu, strata
data/derived/         (gitignored) artefact ingest, cache docling, cache BM25
backend/app/          api/ · core/ · db/ · ingestion/ · ontology/ · registry/ · retrieval/
                      (frame.py, hops.py, candidates.py, retriever.py) · graph/ · qa/ · evaluation/ · ops/
backend/scripts/      ingest.py, build_graph.py, carry_embeddings.py, evaluate.py, gold.py,
                      review_pack.py, review_import.py, purge_logs.py, llm_bench.py, graph_fingerprint.py
backend/tests/        pytest (DB test tự tạo qcvn_test; Neo4j test dùng qcvntest)
backend/alembic/      migration PostgreSQL (head f5a6b7c8d9e0)
frontend/src/app/     Next.js App Router: /, /documents, /applicability, /admin/*
infra/                docker-compose.yml (PostgreSQL + pgvector, Neo4j + APOC + GDS), .env.example
docs/                 ADR, ontology, data dictionary, retrieval/qa spec, graph schema, runbook, UAT,
                      plans/ (kế hoạch theo ngày), reports/ (eval, báo cáo cuối môn), diagrams/
report-resources/     Số liệu (CSV), hình (draw.io, PNG) và script dựng báo cáo
```

## 9. Sự cố thường gặp

| Triệu chứng | Nguyên nhân / cách xử lý |
|---|---|
| `database not reachable: …` khi start API | PostgreSQL chưa chạy, sai cổng hoặc `DATABASE_URL`; thử `psql -U postgres -h 127.0.0.1 -d qcvn_v2 -c "select 1"` hoặc `docker compose ps` |
| Alembic báo `Target database is not up to date` / thiếu cột | `python -m alembic upgrade head` (head hiện tại `f5a6b7c8d9e0`) |
| `pdf_extract cache missing — run ingest.py pdf` | Chưa chạy bước 5.1 cho văn bản PDF |
| docling `WinError 1314` (symlink) | Thiếu `HF_HUB_DISABLE_SYMLINKS=1` |
| docling lỗi SSL / không tới được huggingface.co | Model đã có trong cache → `$env:HF_HUB_OFFLINE="1"` |
| docling `std::bad_alloc` | OCR đã tắt mặc định (ADR-0009); không bật `do_ocr` |
| Gemini `429 RESOURCE_EXHAUSTED` | Embedder tự retry; free tier giới hạn token/phút → `embed --limit 200` nhiều lần |
| `/health` trả `index_active: null` | Chưa chạy bước 5.7 `activate` |
| Kết nối Neo4j treo hoặc timeout | `NEO4J_URI` dùng `localhost` → đổi sang `127.0.0.1`; kiểm tra cổng 7687; `GRAPH_BACKEND=off` nếu không dùng graph |
| `build_graph.py` báo lỗi xác thực | Chạy từ thư mục `backend/`, không chạy từ gốc repo |
| Chạy `build_graph.py --check` nhưng đồ thị vẫn cũ | `--check` / `--counts` không dựng; chạy `build_graph.py` không tham số |
| Neo4j Desktop và bản headless tranh nhau store (`store lock`) | Chỉ chạy một trong hai |
| Sau `pytest` kết quả graph-first lệch | `tests/test_graph.py` ghi đè projection trên bản Community → chạy lại `build_graph.py` |
| Frontend `fetch failed` / `ECONNREFUSED 8000` | Backend chưa chạy hoặc `NEXT_PUBLIC_API_URL` sai |
| Trình duyệt báo CORS | Frontend chạy ở origin khác `localhost:3000` → thêm vào `CORS_ORIGINS` |
| `npm run dev -- --port 3001` chạy sai | npm 11 nuốt tham số; dùng `npx next dev -p 3001` |
| pytest `permission denied to create database` | Tài khoản trong `DATABASE_URL` cần quyền `CREATEDB` |
| `/api/v1/admin/*` trả 503 / 401 | Chưa đặt `ADMIN_TOKEN` / header `X-Admin-Token` sai |
| Tên file PDF TT 10 có dấu (NFD) mở không được | Registry loader tự xử lý NFC/NFD; không đổi tên file trong `sources/` (hash sẽ lệch) |

Thêm: [docs/runbook-incidents.md](docs/runbook-incidents.md) (ý nghĩa từng cảnh báo vận hành), [docs/dev-setup.md](docs/dev-setup.md) §9.

## 10. Nguyên tắc không thương lượng

1. LLM không bao giờ tạo hay sửa `LimitRule`, `TransitionRule`, quan hệ `REPLACES` / `REPEALS`; con số trong câu trả lời lấy từ fact, không do LLM gõ.
2. Mọi câu trả lời khẳng định phải có citation hợp lệ với địa chỉ pháp lý; không có thì abstain.
3. Bảng giữ nguyên cấu trúc (header, dòng, đơn vị, footnote, nguồn), không tách thành text thuần.
4. Văn bản dự thảo gắn `text_status=draft`; ngày ký / hiệu lực do quản trị viên khai báo là `registry_fact` riêng, có audit. Từ 2026-09-24 cả 5 văn bản là `official`; TT 45/2024 và TT 10/2021 vẫn dùng `registry_fact` vì file không in số và ngày.
5. Tập test niêm phong chỉ chạy ở cuối với `--confirm-test`; mọi tinh chỉnh dùng dev / val.

## 11. Bảng thuật ngữ

| Thuật ngữ | Nghĩa trong dự án |
|---|---|
| GraphRAG | Retrieval-Augmented Generation có dùng đồ thị tri thức để tìm và kiểm chứng căn cứ |
| Chunk / evidence chunk | Đơn vị văn bản được lập chỉ mục (điều khoản, dòng bảng) kèm địa chỉ pháp lý |
| Fact layer | Lớp "khẳng định có điều kiện" trích từ bảng ngưỡng: ô, chiều điều kiện, hiệu lực, độ tin cậy |
| BM25 | Xếp hạng từ khóa (thư viện `rank-bm25`, token hóa tiếng Việt bằng `pyvi`) |
| pgvector / HNSW | Extension PostgreSQL lưu vector embedding và chỉ mục tìm gần đúng |
| RRF | Reciprocal Rank Fusion, hợp nhất nhiều danh sách xếp hạng |
| Structured lookup | Tra thẳng bảng ngưỡng từ slot của câu hỏi, không qua tìm kiếm văn bản |
| Graph expansion | Mở rộng evidence pack theo quan hệ (văn bản ↔ điều khoản ↔ bảng ↔ thực thể) |
| Graph-first | Tìm vùng ứng viên trong Neo4j (quan hệ → ứng viên → cosine trên vector node) trước khi hợp nhất với RRF |
| PPR | Personalized PageRank chạy bằng Neo4j Graph Data Science (GDS), dùng trong cấu hình C11 / C12 / S1 |
| Evidence pack | Tập căn cứ được chọn cho LLM và hiển thị ở panel căn cứ |
| Validator (8 luật) | Bộ kiểm tra câu trả lời: citation hợp lệ, số liệu có trong căn cứ, đúng miền, … ; luật 8 kiểm theo đường đi trên đồ thị |
| Abstain | Từ chối trả lời khi không đủ căn cứ, thay vì đoán |
| Understanding v1 / v2 | Bước hiểu câu hỏi: v1 = từ khóa + LLM dự phòng; v2 = khung slot có kiểu, tách câu, hop nhiều bước (`frame.py`, `hops.py`) |
| Applicability | Xác định quy chuẩn, cột, hệ số áp dụng cho một kịch bản xả thải |
| Gold set / tier / split | Bộ câu hỏi chuẩn; tầng A (sinh từ bảng), B (soạn tay), M (nhiều bước), C (nhật ký thật); split dev / val / test |
| Ablation | Thí nghiệm tắt / bật từng thành phần để đo đóng góp (C0 … S1g) |
| LLM-judge | Dùng Gemini chấm câu trả lời theo rubric, có kiểm chéo với người chấm |
| SSE | Server-Sent Events, luồng trả lời từng phần của `POST /questions/ask` |
| Projection | Bản sao dẫn xuất của PostgreSQL trong Neo4j, dựng lại được bằng `build_graph.py` |
| Registry fact | Thông tin đăng ký (số, ngày, hiệu lực) do quản trị viên khai báo, có audit |
