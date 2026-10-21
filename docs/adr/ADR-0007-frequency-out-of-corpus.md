# ADR-0007 — Không nạp NĐ 08/2022 và NĐ 05/2025; câu hỏi tần suất quan trắc luôn abstain có chỉ dẫn

**Trạng thái:** Accepted (PLAN.md D10, PO 2026-09-23) · **Ngày:** 2026-09-23

## Context
QCVN 40:2025 và QCVN 19:2024 không quy định tần suất quan trắc. TT 10/2021 Điều 16.3 và 17.5 ghi *"Tần suất quan trắc … được thực hiện theo quy định của pháp luật hiện hành"* — tức Nghị định 08/2022/NĐ-CP (Điều 97, 98), đã sửa bởi NĐ 05/2025/NĐ-CP. Các mức tần suất ở TT 10/2021 Điều 6–13 là cho chương trình quan trắc chất lượng môi trường nền, không áp dụng cho cơ sở.

## Decision
- **Không** đưa NĐ 08/2022 / NĐ 05/2025 vào corpus MVP (PO). Hai văn bản là `external_stub`.
- Rule **M-01**: mọi câu hỏi tần suất quan trắc nước thải/khí thải → abstain với câu trả lời chuẩn nêu rõ căn cứ dẫn chiếu (cite TT10 Đ16.3/Đ17.5) và chỉ ra văn bản ngoài phạm vi.
- Chunk TT10 Chương II (môi trường nền) **không** embed (ADR-0008) để retrieval không lẫn "02 tháng/đợt" vào câu hỏi nước thải.
- Node `Frequency` bỏ khỏi ontology MVP; NĐ 08/2022 vào backlog P5.

## Consequences
- UC-03 phần tần suất là abstain có kiểm soát; gold set phải có ≥ 5 câu tần suất (kể cả câu bẫy nước mặt) để bảo đảm hệ thống không bịa.
