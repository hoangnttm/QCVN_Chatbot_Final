# Applicability engine v1 — rule set và cách hoạt động

Triển khai PLAN.md §7 (Phase 3). Mã: `backend/app/applicability/` (`rules.py` nạp YAML + resolve evidence, `engine.py` handler, `models.py` I/O); rule: `data/rules/applicability_v1.yaml`; kịch bản: `data/rules/applicability_scenarios_v1.yaml`; API: `POST /api/v1/applicability-assessments`, `GET …/{id}`, `GET /api/v1/applicability/schema/{domain}`; UI: `/applicability`.

## Nguyên tắc

1. **Hoàn toàn xác định** — không có LLM; cùng ngữ cảnh → cùng kết quả (test `test_deterministic`).
2. **Mọi rule kích hoạt đều có evidence** — `evidence` trong YAML là nhãn trích dẫn / registry fact / chú thích bảng / MonitoringRequirement, được resolve thành `chunk_id` khi nạp; thiếu → lỗi, engine không chạy.
3. **Thiếu thông tin thì báo thiếu, không đoán** — trường trong `requires` trống → rule bị bỏ qua và ghi `missing_information` (trừ `dossier_date` khi dự án mới).
4. **Luôn cảnh báo**: ngày do quản trị viên khai báo (TT 45, TT 10 — file không in ngày), bản dự thảo / text `unverified` nếu có (từ 2026-09-24 cả 5 văn bản `official`), LimitRule `pending` (chưa chuyên gia duyệt), Cột B mặc định, quy chuẩn cũ trong giai đoạn chuyển tiếp. Không bao giờ xuất "đạt / không đạt".

## Ngữ cảnh người dùng cung cấp (schema)

| Miền | Trường | Kiểu | Dùng cho |
|---|---|---|---|
| Nước thải | `wastewater_kind` (industrial / offshore_oil_gas / livestock / domestic_urban) | enum | W-02 |
| | `discharges_to_receiving_water`, `connected_to_centralized_system`, `mixed_domestic`, `is_cooling_water`, `is_aquaculture`, `uses_chlorine` | bool | W-01, W-03, W-04, W-10, W-11 |
| | `sector_id` (S01–S54, Phụ lục 2), `receiving_body_type` (lake / river / sea / saline_brackish), `zone` (A/B/C/unknown), `flow_m3_day` | enum/number | W-05…W-09 |
| | `project_status` (operating / approved_before / submitted_before / new), `dossier_date`, `as_of_date` | enum/date | W-12 |
| Khí thải | `has_stack_emission`, `is_vehicle`, `co_processes_waste` | bool | E-01, E-02, E-04 |
| | `zone`, `equipment_type_id` (94 loại theo Bảng 1/2), `fuel_type`, `capacity_value` + `capacity_unit`, `parameters` | enum/number/list | E-03, E-04, E-06, E-08 |
| | `project_status`, `dossier_date`, `as_of_date` | enum/date | E-07, E-09 |

## Rule set v1 (thứ tự chạy = thứ tự trong file)

| Rule | Hành vi engine | Căn cứ (đã resolve thành chunk) |
|---|---|---|
| W-02 | Loại nước thải dầu khí biển / chăn nuôi / sinh hoạt–đô thị → `excluded`, chỉ ra QCVN khác (ngoài corpus); mọi rule chọn ngưỡng phía sau không chạy | QCVN 40 Mục 1.1.2, 4.9 |
| W-01 | Xả ra nguồn tiếp nhận → ứng viên QCVN 40:2025 | Mục 1.1.1, 1.2, 1.3.2 |
| W-03 | Đấu nối hệ thống tập trung → `indirect` (ngưỡng chỉ tham khảo) | Mục 4.10, 4.11 |
| W-04 | Sinh hoạt/chăn nuôi nhập chung → ghi chú quản lý như nước thải công nghiệp | Mục 4.8 |
| W-06 | Cột A/B/C theo chức năng nguồn tiếp nhận (QCVN 08:2023 ngoài corpus) → nếu chưa rõ ghi `missing_information(zone)` | Mục 1.3.3, 1.3.3.1–3 |
| W-05 | `zone` unknown → Cột B + cảnh báo | TT 06 Điều 2.4 |
| W-10 | Nước làm mát → chỉ Temperature (+ Clo dư, Chloroform nếu dùng Clo), bỏ Bảng 1; thủy sản → pH, Coliform, T-N, T-P (Bảng 2) + BOD5/COD/TOC/TSS Bảng 1 **mức F ≤ 2000** không phân biệt lưu lượng | Mục 4.3.1–4.3.3 |
| W-07 | `flow_m3_day` ≤ / > 2000 → nhóm cột Bảng 1 | Mục 4.1.1 |
| W-09 | `sector_id` → dòng Phụ lục 2: thông số đặc trưng (cột 4) vào tập kiểm soát, cột 5 thành ghi chú; luôn kèm pH, Coliform | Mục 4.1.1, 4.1.2, 4.1.4 |
| W-11 | Dùng Clo → thêm Residual_chlorine, Chloroform | Mục 4.1.3 |
| W-08 | Nguồn tiếp nhận + loại hình → dòng T-P: hồ → 4.1; sông/biển + (thủy sản, chế biến thủy sản, tinh bột sắn, cao su) → 4.2; sông/biển khác → 4.3; mặn–lợ → bỏ Clorua | Bảng 2 TT 4.1, 4.2, 4.3, 30 |
| W-12 | Chuyển tiếp: ngày hiệu lực từ registry fact TT 06 (01/9/2025, from_text), mốc 01/01/2032 → `new_standard` / `old_standard_until_deadline` (liệt kê QCVN cũ từ quan hệ REPLACES) / `not_yet_effective` | TT 06 Điều 2.1, 3.1, 4.1–4.3 |
| W-13 | Thông số mới → tiêu chuẩn G7 (chỉ ghi chú) | Mục 4.1.5, 4.2 |
| E-02 | Phương tiện giao thông → `excluded` | QCVN 19 Mục 1.1.2 |
| E-01 | Có thiết bị xả khí thải → ứng viên QCVN 19:2024 | Mục 1.1.1, 1.2, 1.3.5 |
| E-03 | Cột theo phân vùng môi trường → chưa rõ ghi thiếu | Mục 1.3.13, 1.3.13.1–3 |
| E-08 | `zone` unknown → Cột B + cảnh báo (Cột B mặc định) | TT 45 Điều 2.4 |
| E-04 | `equipment_type_id` → mọi LimitRule của thiết bị đó ở Bảng 1/2; không có → dòng "Các thiết bị xả thải khác" + cảnh báo; `fuel_type` / `capacity` lọc theo điều kiện dòng, không khớp → **cảnh báo, giữ dòng để tham khảo** (không bỏ im lặng); `co_processes_waste` không khớp → ghi chú | Mục 4.2.1, 4.2.2, 1.3.12 |
| E-05 | Ghi chú O₂ tham chiếu (giá trị `ref_o2` đã có trong từng ngưỡng) | Mục 1.3.11 + chú thích Bảng 1 |
| E-06 | `parameters` quan tâm chưa có dòng → cảnh báo G7 | Mục 4.2.3, 4.3 |
| E-07 | Hiệu lực QCVN 19 = registry fact TT 45 `effective_date` (admin_declared) → cảnh báo "do quản trị viên khai báo" | registry fact TT 45 |
| E-09 | Chuyển tiếp như W-12 với TT 45 (hiệu lực 01/7/2025 khai báo) + `missing_information(local_roadmap)` vì UBND tỉnh có thể ban hành lộ trình sớm hơn | TT 45 Điều 3.1, 4.1–4.3, registry fact |
| M-03, M-04 | Ghi chú điều kiện lấy mẫu / đo trực tiếp từ MonitoringRequirement TT 10 | TT10.MR.10, 11, 13, 18, 19, 25, 28 |
| M-01, M-02 | `qa_policy` — không chạy trong assessment; Phase 4 dùng cho câu hỏi tần suất / phương pháp | TT 10 Đ16.3, Đ17.5, Đ3.1, Đ16.1, Đ17.1 |

## Kết quả

`AssessmentResult`: `candidates[]` (candidate / excluded / indirect + pointer), `effective_zone` + `zone_defaulted`, `transition` (applies, ngày hiệu lực + nguồn, deadline, quy chuẩn cũ), `applied_rules[]` (applied / not_triggered / skipped_missing, chi tiết, evidence), `parameter_set[]`, `parameter_notes[]`, `limit_rules[]` (rule_code, giá trị, O₂, điều kiện, ngoại lệ, nhãn trích dẫn, `review_status`, `selected_by`), `missing_information[]`, `warnings[]`, `notes[]`, `evidence_path[]` (rule evidence + đường cấu trúc Document → Section → Table → TableRow), `disclaimer`. Mỗi kết quả lưu ở bảng `applicability_assessments` (id, context, result).

## Kiểm định

- `backend/tests/test_applicability.py`: 14 test hành vi (mặc định Cột B, nhóm cột theo lưu lượng, T-P theo nguồn tiếp nhận/loại hình, mặn–lợ bỏ Clorua, nước làm mát, loại trừ / gián tiếp, chuyển tiếp 3 mốc, khí thải lò hơi 20 t/h, nhiên liệu/công suất không khớp, thiết bị lạ, phương tiện, lộ trình UBND, tính xác định).
- `test_applicability_scenarios.py`: 17 kịch bản trong `applicability_scenarios_v1.yaml` — cũng là sheet 12 của hồ sơ kiểm chuyên gia (PLAN yêu cầu 20 kịch bản do chuyên gia soạn; đây là bản nháp để chuyên gia sửa/bổ sung).

## Giới hạn hiện tại

- Tập thông số theo loại hình dựa vào cột 4 Phụ lục 2 đã resolve; cột 5 ("theo thực tế nguyên liệu…") chỉ là ghi chú, không tự chọn ngưỡng.
- Khí thải: khớp theo `equipment_type_id` chọn từ danh sách 94 loại (nhãn cột 4 Bảng 1/2); chưa có cây thiết bị tổng quát ngoài các nhãn đã xuất hiện trong bảng.
- Không xét `as_of_date` với hiệu lực của QCVN cũ (ngoài corpus) — chỉ nêu tên.
