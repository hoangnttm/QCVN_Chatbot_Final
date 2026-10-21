# ADR-0003 — Provenance bằng SourceSpan + citation label cấu trúc; số trang chỉ với PDF

**Trạng thái:** Accepted (PLAN.md D3, PO 2026-09-23) · **Ngày:** 2026-09-23

## Context
Hai QCVN trong corpus là Markdown chuyển từ PDF, **không có số trang**; `SourcePage.page_no` của thiết kế gốc không lấp được. Hai Thông tư bổ sung là PDF nên có số trang, nhưng TT 10/2021 đánh số trang in lại từ 1 ở phần Phụ lục.

## Decision
- Đơn vị provenance là `SourceSpan{file_hash, line_start, line_end, char_start, char_end, pdf_page_start?, pdf_page_end?, printed_page?}`. Mọi node cấu trúc, dòng bảng, chunk, rule đều trỏ về một span.
- Citation hiển thị theo **đường dẫn cấu trúc pháp lý**, không theo trang: `QCVN 40:2025/BTNMT, Mục 2.2, Bảng 2, TT 4.2, Cột B`. Với PDF thêm hậu tố `tr. N` (trang PDF) hoặc `tr. PL-N` (trang in phụ lục). Văn bản dự thảo có hậu tố `(dự thảo)`. Đặc tả đầy đủ: `docs/citation-spec.md`.
- Mỗi label ánh xạ 1–1 tới `evidence_chunk.id`; API trả cả id lẫn label.

## Consequences
- Người dùng tra được đúng điều/khoản/dòng bảng thay vì trang.
- Khi có PDF gốc của hai QCVN, thêm bước page-align (roadmap P1) để bổ sung `pdf_page` cho span Markdown mà không đổi label.
