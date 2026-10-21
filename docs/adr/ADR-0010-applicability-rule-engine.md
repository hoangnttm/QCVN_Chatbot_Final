# ADR-0010 — Applicability engine: rule khai báo trong YAML, handler Python xác định, evidence bắt buộc

**Trạng thái:** Accepted · **Ngày:** 2026-09-23 · Liên quan: PLAN.md §7, §11; ADR-0003 (provenance), ADR-0004 (registry fact)

## Context
PLAN §7 yêu cầu "xác định quy chuẩn có khả năng áp dụng" hoàn toàn xác định, không LLM, mỗi rule có `evidence_chunk_ids`, kết quả có `missing_information` và cảnh báo bản dự thảo. Rule W-01…W-12, E-01…E-09, M-01…M-04 vừa có phần "logic" (điều kiện kích hoạt) vừa có phần "hành vi dữ liệu" (chọn dòng T-P, nhóm cột theo lưu lượng, lọc theo thiết bị/nhiên liệu/công suất, tính chuyển tiếp theo registry fact) — phần sau không diễn đạt gọn bằng một DSL tổng quát.

## Decision
1. **YAML là nguồn sự thật cho rule** (`data/rules/applicability_v1.yaml`): id, miền, tiêu đề, điều kiện kích hoạt `when` (DSL nhỏ: eq/ne/in/not_in/gt/gte/lt/lte trên trường ngữ cảnh), `requires` (trường bắt buộc), `action` (tên handler), `params`, `message`, `evidence`. Schema ngữ cảnh (trường, kiểu, `required_for`) cũng nằm trong YAML để UI sinh form.
2. **Handler Python có tên cố định** (`Assessor._act_<action>`): candidate, exclude, indirect, note, zone_source, default_zone, special_cases, flow_regime, sector_parameters, add_parameters, receiving_body, transition, equipment_match, unknown_parameters, effective_fact, monitoring_note, qa_policy. Handler chỉ đọc PostgreSQL (limit_rules, table_rows, registry_facts, document_relations) và từ điển; không gọi mạng, không LLM.
3. **Evidence resolve khi nạp**: nhãn trích dẫn → `evidence_chunks.citation_label`; registry fact → chunk `registry_fact`; chú thích bảng → chunk footnote của bảng; MonitoringRequirement → chunk nguồn. Bất kỳ tham chiếu nào không resolve được → engine từ chối khởi động (test `test_rules_load_and_every_evidence_resolves`).
4. **Thiếu thông tin → `missing_information`, không suy đoán**; ngoại lệ có căn cứ văn bản: `zone` unknown → Cột B (TT 06 Đ2.4 / TT 45 Đ2.4) kèm cảnh báo và vẫn ghi thiếu.
5. **Không bỏ dữ liệu im lặng**: nhiên liệu/công suất không khớp dòng thiết bị → cảnh báo và giữ dòng để tham khảo; thiết bị không có dòng riêng → dòng "thiết bị xả thải khác" + cảnh báo.
6. **Cảnh báo bắt buộc** gắn tự động: bản dự thảo (theo `text_status` của chunk/bảng), `unverified`, LimitRule `pending`, chuyển tiếp chưa bắt buộc. Kết quả luôn có `disclaimer` "đánh giá sơ bộ".
7. Kết quả lưu ở `applicability_assessments` (id, context, result JSON) để `GET /{id}` và audit; ephemeral về mặt nghiệp vụ (không gắn với cơ sở/người dùng).

## Consequences
- Thêm/sửa rule = sửa YAML + (nếu cần hành vi mới) một handler nhỏ có test; chuyên gia đọc được YAML và sheet kịch bản.
- Rule phụ thuộc vào id trong từ điển (parameter_id, sector_id, equipment_id) và mã dòng bảng (row_id): đổi từ điển/grammar phải chạy lại test kịch bản.
- Chưa có DSL cho logic phức tạp — chấp nhận để giữ engine dễ kiểm chứng; nếu số rule tăng nhiều (P5 mở rộng corpus) mới cân nhắc rule engine tổng quát.
- Graph Neo4j chỉ là projection; engine dùng `PostgresGraphStore` cho đường bằng chứng nên chạy được khi Neo4j tắt.
