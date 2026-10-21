# ADR-0009 — Trích xuất PDF bằng docling (OCR tắt, theo lô trang), pypdf làm fallback

**Trạng thái:** Accepted — spike lần 2 đạt (PLAN.md D12) · **Ngày:** 2026-09-23

## Context
Hai PDF là text-based (không cần OCR) nhưng pypdf trả text có lỗi font kế thừa (`ƣ`) và **khoảng trắng chèn trong từ** (`c ứ`, `quyđịnh`), bảng mất lưới. Docling 2.75 có sẵn trong môi trường dev.

## Bằng chứng từ spike (2026-09-23)
| Chạy | Cấu hình | Kết quả |
|---|---|---|
| Lần 1 (cả 192 trang) | mặc định (OCR bật, images_scale 2.0) | TT45: 3 trang OK, 0 bảng (đúng). TT10: thân văn bản (tr. 1–56) **rất sạch** — khoảng trắng trong từ được sửa đúng, `Điều 17` đọc chuẩn; 9 bảng khôi phục ở tr. 22–39 và 153–154; **132/192 trang phụ lục lỗi `std::bad_alloc`** ở bước preprocess → Phụ lục 4.1/4.2 (Bảng 25, 26) không có trong output. 196 s. |
| Lần 2 (tr. 105–133) | OCR tắt, TableFormer ACCURATE, images_scale 1.0 | **Không còn `bad_alloc`.** Bảng 25 (2×3) và Bảng 26 Phụ lục 4.1 khôi phục thành Markdown grid đúng cột, phương pháp ngăn bằng `;`; các bảng Phụ lục 4.2, 4.3, 5, 6 (tr. 108–131) đều được nhận diện. Còn ký tự font kế thừa `ƣ` trong tiêu đề/bảng ("nƣớc thải", "phƣơng pháp") và vài chỗ mất khoảng trắng ("SMEWW2550B") → xử lý ở `normalize_legacy_text`. Chi tiết: `docs/spikes/docling-spike.md`. |

## Decision
1. `pdf_extract` dùng docling với `do_ocr=False`, `images_scale=1.0`, TableFormer ACCURATE, **chạy theo lô trang** (≤ 30 trang/lô) và ghi `page_map`.
2. Song song xuất text thuần bằng pypdf + `normalize_legacy_text` để đối chiếu coverage; trang docling lỗi → fallback pypdf + `review_task`.
3. Bảng không khôi phục được lưới → lưu dạng text-flow với cảnh báo, không "im lặng".

## Consequences
- Bước ingest PDF chậm hơn (≈ 1–2 s/trang CPU) nhưng chỉ chạy khi có version mới.
- Cần `HF_HUB_DISABLE_SYMLINKS=1` trên Windows khi tải model docling lần đầu (đã ghi vào README).
