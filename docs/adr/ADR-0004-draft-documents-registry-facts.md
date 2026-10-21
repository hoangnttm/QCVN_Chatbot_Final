# ADR-0004 — Văn bản dự thảo và registry fact do quản trị viên khai báo

**Trạng thái:** Accepted (PLAN.md D4, D9, D13, §4.5) · **Ngày:** 2026-09-23

## Context
`45-btnmt.pdf` (TT 45/2024) và `Thông tư 10-2021-TT-BTNMT.pdf` là **bản dự thảo**: số hiệu, ngày ký, ngày hiệu lực để trống. File Markdown QCVN 19 cũng để trống số Thông tư ở Lời nói đầu. PO đã cung cấp ngày: TT 45/2024 ký 30/12/2024 tại Hà Nội, hiệu lực 01/7/2025; TT 10/2021 ban hành 30/6/2021, hiệu lực 16/8/2021. Nguyên tắc citation-first không cho phép trả lời một ngày mà không cite được evidence.

## Decision
1. `documents.text_status ∈ {official, draft, unverified}` nhập tay trong `data/registry/documents.yaml`, không suy ra từ file.
2. Ngày/số hiệu là `registry_fact{fact_type, value, source ∈ {from_text, admin_declared}, declared_by, declared_at}`. Fact `admin_declared` sinh `AuditEvent` và một `EvidenceChunk(chunk_type=registry_fact)` không embed, label `Sổ đăng ký văn bản: … (do quản trị viên khai báo)`.
3. Bản dự thảo **không được** sinh fact ngày `from_text`; loader từ chối (test `test_draft_documents_only_declare_dates_via_admin`).
4. Câu trả lời cite văn bản `draft` bắt buộc kèm cảnh báo "bản dự thảo"; câu trả lời về ngày ký/hiệu lực phải cite chunk `registry_fact`.
5. Khi nhận bản ký: tạo `DocumentVersion` mới, diff cấu trúc + bảng, fact `from_text` mới đối chiếu với fact `admin_declared` — lệch → `review_task(risk=high)`; khớp → fact cũ `superseded_by`. Không xóa version dự thảo.

## Consequences
- Hệ thống trả lời được UC-06 cho QCVN 19 ngay từ MVP mà vẫn minh bạch nguồn gốc ngày.
- Nợ dữ liệu phải theo dõi: bản ký TT 45/2024 (kèm QCVN 19), bản ký TT 10/2021, tình trạng sửa đổi TT 10/2021 sau 2021.

## Addendum 2026-09-24 — cả ba văn bản được xác nhận là bản chính thức
PO xác nhận (2026-09-24) rằng `45-btnmt.pdf` (TT 45/2024), `Thông tư 10-2021-TT-BTNMT.pdf` (TT 10/2021) và file Markdown QCVN 19:2024 là **bản chính thức đã được đối chiếu với bản gốc** → `text_status=official` cho cả 5 văn bản (registry `documents.yaml`, grammar bỏ hậu tố "(dự thảo)" trong citation label). Vì các file vẫn để trống số hiệu/ngày ký/ngày hiệu lực nên quyết định 2 giữ nguyên: số/ngày của TT 45 và TT 10 là `registry_fact` `admin_declared` (khai báo 2026-09-23), được cite qua chunk "Sổ đăng ký văn bản" kèm cảnh báo nguồn ngày ("do quản trị viên khai báo; văn bản không in ngày"). Validator rule 6 và rule E-07 của engine nay khóa theo **chunk registry fact do quản trị viên khai báo** thay vì theo trạng thái dự thảo; quyết định 3–5 vẫn áp dụng cho bất kỳ văn bản `draft` nào nạp sau này. Prompt QA lên `qa-v1.1`.
