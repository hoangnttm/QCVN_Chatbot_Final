# Đặc tả JSON mỗi dòng bảng (`table_rows.normalized`) — v1

Quyết định PO #4 (2026-09-23): bảng được lưu **theo dòng**, mỗi dòng một JSON. Mỗi bản ghi `table_rows`
có `raw_cells` (nguyên văn, giữ LaTeX/`<br>`) và `normalized` theo schema dưới đây. Mỗi dòng logic ↔ đúng
một `EvidenceChunk`; `LimitRule` được sinh từ `values` × `zone`.

## Schema (JSON Schema draft 2020-12, rút gọn)

```json
{
  "$id": "qcvn.table_row.v1",
  "type": "object",
  "required": ["row_id", "table_code", "row_no", "row_path", "is_group_header", "cells", "span", "citation_label"],
  "properties": {
    "row_id":          { "type": "string", "description": "<doc>.<table>.r<row_no>[.<eq_path>][.a|.b]" },
    "table_code":      { "type": "string", "description": "QCVN40_2025.B1 | QCVN40_2025.B2 | QCVN40_2025.PL1 | QCVN40_2025.PL2 | QCVN19_2024.B1 | QCVN19_2024.B2 | QCVN19_2024.PL1 | QCVN19_2024.PL2 | QCVN19_2024.PL3 | TT10_2021.B<n>" },
    "row_no":          { "type": "string", "description": "TT như trong bảng (kể cả 4.1, 2.a)" },
    "row_path":        { "type": "array", "items": { "type": "string" }, "description": "đường dẫn số của dòng trong block thông số (QCVN 19) hoặc [row_no]" },
    "is_group_header": { "type": "boolean" },
    "inherits_from":   { "type": ["string", "null"], "description": "row_id của dòng cha khi ô TT/thông số/đơn vị được forward-fill" },
    "cells":           { "type": "object", "description": "ô đã chuẩn hóa theo tên cột logic của grammar (tt, parameter_raw, unit_raw, equipment_raw, …)" },
    "entities":        { "type": "object", "properties": { "parameter_id": {}, "unit_id": {}, "equipment_type_id": {}, "sector_id": {}, "method_ids": {} } },
    "values":          { "type": "object", "description": "khóa = zone hoặc <flow_regime>.<zone>; giá trị = Value" },
    "conditions":      { "type": "array", "items": { "$ref": "#/$defs/Condition" } },
    "exceptions":      { "type": "array", "items": { "type": "string" } },
    "methods":         { "type": "array", "items": { "$ref": "#/$defs/Method" } },
    "characteristic_parameters": { "type": "array", "items": { "type": "string" } },
    "conditional_parameters":    { "type": "array", "items": { "type": "object", "properties": { "condition_text": {}, "parameter_ids": {} } } },
    "vsic_codes":      { "type": "array", "items": { "type": "string" } },
    "coefficient":     { "type": "object", "properties": { "name": {}, "tef": { "type": "number" } } },
    "footnote_refs":   { "type": "array", "items": { "type": "string" } },
    "span":            { "$ref": "#/$defs/Span" },
    "citation_label":  { "type": "string" },
    "parse_warnings":  { "type": "array", "items": { "type": "string" } }
  },
  "$defs": {
    "Value": {
      "type": "object",
      "required": ["op"],
      "properties": {
        "op":     { "enum": ["<=", "<", ">=", ">", "=", "between"] },
        "value":  { "type": "number" },
        "min":    { "type": "number" },
        "max":    { "type": "number" },
        "unit_id": { "type": "string" },
        "ref_o2": { "type": ["number", "null"], "description": "% ôxy tham chiếu (QCVN 19)" },
        "raw":    { "type": "string", "description": "ô gốc, ví dụ '$\\\\le 200(4)$'" }
      }
    },
    "Condition": {
      "type": "object",
      "required": ["type"],
      "properties": {
        "type": { "enum": ["flow_regime", "equipment_path", "fuel_type", "capacity", "receiving_body", "sector_scope", "co_processing", "zone", "other"] },
        "op": {}, "value": {}, "unit": {}, "alt": {}, "text": { "type": "string" }
      }
    },
    "Method": { "type": "object", "required": ["code", "order"], "properties": { "code": {}, "order": { "type": "integer" }, "stage": { "enum": ["field", "lab", "sampling"] } } },
    "Span": { "type": "object", "required": ["file_hash", "line_start", "line_end"], "properties": { "file_hash": {}, "line_start": {}, "line_end": {}, "char_start": {}, "char_end": {}, "pdf_page_start": {}, "pdf_page_end": {}, "printed_page": {} } }
  }
}
```

## Quy ước theo bảng

| Bảng | `values` | Ghi chú |
|---|---|---|
| QCVN40.B1 | khóa kép `"F<=2000.A"` … `"F>2000.C"` | ô COD/TOC (`<br>`) → 2 dòng logic `2.a` (COD), `2.b` (TOC), cùng `row_no` gốc trong `cells.tt` |
| QCVN40.B2 | `A`, `B`, `C` | pH → `{op:"between",min:6,max:9}`; dòng 4 (T-P) `is_group_header=true`; 4.1–4.3 `inherits_from` dòng 4, `conditions[]` từ nhãn (receiving_body, sector_scope); dòng 30 `exceptions:["xả thải vào nguồn nước mặn, nước lợ"]` |
| QCVN40.PL1 | — | `methods[]` có `order` (phương pháp trọng tài = order 1), `stage` = `sampling` cho TT 1–2, `lab` còn lại |
| QCVN40.PL2 | — | `vsic_codes[]`, `characteristic_parameters[]` (id sau resolve), `conditional_parameters[]` từ cột 5 dạng `- <điều kiện>: <thông số>` |
| QCVN19.B1/B2 | `A`, `B`, `C` với `ref_o2` | `row_path` từ tiền tố số ở cột thiết bị; dòng nhóm (không giá trị) `is_group_header=true`; `conditions[]`: equipment_path, fuel_type, capacity (kèm `alt` cho nhiệt lượng Kcal/giờ), co_processing; `exceptions[]` từ "(trừ …)" |
| QCVN19.PL1 | — | `methods[]` với `stage` = `field` (cột 3) / `lab` (cột 4) |
| QCVN19.PL2/PL3 | — | `coefficient{name, tef}`; công thức TEQ/PAH lưu ở node `Formula` của phụ lục |
| TT10 (Bảng 25, 26, …) | — | chỉ `cells` + `entities` + `methods` (ngăn bằng `;`); không sinh `LimitRule`; ô "Lưu lượng" có quy trình + công thức → `cells.procedure_text`, `Formula` riêng |

## Kiểm tra bắt buộc (Phase 1)
- Số dòng logic mỗi bảng khớp snapshot (QCVN19.B1: 215 dòng giá trị / 54 thông số; QCVN19.B2: 89 / 12; QCVN40.B2: 64 dòng / 61 thông số; QCVN40.PL2: 54).
- Mọi ô giá trị không parse được → `parse_warnings` khác rỗng → `review_task`; **0** ô bị bỏ qua im lặng.
- Chuyên gia kiểm 30 dòng/bảng ngưỡng đối chiếu nguồn, sai số cho phép = 0.
