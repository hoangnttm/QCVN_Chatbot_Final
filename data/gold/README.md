# Gold set — bộ câu hỏi vàng cho đánh giá nghiên cứu

## Bản v3, đợt 1 (2026-10-06)

Kế hoạch: `docs/plans/2026-10-06-xay-lai-bo-cau-hoi.md` mục 0. Tệp làm việc là `gold_*_v3.jsonl` và `strata_v3.yaml`; tệp v2 giữ nguyên (hash ở `gold_v2_frozen.manifest.json`).

| Thay đổi | Nội dung |
|---|---|
| Tập | `dev` (chỉnh hệ thống), `val` (tập test cũ, đã lộ một lần, dùng để chọn cấu hình), `test` (mới, chỉ chạy ở G8) |
| Trường mới | `group` (T1, T2, B1–B6), `fact_ids`, `legal_address` (địa chỉ pháp lý của mọi căn cứ bắt buộc) |
| Soạn tay | người soạn điền Excel, không viết JSON |

```powershell
cd backend
python scripts/gold.py migrate-v3 [--force]            # v2 -> v3, test cũ -> val, đối chiếu lớp fact
python scripts/gold.py authoring-kit                   # -> data/gold/work/v3/soan-cau-hoi-dot-1.xlsx
python scripts/gold.py import-authoring <xlsx> --author "Tên" [--dry-run]
                                                       # nhận câu đạt vào gold_qa_v3.jsonl; câu cần sửa -> work/v3/tra-lai-<ngày>.md
python scripts/gold.py reserve-test-rows                # D53: dòng QCVN 40 rời val để dành cho test -> test_rows_v3.yaml (chỉ rút một lần)
python scripts/gold.py authoring-kit --templates-only  # -> data/gold/work/v3/mau-cau-dot-1.xlsx (15 mẫu câu mới)
python scripts/gold.py generate-test [--dry-run]       # T1 100 + T2 60 câu test từ mẫu câu mới
python scripts/gold.py validate --tier A,B,M --resolver db
```

`authoring-kit` sinh lại tệp trống; không chạy lại sau khi người soạn đã bắt đầu điền. `import-authoring` nhập lại được nhiều lần: câu trùng mã được thay bằng bản mới.

Đoạn trích nguyên văn được so với nguồn không phân biệt khoảng trắng, LaTeX, markdown và dấu câu (kể cả dấu «|» của bảng); người soạn được bỏ bớt chữ bằng «...» miễn các mảnh còn lại có thật và đúng thứ tự trong cùng một đoạn. Câu không tìm thấy được trả lại kèm dòng nguồn gần nhất. Tệp làm việc của 60 câu test đợt 1 là `work/v3/soan-cau-hoi-test-60-chuan-hoa.xlsx` (bản của PO dựng lại theo khuôn kit, xem `work/v3/ket-qua-nhap-test-60-2026-10-20.md`).

Phần dưới mô tả bản v2 (schema, nguyên tắc và các lệnh còn dùng chung).

Ba tầng dữ liệu (kế hoạch: `~/.claude/plans/streamed-popping-mountain.md`, PLAN.md §12):

| Tầng | Tệp | Cách tạo | Split | Judge |
|---|---|---|---|---|
| **A — tra bảng sinh theo mẫu** | `gold_tables_v2.jsonl` (393 câu) | `rawtables.py` đọc bảng Markdown thô **độc lập parser** + mẫu câu `templates_v2.yaml`, đối chiếu chéo với `limit_rules` (`crosscheck`), chia theo dòng 20/80 | dev 81 / test 312 | không (CS tự động) |
| **B — soạn tay phân tầng** | `gold_qa_v2.jsonl` (mục tiêu 144: dev 30 / test 114) | tác giả soạn từ tệp nguồn theo `strata_v2.yaml`, kiểm mù 25 % | theo tầng | có |
| **C — câu hỏi thật** | `gold_natural_v2.jsonl` (30–50, sau pilot) | `gold import-logs` từ nhật ký `/admin/logs` (ẩn danh) | test | có |
| **M — nhiều bước sinh theo mẫu** | `gold_multihop_v2.jsonl` (114 câu) | `gold generate-multihop` ghép một ô bảng (như tầng A) với hop viết tay trong `multihop_v2.yaml` (điều khoản khác, neo dòng nguồn thô); ≥ 2 chunk grade 2/câu; split theo dòng, kế thừa tầng A | dev 22 / test 92 | không (CS tự động) |

Bản v1 (`gold_qa_v1.jsonl`, 13 câu nháp) đã được chuyển sang v2 bằng `gold migrate-v1` và nằm trong **dev** (đã dùng để tinh chỉnh hệ thống nên không được vào test).

## Nguyên tắc

- **Chân lý neo vào nguồn**: mỗi câu có `source_ref` (`document_id`, sha256 tệp, số dòng trong `sources/*.md` hoặc trang PDF, bảng/dòng/cột); `verification.source_quote` phải nằm trong các dòng thô đó. `relevant[].chunk_id` chỉ là lớp ánh xạ (`gold map`), lệch là phát hiện của hệ thống, không sửa gold.
- **Không dùng LLM để sinh đáp án/nhãn/số**. LLM chỉ được diễn đạt lại ≤ 20 % câu hỏi, có `provenance` và người kiểm lại. Tầng A sinh theo mẫu (không LLM).
- Chỉ câu `status=reviewed` (chuyên gia) mới tính cho nghiệm thu sản phẩm; câu `draft` dùng cho nghiên cứu với tuyên bố hạn chế tương ứng.
- Test kín: `evaluate.py --split test` cần `--confirm-test` và ghi `test_runs.log`.

## Schema v2 (Pydantic `backend/app/evaluation/gold_schema.py`, `extra=forbid`)

| Trường | Ý nghĩa |
|---|---|
| `id` | `G-<WW|EM|XD|MET|DEF|SAM|EFF|TRN|FRQ|OOS|MIS|TRP|TA|TC>-nnn` |
| `tier`, `split`, `stratum`, `cluster_id` | tầng dữ liệu; dev/test; mã tầng lấy mẫu (`strata_v2.yaml`); cụm (dòng bảng) cho bootstrap |
| `question`, `domain`, `intent`, `as_of_date` | như v1 (`as_of_date` bắt buộc với effectivity/applicability) |
| `expected_answer` | đáp án ngắn nguyên văn từ nguồn (số như in, dấu phẩy thập phân) |
| `expected_numbers[]` | `{value` chuẩn hóa như `validator.numbers`, `display`, `unit`, `role` limit/range_min/range_max/o2_ref/date/factor/count/other, `must_appear}` |
| `forbidden_numbers[]` | ô kề (cột/dải khác) — phát hiện sai ô |
| `expected_conditions` | `zone`, `flow_band`, `receiving_body` (lake/river/sea/saline_brackish), `sector_id`/`sector_scope_text`, `equipment_path`, `fuel`, `capacity_text`, `o2_ref`, … |
| `target_rule_codes[]` | `limit_rules.rule_code` / `transition_rules.rule_id` / `TT10.MR.nn` (để đối chiếu và kiểm tính duy nhất) |
| `source_ref` | `{document_id, file_sha256, kind text|registry, line_start, line_end, pdf_page, printed_page, table_code, row_no, column_path, cell_raw}` |
| `relevant[]` | `{chunk_id, citation_label (copy byte-for-byte), grade 2 must-cite / 1 supporting / 0 acceptable, note, source_ref}` |
| `expected_citation_labels[]` | nhãn grade 2 (dạng rule `…, Cột B` khi nhắm rule) — tương thích `scripts/eval.py` cũ |
| `must_abstain`, `expected_behaviour` | `{mode answer|abstain|pointer|clarify, must_not[], pointer_labels[], clarification_topics[]}` |
| `expected_warnings[]` | draft_document, external_document, admin_declared_fact, missing_information, out_of_scope, cross_domain_citation |
| `trap_type`, `paired_with`, `tags[]`, `judge_notes` | bẫy; cặp đối chứng; nhãn tự do; ghi chú cho judge/người chấm |
| `difficulty`, `difficulty_factors[]` | điểm = số yếu tố + số yếu tố HARD; easy ≤ 2, medium 3–4, hard ≥ 5 (validator tính lại) |
| `source`, `provenance`, `verification`, `check_level`, `status`, `reviewer`, `revision`, `notes` | nguồn gốc câu; dấu vết LLM/mẫu; đối chiếu (người, ngày, `source_quote`, kiểm lần hai); authored/self_verified/double_checked/disputed; draft/reviewed/rejected |

## Công cụ (`backend/scripts/gold.py`)

```powershell
cd backend
python scripts/gold.py validate --tier all [--strict]        # mã lỗi E01–E23, cảnh báo W01–W20; exit 1 khi lỗi
python scripts/gold.py candidates --stratum EM-B1-EQUIP --exclude-used   # ứng viên + dòng nguồn thô + JSON khung -> data/gold/work/
python scripts/gold.py source --rule QCVN19_2024.B1.r2.1.2.1.B          # in các dòng thô quanh ô
python scripts/gold.py resolve --rule ... --json | --chunk ... | --label "..." | --mr TT10.MR.28 | --transition tr:...
python scripts/gold.py map --tier B                          # source_ref -> chunk_id, báo ánh xạ lệch
python scripts/gold.py generate                              # Tầng A (sinh lại deterministic theo seed)
python scripts/gold.py generate-multihop                     # Tầng M (kế hoạch 2026-09-29 §4.6); --tier all = A,B,C, dùng --tier A,B,M
python scripts/gold.py crosscheck                            # bộ đọc thô vs limit_rules -> parser_findings.jsonl
python scripts/gold.py sample-check --n 60                   # sheet kiểm tay Tầng A
python scripts/gold.py stats [--show-test]                   # phân bố + phủ -> docs/reports/gold-stats-<ngày>.md
python scripts/gold.py split [--dry-run]                     # gán dev/test theo hạn ngạch, twins cùng split
python scripts/gold.py export --out data/gold/review/gold-review.xlsx --for expert|second-check
python scripts/gold.py import <xlsx> --reviewer "Tên" --role expert|second
python scripts/gold.py agreement                             # đồng thuận A1–A2
python scripts/gold.py freeze --version 2.0.0                # validate --strict + manifest + tag
python scripts/gold.py import-logs --csv <export.csv>        # ứng viên Tầng C
```

## Quy trình soạn một câu Tầng B (không có chuyên gia)

1. Lấy ứng viên kế tiếp trong `data/gold/work/candidates-<tầng>.md` (đã có dòng thô, giá trị, điều kiện, `chunk_id`, JSON khung).
2. Đọc **tệp nguồn thô** tại `source_ref` (`gold source`), cả header/chú thích/dòng cha.
3. Viết `question` theo giọng người dùng (xoay alias; nêu đủ điều kiện; không chép nguyên văn ô; `as_of_date` khi phụ thuộc thời gian).
4. Viết `expected_answer` nguyên văn; điền `expected_numbers` (display như in), `forbidden_numbers` (ô kề), `expected_conditions`, `target_rule_codes`, `relevant` (grade 2 dòng đích; chú thích 2 nếu giá trị phụ thuộc, ngược lại 1; định nghĩa cột 0/1; registry fact 2 với ngày do quản trị viên khai báo (TT 45, TT 10 — file không in ngày); pointer M-01 2 với tần suất), `expected_citation_labels` = nhãn grade 2.
5. `verification` (`source_quote` nguyên văn ≤ 300 ký tự, `verified_by/at`), `difficulty_factors`, `tags`, `judge_notes`, `expected_behaviour`; `check_level=self_verified`.
6. `gold validate --item <id>` → 0 lỗi.
7. Cuối lô: `gold export --for second-check` → A2 điền → `gold import --role second` → `gold agreement`; bất đồng → cả hai đọc lại nguồn; không giải quyết → `check_level=disputed`.

Câu negative/bẫy: xem bảng `trap_type` trong kế hoạch (§2.4); injection phải do người viết.

## Tệp kèm

`strata_v2.yaml` (tầng, hạn ngạch, ràng buộc phủ), `templates_v2.yaml` (mẫu câu Tầng A), `parser_findings.jsonl` (lệch giữa bộ đọc thô và parser — hiện 0/1.125 ô), `gold_qa_v2.manifest.json` (sau freeze), `CHANGELOG.md`, `second_check_v2.jsonl`, `test_runs.log`, `work/` (gitignore).
