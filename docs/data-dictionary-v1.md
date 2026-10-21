# Data Dictionary v1 (2026-09-23)

Từ điển chuẩn nằm ở `data/dictionaries/*.yaml`, schema Pydantic ở `backend/app/ontology/schema.py`, kiểm tra bằng
`backend/tests/test_dictionaries.py` (load + integrity: id duy nhất, tham chiếu chéo hợp lệ, alias không đụng nhau).
Ba file lớn (`parameters`, `industry_sectors`, `equipment_types`) là **sinh tự động** từ hai file Markdown bằng
`backend/scripts/bootstrap_dictionaries.py`; muốn sửa thì sửa bảng curated trong script rồi chạy `--write`, không sửa tay.

| File | Bản ghi | Nguồn | Ghi chú |
|---|---:|---|---|
| `domains.yaml` | 2 | QCVN 40 Mục 1.1; QCVN 19 Mục 1.1 | `wastewater`, `emission` |
| `zones.yaml` | 6 | QCVN 40 Mục 1.3.3; QCVN 19 Mục 1.3.13; TT06 Đ2.4; TT45 Đ2.4 | Cột A/B/C **khác nghĩa theo miền**; Cột B là mặc định khi chưa phân vùng |
| `units.yaml` | 20 | Bảng ngưỡng hai QCVN, TT10 PL4.1/5/6 | `latex` là dạng gốc trong Markdown để parser khớp; `ngTEQ/Nm³` cho Dioxin/Furan khí thải (D70 2026-10-20: tệp nguồn ghi nhầm pg, đã sửa theo bản chính thức) |
| `receiving_body_types.yaml` | 4 | QCVN 40 Bảng 2 TT 4.1–4.3, TT 30 | `saline_brackish` là thuộc tính độ mặn, không loại trừ với lake/river/sea |
| `fuel_types.yaml` | 5 | QCVN 19 Mục 1.3.7–1.3.9, Bảng 1/2 | liquid, solid, biomass_solid, gas, biogas |
| `parameter_groups.yaml` | 16 | — | nhóm nghiệp vụ để lọc/hiển thị |
| `parameters.yaml` | **106** | QCVN 40 Bảng 1, 2; QCVN 19 Bảng 1, 2 | 40 chỉ nước thải, 41 chỉ khí thải, **25 dùng chung** (gộp theo công thức hóa học: As, Hg, Pb, Cd, Cr, Cu, Zn, Ni, Sb, F, Phenol, Dioxin/Furan, Benzene, Toluene, Xylene, Styrene, Chloroform, CCl₄, Dichloromethane, 1,2-dichloroethane, Trichloroethylene, Vinyl chloride, Acrylonitrile, Formaldehyde, 1,4-Dioxane) |
| `industry_sectors.yaml` | **54** | QCVN 40 Phụ lục 2 | `sector_id = S01..S54`, `vsic_codes[]`, nhóm ngành I–X, cột 4/5 giữ nguyên văn; `characteristic_parameter_ids` để trống cho `resolve_entities` (Phase 1) |
| `equipment_types.yaml` | **110** | QCVN 19 Bảng 1, 2 cột "Thiết bị xả khí thải" (366 dòng) | 16 dòng nhóm (không giá trị), 28 gắn nhiên liệu, 13 có điều kiện công suất; `occurrences[]` liệt kê mọi dòng bảng dùng nhãn đó |
| `aliases.yaml` | 68 | — | alias phía truy vấn: miền, cột, viết tắt thông số, nguồn tiếp nhận, nhiên liệu, đơn vị |

## Quy ước id
- `parameter_id`: curated (BOD5, COD, TOC, TSS, pH, T_N, T_P, NH4_N, Coliform, Color, PM, NOx…); công thức vô cơ ngắn (≤ 3 ký tự chữ-số: As, Hg, Cr6, Cl2, H2S…); còn lại là slug tên tiếng Anh gặp đầu tiên (Benzene, Chloroform…). Tên tiếng Việt của QCVN 19 luôn được giữ trong `aliases`.
- `unit_id`: `mg_L`, `mg_Nm3`, `degC`, `ng_TEQ_Nm3`, `m3_per_day`, `t_steam_per_h`, `kcal_per_h`, `MW`…
- `equipment_id`: slug ASCII của nhãn thiết bị đã bỏ số thứ tự; cùng nhãn ở nhiều block thông số → một bản ghi, nhiều `occurrences`.
- Mọi bản ghi có `sources[]` là citation label (`docs/citation-spec.md`).

## Việc còn lại (Phase 1)
- `resolve_entities`: điền `characteristic_parameter_ids` cho 54 loại hình từ cột 4 (tên → id qua alias), và `parameter_id` cho từng dòng bảng phương pháp (PL1 hai QCVN, PL4 TT10).
- Chuyên gia rà 41 thông số chỉ-khí-thải có tên Việt (Etylbenzen, Metylclorua, Cresol…) xem còn cặp trùng với nước thải không.
- Bổ sung `Method` dictionary (mã TCVN/SMEWW/US EPA/ISO/ES) khi parse Phụ lục 1.
