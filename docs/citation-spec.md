# Đặc tả citation label (v1)

Mỗi `EvidenceChunk` có đúng một `citation_label`, sinh xác định từ đường dẫn cấu trúc (không từ LLM).
Label là thứ người dùng nhìn thấy; API luôn trả kèm `chunk_id`, `span` và `structure_path`.

## Cú pháp

```
<DOC_LABEL>[ (dự thảo)], <PATH>[, <TABLE>[, TT <ROW>[, Thiết bị <EQ_PATH>][, Cột <ZONE>]]][, Chú thích][, tr. <PAGE>]
```

| Thành phần | Quy tắc | Ví dụ |
|---|---|---|
| `DOC_LABEL` | Mã văn bản đầy đủ theo registry: `QCVN 40:2025/BTNMT`, `Thông tư 06/2025/TT-BTNMT` | |
| `(dự thảo)` | Bắt buộc khi `text_status=draft`; `(chưa đối chiếu)` khi `unverified`. Từ 2026-09-24 cả 5 văn bản `official` nên không nhãn nào mang hậu tố | `Thông tư 45/2024/TT-BTNMT (dự thảo)` (ví dụ định dạng) |
| `PATH` | QCVN: `Mục N[.N[.N[.N]]]` cho Section/Clause/Point/Definition; Thông tư: `Điều N[, Khoản N[, điểm x]]`; TT10: có thêm `Chương X`, `Phụ lục N[.N]` | `Mục 4.3.2`; `Điều 3, Khoản 1`; `Phụ lục 4.1` |
| `TABLE` | `Bảng N` (số bảng như trong văn bản; TT10 đánh số liên tục xuyên phụ lục) | `Bảng 2` |
| `TT <ROW>` | Số thứ tự dòng như trong bảng; dòng logic tách từ `<br>` thêm hậu tố `.a/.b`; QCVN 19 kèm tên ngắn thông số trong ngoặc | `TT 4.2`; `TT 2.a`; `TT 2 (CO)` |
| `Thiết bị <EQ_PATH>` | Chỉ QCVN 19: đường dẫn số của thiết bị trong block thông số | `Thiết bị 1.2.1` |
| `Cột <ZONE>` | `A`/`B`/`C`; QCVN 40 Bảng 1 thêm tiền tố lưu lượng: `F ≤ 2000, Cột B` | `Cột B` |
| `Chú thích` | Footnote gắn bảng/mục | `Bảng 1, Chú thích` |
| `tr. <PAGE>` | Chỉ PDF: trang PDF; trang in phụ lục TT10 dùng `PL-N` | `tr. 12`; `tr. PL-53` |
| Registry fact | `Sổ đăng ký văn bản: <DOC_LABEL> — <fact> <value> (do quản trị viên khai báo ngày <declared_at>)` | `Sổ đăng ký văn bản: Thông tư 45/2024/TT-BTNMT — ngày hiệu lực 01/7/2025 (do quản trị viên khai báo ngày 2026-09-23)` |

## Ví dụ chuẩn
- `QCVN 40:2025/BTNMT, Mục 1.1.2`
- `QCVN 40:2025/BTNMT, Mục 2.1, Bảng 1, TT 1, F > 2000, Cột B`
- `QCVN 40:2025/BTNMT, Mục 2.2, Bảng 2, TT 4.2, Cột A`
- `QCVN 40:2025/BTNMT, Phụ lục 2, TT 13`
- `QCVN 19:2024/BTNMT, Mục 2.1, Bảng 1, TT 2 (CO), Thiết bị 1.2.1, Cột B`
- `QCVN 19:2024/BTNMT, Bảng 1, Chú thích`
- `Thông tư 06/2025/TT-BTNMT, Điều 3, Khoản 1`
- `Thông tư 45/2024/TT-BTNMT, Điều 2, Khoản 4, tr. 2`
- `Thông tư 10/2021/TT-BTNMT, Điều 17, Khoản 4, tr. 12`
- `Thông tư 10/2021/TT-BTNMT, Phụ lục 4.1, Bảng 26, TT 5, tr. PL-53`
- `Sổ đăng ký văn bản: 45/2024/TT-BTNMT — ngày hiệu lực 2025-07-01 (do quản trị viên khai báo ngày 2026-09-23)` (registry fact)

## Ràng buộc
1. Label là duy nhất trong một `index_version` (test snapshot).
2. Đổi label = đổi `chunk_id`? **Không** — `chunk_id` ổn định theo `(document_version, structure_path, row_no)`; label chỉ là cách hiển thị.
3. Validator so khớp `chunk_id`, không so khớp chuỗi label.
4. UI hiển thị label, click mở panel: raw text, `context_text`, structure path, span (dòng/trang), graph path, cảnh báo dự thảo.
