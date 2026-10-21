# Online retrieval — kỹ thuật và tham số ở từng bước

Bản chi tiết cho người đọc kỹ thuật, đi theo đúng mười bước của bản dễ hiểu (`docs/online-retrieval-de-hieu.md`). Mọi tham số dưới đây lấy từ mã và cấu hình đang chạy ngày 2026-10-05 (nhánh `feature/graph-neo4j-gds`). Corpus: 5 văn bản, 1.259 evidence chunk (`context_text` trung bình 578 ký tự, dài nhất 4.897).

Mã: `backend/app/retrieval/` (`query.py`, `tokenize.py`, `structured.py`, `lexical.py`, `vector.py`, `embedder.py`, `retriever.py`, `expand.py`), `backend/app/qa/` (`service.py`, `prompts.py`, `validator.py`, `graph_verify.py`), `backend/app/llm/client.py`.

## Tổng quan luồng

```text
câu hỏi ─► 1 hiểu câu hỏi ─┬─► 2 structured lookup (SQL, ≤ 8 dòng) ──── +1,0 ─┐
                            ├─► 3 BM25 (top 50) ────────── 1/(60+rank) ──────┼─► 5 RRF × type boost
                            └─► 4 vector (top 50) ──────── 1/(60+rank) ──────┘     lọc hiệu lực, top k
   ─► 6 graph expansion (≤ k/2) ─► 7 evidence pack (≤ 18.000 ký tự)
   ─► 8 policy hoặc LLM (JSON) ─► 9 validator (sinh lại 1 lần) ─► 10 SSE + audit log
```

## Bước 1. Hiểu câu hỏi (`query.understand`)

Không dùng LLM ở đường chính; LLM chỉ được gọi khi luật không chắc.

**Chuẩn hóa.** Unicode NFC, chữ thường, `–`/`—` → `-`, `³` → `3`, `²` → `2`, gộp khoảng trắng.

**Nhận diện thực thể (`AliasIndex`).** Từ điển alias gồm khoảng 70 mẫu khai báo tay trong `aliases.yaml` cộng tên, ký hiệu, công thức, alias của mọi thông số, đơn vị, nhiên liệu, nguồn tiếp nhận, loại hình và mã văn bản (kèm các dạng rút gọn như "qcvn 40", "thông tư 06/2025"). Khớp bằng regex có biên từ `(?<!\w)mẫu(?!\w)`, **dài nhất trước**, và che đoạn đã khớp để "bod5" không bị "bod" khớp lại.

**Trích bằng regex.**

| Thành phần | Mẫu |
|---|---|
| Lưu lượng | `(\d[\d.,]*)\s*m3\s*/\s*ngày` |
| Công suất | số + `tấn hơi/giờ`, `tấn/giờ`, `MW`, `kcal/giờ` |
| Cột | `(cột\|loại\|mức)\s*([abc])` và alias vùng ("vùng hạn chế phát thải") |
| Phủ định | sau `không thuộc`, `không phải (là)`, `không nằm trong`, đến hết câu hoặc tới "mà" / "nhưng" |

Quy tắc chọn cột: lấy cột đầu tiên không nằm trong phạm vi phủ định; nếu mọi cột được nhắc đều bị phủ định và có đúng hai cột bị loại thì chọn cột còn lại ("không thuộc Mức A, Mức B" → C); chỉ một cột bị loại thì để trống.

**Miền.** Đếm từ khóa: 18 từ khóa nước thải, 19 từ khóa khí thải, 12 từ khóa ngoài phạm vi; cộng điểm từ văn bản được nêu, miền của alias vùng, và đơn vị (mg/L → nước thải, mg/Nm³ → khí thải). Quyết định: chỉ có từ khóa ngoài phạm vi → `out_of_scope`; cả hai miền → `cross_domain`; một miền → miền đó; không có gì → `unknown`.

**Ý định.** Danh sách từ khóa theo thứ tự ưu tiên, khớp đầu tiên thắng: frequency → effectivity → method → sampling → definition → applicability → threshold → scope. Không khớp nhưng có thông số → threshold. Ý định frequency đặt `policy = M-01`.

**Khi nào gọi LLM.** `needs_llm` = miền `unknown` / `cross_domain` hoặc ý định `other`. Khi đó gọi `gemini-3.5-flash-lite` với structured output (`LLMUnderstanding`). Kết quả chỉ được dùng để điền chỗ luật để trống; nếu miền thay đổi thì chạy lại retrieval với miền mới.

**Mở rộng truy vấn.** Mỗi thực thể sinh một canonical token (`param_bod5`, `zone_b`, `fuel_solid`, `doc_qcvn_40_2025`); ý định threshold thêm `ctype_table_row`. Các token này cũng được gắn vào phía tài liệu ở bước 3.

## Bước 2. Structured lookup (`structured.lookup`)

Chạy khi ý định ∈ {threshold, applicability, other} **và** câu hỏi có thông số.

1. SQL trên `limit_rules`: `table_code` thuộc các bảng ngưỡng của miền (QCVN 40 Bảng 1–2, QCVN 19 Bảng 1–2), `parameter_id` ∈ thông số của câu hỏi, `zone` = cột nếu có.
2. Lọc dải lưu lượng: `F ≤ 2000` hoặc `F > 2000` theo số trong câu hỏi; rule không có điều kiện lưu lượng được giữ.
3. Lọc nguồn tiếp nhận theo từ khóa (hồ, sông, biển, mặn / lợ); nếu lọc xong rỗng thì bỏ bộ lọc này.
4. Khí thải: lọc nhiên liệu và công suất bằng `applicability.matching.filter_rules`.
5. Xếp theo độ đặc hiệu: +2 nếu điều kiện nhiên liệu khớp, +2 nếu đơn vị công suất khớp, +1 nếu nhãn thiết bị lặp từ khóa câu hỏi ("hơi", "phát điện"), −1 với dòng "khác"; hòa thì theo vị trí trong văn bản.
6. Giữ tối đa **8** dòng; nhiều hơn thì cắt và ghi cảnh báo.

Mỗi dòng trả về giá trị, toán tử, đơn vị, O₂ tham chiếu, điều kiện, `review_status` và `chunk_id` của dòng bảng tương ứng.

## Bước 3. Tìm theo từ khóa — BM25 (`lexical.BM25Index`)

| Hạng mục | Giá trị |
|---|---|
| Thư viện | `rank_bm25` 0.2.2, lớp `BM25Okapi` (biến thể ATIRE) |
| `k1` | 1,5 (mặc định thư viện; độ bão hòa tần suất từ) |
| `b` | 0,75 (mặc định; mức chuẩn hóa theo độ dài) |
| `epsilon` | 0,25 (IDF âm được thay bằng 0,25 × IDF trung bình) |
| Tách từ | `pyvi` 0.1.1: giữ từ ghép (`nước_thải`) **và** thêm từng âm tiết dài hơn 1 ký tự |
| Token tài liệu | token của `context_text` + canonical token: thông số, đơn vị, thông số đặc trưng, loại hình, `doc_<id>`, `ctype_<loại chunk>` |
| Token truy vấn | token của câu hỏi + canonical token ở bước 1 |
| Số ứng viên | **50** (`candidates`, đổi được 10–200) |
| Lọc | bỏ điểm ≤ 0; miền ∈ {miền câu hỏi, `both`}; `cross_domain` / `unknown` không lọc |
| Nơi chạy | trong tiến trình backend; cache `data/derived/index/bm25-<chữ ký>.pkl`, chữ ký = SHA-1 của mọi cặp (`chunk_id`, MD5 `context_text`) nên tự dựng lại sau khi nạp lại văn bản |

`k1`, `b`, `epsilon` chưa từng được tinh chỉnh; đây là giá trị mặc định.

## Bước 4. Tìm theo nghĩa — vector (`vector.VectorIndex`)

| Hạng mục | Giá trị |
|---|---|
| Mô hình | `gemini-embedding-001`, `output_dimensionality = 1536` (cắt MRL) |
| Task type | `RETRIEVAL_QUERY` cho câu hỏi, `RETRIEVAL_DOCUMENT` cho chunk |
| Hậu xử lý | chuẩn hóa L2; văn bản dài hơn 6.000 ký tự bị cắt (có đếm) |
| Thử lại | tối đa 6 lần với HTTP 429 / 5xx, chờ 5 s nhân đôi đến 60 s |
| Kho | pgvector 0.8.6, cột `vector(1536)`, khoảng cách cosine (`<=>`), điểm = 1 − khoảng cách |
| Index | HNSW `m = 16`, `ef_construction = 64`, `ef_search = 40` (mặc định) |
| Số ứng viên | **50**, cùng bộ lọc miền với BM25 |
| Khi lỗi | bỏ nhánh vector, thêm cảnh báo, tiếp tục bằng BM25 và structured |

Lưu ý: với 1.259 dòng, PostgreSQL hiện chọn **quét tuần tự** thay vì dùng index HNSW (đã kiểm bằng `EXPLAIN`), nên kết quả là láng giềng chính xác; các tham số HNSW chỉ có tác dụng khi corpus lớn hơn. Truy vấn mất 5–8 ms; gần như toàn bộ thời gian bước này (khoảng 400 ms) là lời gọi API embedding câu hỏi.

Tùy chọn `VECTOR_BACKEND=neo4j`: cùng embedding, lưu trên node `EvidenceChunk`, index HNSW cosine của Neo4j. Trên dev cho top-10 trùng 114/116 câu; mặc định vẫn là pgvector.

## Bước 5. Gộp và xếp hạng (`retriever.search`)

**Reciprocal Rank Fusion (RRF).** Chỉ dùng thứ hạng, không dùng điểm thô, vì BM25 và cosine khác thang:

```text
score(c)  = Σ_{s ∈ {bm25, vector}} 1 / (60 + rank_s(c))     # RRF_K = 60
          + 1,0                                              # nếu c là dòng structured lookup
ranked(c) = score(c) × TYPE_BOOST[ý định][loại chunk]        # mặc định 1,0
```

- Hạng 1 = 1/61 ≈ 0,0164; hạng 50 ≈ 0,0091; đứng đầu cả hai danh sách = 0,0328.
- Thưởng +1,0 lớn hơn mọi tổng RRF, nên dòng structured luôn đứng trên và giữ thứ tự đặc hiệu của bước 2.

**Type boost theo ý định.**

| Ý định | Hệ số |
|---|---|
| threshold | table_row 1,3 · footnote 1,1 |
| method | table_row 1,2 · clause 1,1 |
| effectivity | registry_fact 1,4 · clause 1,2 · preamble 1,2 · point 1,1 |
| applicability | clause 1,2 · point 1,2 |
| definition | definition 1,5 |
| sampling | clause 1,2 · paragraph 1,1 |
| frequency | clause 1,2 |

**Lọc hiệu lực.** Khi có `as_of_date`, chunk của văn bản có `effective_date` (registry fact không kèm phạm vi) sau ngày đó bị loại, trừ khi ý định là effectivity hoặc applicability.

**Cắt top k.** `k` = 10 cho hỏi đáp (cho phép 3–20), 8 cho `/search`.

Ví dụ "Giới hạn BOD5 cột B, lưu lượng 3000 m³/ngày":

| Chunk | BM25 | Vector | Structured | score | × boost |
|---|---|---|---|---|---|
| Bảng 1, TT 1 (BOD5) | hạng 2 → 0,0161 | hạng 1 → 0,0164 | +1,0 | 1,0325 | 1,342 |
| Mục 2.1 (đoạn dẫn Bảng 1) | hạng 1 → 0,0164 | hạng 4 → 0,0156 | | 0,0320 | 0,032 |
| Chú thích Bảng 1 | hạng 12 → 0,0139 | | | 0,0139 | 0,0153 |

## Bước 6. Bổ sung căn cứ liên quan (`expand.expand`)

Thêm tối đa `max(2, k // 2)` chunk (5 chunk khi k = 10), nối **sau** top k, không có điểm. Đọc từ PostgreSQL, theo thứ tự ưu tiên:

1. `policy = M-01` → hai chunk TT 10/2021 Điều 16 khoản 3 và Điều 17 khoản 5 (theo `chunk_id` cố định).
2. Đã biết cột và ý định ∈ {threshold, applicability, scope} → chunk định nghĩa "Cột X:" của QCVN theo miền.
3. Với 5 chunk đầu: dòng bảng → các chú thích trong `footnote_refs`; khoản / điểm / đoạn → node cha.
4. Chunk `definition` có thuật ngữ (phần trước dấu ":", dài hơn 3 ký tự) xuất hiện trong câu hỏi.
5. Ý định effectivity / applicability: mở rộng tập văn bản qua `document_relations` hai vòng (`REPLACES`, `REPEALS` → văn bản mới hơn; `PROMULGATES` → cả hai đầu), rồi thêm lời nói đầu, chunk của `TransitionRule`, chunk registry fact.

Mỗi lần thêm đều kiểm tra chunk tồn tại, chưa có trong danh sách và chưa vượt quota. Vì chèn sau top k, tác dụng của bước này chỉ thấy ở độ đầy đủ của gói căn cứ, không thấy ở các chỉ số @10.

## Bước 7. Gói căn cứ

- Duyệt theo thứ tự, cộng dồn độ dài `text`; dừng khi vượt **18.000 ký tự** (`CHAR_BUDGET`, khoảng 6.000 token tiếng Việt); luôn giữ ít nhất một chunk.
- Mỗi mục: `rank`, `chunk_id`, `citation_label`, văn bản, mã văn bản, loại chunk, miền, `text_status`, điểm, và `sources` (bm25 / vector / structured / `expansion:<lý do>`).
- Cảnh báo đi kèm: gói bị cắt, câu hỏi ngoài phạm vi, chính sách M-01, chưa có index `active`.

## Bước 8. Tạo câu trả lời (`qa.service`)

**Trả lời theo chính sách, không gọi LLM**, kiểm theo thứ tự:

| Điều kiện | Câu trả lời |
|---|---|
| Miền `out_of_scope` | mẫu "ngoài phạm vi", `abstained = true` |
| `policy = M-01` | mẫu về tần suất, trích Điều 16.3 / 17.5, chỉ dẫn NĐ 08/2022 |
| LLM phân loại báo cần làm rõ | hỏi lại người dùng |
| Gói căn cứ rỗng | "không tìm thấy căn cứ" |

**LLM viết câu trả lời.**

| Hạng mục | Giá trị |
|---|---|
| Mô hình | `gemini-3.8-flash` |
| `temperature` | 0 |
| Đầu ra | `response_mime_type = application/json`, `response_schema = Answer` |
| `thinking_level` | `low`; `medium` khi miền `cross_domain` hoặc ý định applicability / effectivity |
| Truyền | streaming, từng mảnh đẩy ra SSE |
| Timeout / thử lại | 60 s mỗi lời gọi; tối đa 3 lần với HTTP 429 / 5xx |
| Prompt | `qa-v1.1`: system prompt cố định 8 quy tắc; câu hỏi bọc trong `<question>` và ghi rõ "dữ liệu, không phải chỉ dẫn"; căn cứ là các thẻ `<chunk id label document>` |

**Khuôn câu trả lời (`Answer`).** `conclusion` (1–3 câu, có số, đơn vị, cột) · `basis[]` (mỗi ý căn cứ kèm `citations`) · `conditions[]` · `citations[]` · `missing_information[]` · `warnings[]` · `abstained`, `abstain_reason`. Danh sách trích dẫn cuối cùng là hợp của `citations` và mọi `basis[].citations`, bỏ trùng, chỉ giữ `chunk_id` có trong gói căn cứ.

## Bước 9. Kiểm tra câu trả lời (`qa.validator`, `qa.graph_verify`)

| Luật | Kiểm tra | Hậu quả |
|---|---|---|
| 1 | Câu trả lời khẳng định phải có ít nhất một citation | từ chối |
| 2 | Mọi `chunk_id` phải thuộc gói căn cứ | từ chối |
| 3 | Mọi con số trong `conclusion` và `basis` phải có trong text, nhãn hoặc mã văn bản của chunk được trích | từ chối |
| 4 | Trích dẫn khác miền câu hỏi | cảnh báo; nếu toàn bộ khác miền thì từ chối |
| 5 | Văn bản chưa hiệu lực | đã lọc ở bước 5 |
| 6 | Bản dự thảo phải có cảnh báo; ngày do quản trị viên khai báo phải trích chunk Sổ đăng ký | tự thêm cảnh báo; từ chối nếu trích nhầm nguồn ngày |
| 7 | Sinh lại | xem dưới |
| 8 | Con số phải là giá trị của `LimitRule` khớp điều kiện câu hỏi (đồ thị) | chế độ `observe`: chỉ ghi nhận |

**Chuẩn hóa số (luật 3).** Regex `\d+(?:[.,]\d+)*`; `3.000` → 3000, `0,05` → 0.05, `1.234,5` → 1234.5, `6,0` → 6. Bỏ qua các số 1, 2, 3 và mọi số lặp lại từ câu hỏi hoặc từ `as_of_date`.

**Sinh lại (luật 7).** Bị từ chối lần đầu → gọi LLM lần hai, kèm danh sách lỗi trong prompt. Vẫn bị từ chối → trả mẫu "không tạo được câu trả lời có trích dẫn hợp lệ", giữ gói căn cứ cho người dùng xem, ghi `status = failed`.

**Luật 8 (Neo4j).**

- Với chunk dòng bảng: lấy mọi `LimitRule` của dòng đó, giữ các rule không mâu thuẫn với cột, thông số, dải lưu lượng, nhiên liệu, công suất, nguồn tiếp nhận của câu hỏi. Điều kiện mà câu hỏi không nêu thì không dùng để loại.
- `contradicted` khi câu trả lời nêu số thuộc rule bị loại mà không nêu số của rule được giữ, hoặc không rule nào khớp. Số O₂ tham chiếu không tính, vì giống nhau ở mọi cột.
- Với chunk khác: tìm đường ngắn nhất ≤ 4 bước từ thực thể của câu hỏi tới chunk trên các cạnh `PATH_RELS`; không có đường → `weak` (chỉ cảnh báo).
- Chế độ hiện tại `GRAPH_VERIFY_MODE=observe`; Neo4j không sẵn sàng thì luật 8 bị bỏ qua, không làm hỏng câu trả lời.

## Bước 10. Trả kết quả và lưu vết

- **SSE:** các sự kiện `stage` (understanding, generating, regenerating, validating), `understanding`, `evidence`, `delta` (văn bản đang sinh), `answer`.
- **Trạng thái:** `answered`, `abstained`, `failed`; đường xử lý `policy`, `llm`, `fallback`.
- **Audit (`query_logs`, `answers`, `answer_citations`):** câu hỏi, kết quả hiểu câu hỏi, chunk đã lấy kèm nguồn và thứ hạng, structured hits, mô hình, `thinking_level`, phiên bản prompt, số lần sinh, token và chi phí ước tính, độ trễ từng bước, kết quả validator kể cả đường căn cứ của luật 8.

## Thành phần đã thử nghiệm, đang tắt ở đường chính

| Thành phần | Cấu hình | Kết quả đã đo | Trạng thái |
|---|---|---|---|
| LLM rerank | `gemini-3.5-flash-lite` chấm top 30, 700 ký tự mỗi ứng viên, timeout 10 s | dev (28/9): nDCG@10 0,82 → 0,96, nhưng p95 khoảng 4,5 s | tắt, chờ quyết định về độ trễ |
| Personalized PageRank (Neo4j GDS) | damping 0,85, 20 vòng, hạt giống = thực thể + 10 chunk đầu | dev: không điểm nào trong lưới 14 cấu hình đạt tiêu chí chọn; 50–70 ms | tắt |
| Tập ứng viên theo quan hệ (`graph_candidates`) | lõi 2 bước + ngữ cảnh bảng + điều khoản theo vai trò | test (404 câu): phủ 99,9 % căn cứ trong khoảng 11 % corpus; recall khi lọc 0,900 so với 0,895 | chỉ dùng trong đánh giá |
| Vector trên Neo4j | cùng embedding | dev: tương đương pgvector | tùy chọn, mặc định tắt |

## Số đo hiện có

Tập dev, 114 câu có căn cứ bắt buộc (tầng A 81, B 11, M 22), xếp hạng trước bước 6, ngày 2026-10-05:

| Cấu hình | hit@10 | recall_must@10 | nDCG@10 (tầng A) |
|---|---|---|---|
| C1: chỉ vector | 0,974 | 0,885 | 0,850 |
| C3: BM25 + vector + structured + type boost | 0,939 | 0,871 | 0,793 |

Trên các chỉ số xếp hạng này, chỉ vector không kém cấu hình kết hợp. Structured lookup không làm thứ hạng tốt hơn; tác dụng thực tế của nó là ghim dòng bảng khớp điều kiện lên đầu gói căn cứ và trả giá trị đã phân tích cho giao diện và audit log. Giá trị đó **không** được đưa vào prompt: LLM vẫn tự đọc con số từ text của dòng bảng (chứa mọi cột). Tỉ lệ định tuyến đúng miền và ý định (`routing_strict`) là 0,862.

Luật 8 trên bộ nhiễu dev: bắt sai cột 90 % (luật 1–7: 1 %), chặn nhầm câu đúng 0 %, p50 khoảng 2 ms.

## Giới hạn đã biết

- `k1`, `b`, `RRF_K`, `TYPE_BOOST` và mức thưởng +1,0 là giá trị chọn tay hoặc mặc định, chưa tinh chỉnh trên dữ liệu.
- Structured lookup có danh sách bảng và từ khóa viết cứng trong mã; với QCVN 19 thường có nhiều hơn 8 dòng khớp.
- Bước 1 là nguồn lỗi chính đã quan sát; ví dụ "lưu huỳnh điôxit" chưa được nhận là SO2.
- Tập test chưa có câu văn xuôi viết tay (tầng B), nên số đo trên test chỉ nói về câu tra bảng và câu nhiều bước.
- Chưa có số đo độ trễ đầu-cuối cập nhật cho cấu hình hiện tại.
