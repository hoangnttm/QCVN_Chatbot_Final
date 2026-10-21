# ADR-0008 — TT 10/2021 chỉ ingest phần thuộc miền nước thải/khí thải

**Trạng thái:** Accepted (PLAN.md D11, F12) · **Ngày:** 2026-09-23

## Context
TT 10/2021 dài 192 trang, phạm vi rộng hơn MVP: Chương II (môi trường nền), III (POP), VI (trạm quan trắc nước mặt/không khí), Phụ lục 1–3, 15–16 và các mẫu báo cáo. Nạp toàn bộ làm nhiễu retrieval và tăng khối lượng review.

## Decision
- Phân vùng khai báo trong `data/grammars/tt_10_2021.scope.yaml`:
  - `in_scope` (parse + chunk + embed, gắn domain): Chương I, IV (Đ16–18), V, VII (Đ33–38), VIII, X; Phụ lục 4.1–4.3, 5, 6, 9–14.
  - `structure_only` (chỉ parse cấu trúc để cây văn bản đầy đủ, **không** chunk/embed, `domain ∈ {ambient, pop, other}`): phần còn lại.
- Router: câu hỏi về môi trường nền/POP → `out_of_scope` với gợi ý "TT 10/2021 có quy định nhưng ngoài phạm vi MVP".
- Từ TT10 chỉ sinh `MonitoringRequirement` (điều kiện lấy mẫu, điểm lấy mẫu, đo trực tiếp, CEMS, frequency pointer) và `Method`; **không** sinh `LimitRule`.

## Consequences
- Khối lượng chunk TT10 ≈ 250–350 thay vì > 1.000; giữ lộ trình 12 tuần.
- Mở rộng sang môi trường nền chỉ cần đổi `scope` trong file phân vùng và chạy lại ingest.
