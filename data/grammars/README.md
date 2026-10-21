# Grammar cấu trúc per-document

Mỗi file mô tả cách parser xác định (deterministic) tách một văn bản thành cây `structure_nodes`
(PLAN.md §1.2, §5 bước 4–5). Parser đọc grammar, **không hard-code** pattern theo văn bản.

Khóa chung:

| Khóa | Ý nghĩa |
|---|---|
| `document_id` | Trỏ về `data/registry/documents.yaml` |
| `source_format` | `md` (đọc trực tiếp) hoặc `pdf` (qua bước `pdf_extract` → Markdown) |
| `preprocess` | Danh sách bước chuẩn hóa chạy trước parse (`strip_noise_lines`, `normalize_legacy_text`, `latex_to_text`) |
| `structure[]` | Các node kind theo thứ tự ưu tiên; `match` là regex Python (multiline, từng dòng), nhóm đặt tên (`number`, `title`, `text`, `label`, `term`) được đưa vào thuộc tính node |
| `structure[].block` | `line` (một dòng) / `until_blank` (đến dòng trống) / `table` (các dòng `|…|` liền sau) / `until_next_heading` |
| `structure[].parent_kinds` | Kind nào được phép làm cha (để dựng cây theo ngăn xếp) |
| `boilerplate[]` | Regex dòng bắt đầu vùng bỏ qua (nơi nhận, chữ ký) |
| `tables{}` | Gợi ý per-table cho `parse_tables`: cột logic, số tầng header, cột cần forward-fill, cột có phân cấp thiết bị, grammar giá trị, tách `<br>` |
| `citation` | Mẫu sinh `citation_label` (PLAN.md Phụ lục B) |

Quy tắc kiểm tra: 100 % dòng không rỗng thuộc đúng một node; số thứ tự liên tục; PDF có `pdf_page`.
