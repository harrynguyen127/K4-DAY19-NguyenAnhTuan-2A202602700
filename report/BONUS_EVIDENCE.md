# Bằng chứng bonus — ontology tự thiết kế

## Cách thực hiện

Baseline nằm trong [baselines/hint](../baselines/hint/README.md), dùng các hàm HINT tại commit `859e8d1` và điền KG-3 theo Bước 5 của LAB_GUIDE. Hai bản dùng chung KG-1, KG-4, parser luật, embedding, metering và provider. File `bench_kg.py` và bộ test gốc không bị sửa.

Chạy `python -u -X utf8 scripts/bonus_compare.py` trên Neo4j riêng tại cổng 17687 tạo [ket_qua_benchmark_kg.hint.txt](../ket_qua_benchmark_kg.hint.txt), [bonus_check.txt](bonus_check.txt) và [bonus_evidence.json](bonus_evidence.json). Graph bài chính tại cổng 7687 chỉ được đọc khi lấy bằng chứng; các fixture và lệnh reset chạy trên database riêng.

Hai benchmark dùng OpenAI `gpt-4o-mini`, embedding `text-embedding-3-small`, cùng 18 văn bản luật, 20 bài báo, 176 chunks, chunk_size=800 và top_k=3. Bản tự thiết kế lấy số liệu từ [ket_qua_benchmark_kg.txt](../ket_qua_benchmark_kg.txt). Đây là hai lần chạy riêng; trích xuất, sinh câu trả lời, judge và độ trễ có biến động. Chênh lệch benchmark phản ánh toàn bộ pipeline, gồm prompt và cách chọn ngữ cảnh, chưa tách được tác động nhân quả của riêng một thay đổi ontology.

## 1. Benchmark trước/sau

Chỉ số GraphRAG trong hai file kết quả:

| Chỉ số | Ontology gợi ý | Tự thiết kế | Chênh lệch |
| --- | ---: | ---: | ---: |
| Node / cạnh tại lần benchmark | 202 / 381 | 343 / 601 | +141 / +220 |
| Indexing USD | 0.00925 | 0.00992 | +0.00067 |
| Indexing giây | 94.9 | 131.3 | +36.4 |
| Recall trung bình | 0.63 | 0.89 | +0.26 |
| Judge trung bình | 1.33 | 2.00 | +0.67 |
| Token đầu vào mỗi câu | 3307 | 1468 | −1839 |
| USD mỗi câu | 0.00053 | 0.00026 | −0.00027 |
| Giây mỗi câu | 1.83 | 1.87 | +0.04 |

| Câu | HINT recall / judge | Tự thiết kế recall / judge | Quan sát |
| --- | ---: | ---: | --- |
| Q1 | 1.00 / 2 | 1.00 / 2 | Cả hai trả lời định nghĩa tiền chất. |
| Q2 | 1.00 / 2 | 1.00 / 2 | Cả hai nêu đúng hai bị cáo lãnh án tử hình. |
| Q3 | 1.00 / 2 | 1.00 / 2 | Cả hai nối vụ Lê Minh Thành tới Điều 251. |
| Q4 | 0.00 / 0 | 1.00 / 2 | HINT thiếu câu trả lời về Hoàng Nato; bản mới nêu hành vi và mức tối đa. |
| Q5 | 0.80 / 1 | 1.00 / 2 | HINT nhầm Điều 251; bản mới nêu khoản 4 Điều 250. |
| Q6 | 0.00 / 1 | 0.33 / 2 | HINT liệt kê sai bối cảnh/khối lượng của một số vụ; bản mới vẫn thiếu tên người cho phép đo từ khóa. |

Flat RAG của hai lần đều có recall trung bình 0.43 và judge trung bình 1.00. Hệ thống mới tạo graph lớn hơn và indexing đắt hơn, đồng thời chọn ngữ cảnh gọn hơn: token đầu vào mỗi câu giảm khoảng 55.6%, phí hỏi giảm khoảng 50.9% theo số đã làm tròn trong hai file. Độ trễ gần bằng nhau nên chưa kết luận tốc độ hỏi đã cải thiện.

**Bằng chứng câu trả lời Q5, GraphRAG HINT, nguyên văn:**

```text
Cái Quang Huy bị truy tố về tội "vận chuyển trái phép chất ma túy" với loại ma túy là MDMA. Với khối lượng MDMA là hơn 9,6kg trong vụ này, khoản áp dụng tương ứng là Điều 251 BLHS khoản 4. Khung hình phạt theo quy định tại khoản này là từ 20 năm tù, tù chung thân hoặc tử hình.
```

**Bằng chứng Q5, GraphRAG tự thiết kế, nguyên văn:**

```text
Cái Quang Huy bị truy tố về tội vận chuyển trái phép chất ma túy, cụ thể là với loại ma túy MDMA. Với khối lượng MDMA hơn 9,6kg trong vụ này, khoản 4 của Điều 250 Bộ luật Hình sự (BLHS) tương ứng được áp dụng, và khung hình phạt là từ 20 năm tù, tù chung thân hoặc tử hình.
```

Đây là bằng chứng quan sát trên Q5, không khẳng định mọi lần chạy HINT đều nhầm như vậy. Truy vấn cấu trúc bên dưới kiểm tra riêng khả năng so ngưỡng, không gọi LLM.

## 2. Competency question trên dữ liệu thật: so ngưỡng MDMA bằng Cypher

**Câu hỏi CQ-B1:** Vụ Cái Quang Huy có vượt ngưỡng MDMA của khoản 4 Điều 250 không? Trả về số gam, ngưỡng và điểm/khoản bằng truy vấn graph.

Hai graph đều có bài `news-100260917203001265` và khoản 4 Điều 250. HINT lưu lượng MDMA dưới chuỗi `INVOLVES.amount="9.6kg"` và giữ luật trong `Clause.text`; thiết kế mới thêm `INVOLVES.amount_g=9600`, `comparator=">"` cùng `QuantityRule.min_g=100`, cận trên mở. HINT chưa có thuộc tính lượng theo gam hay node ngưỡng để đối chiếu số học trực tiếp; vẫn có thể trả lời bằng đọc văn bản và suy luận ngoài graph.

Chạy cùng truy vấn trên hai graph, tham số `$news_doc='news-100260917203001265'`, `$article_id='Điều 250 BLHS'`:

```cypher
MATCH (k:Case)-[r:INVOLVES]->(s:Substance {name:'MDMA'})
MATCH (k)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
MATCH (cl)-[:HAS_RULE]->(qr:QuantityRule)-[:FOR_SUBSTANCE]->(s)
WHERE k.doc_id = $news_doc AND a.id = $article_id AND cl.number = 4
  AND qr.max_g IS NULL AND qr.min_inclusive = true
  AND r.comparator IN ['=', '>', '>='] AND r.amount_g >= qr.min_g
RETURN k.name AS case_name, k.doc_id AS doc_id, a.id AS article,
       cl.number AS clause, qr.point AS point, qr.min_g AS threshold_g,
       r.amount_g AS amount_g, r.comparator AS comparator, cl.penalty AS penalty
ORDER BY case_name;
```

**HINT:** 0 hàng. **Tự thiết kế:** 1 hàng:

```text
case_name: Vụ vận chuyển ma túy từ Đức về Việt Nam
doc_id: news-100260917203001265
article: Điều 250 BLHS
clause: 4
point: b
threshold_g: 100.0
amount_g: 9600.0
comparator: >
penalty: phạt tù 20 năm, tù chung thân hoặc tử hình
```

Việc HINT có sẵn lượng và khoản luật được xác nhận trong `hint_raw_amount` và `hint_has_law_clause` của [JSON bằng chứng](bonus_evidence.json). Kết quả không rỗng ở thiết kế mới chứng minh khả năng đối chiếu ngưỡng trực tiếp trên graph. Đây là kiểm tra điều kiện khối lượng để truy xuất luật, chưa kết luận đầy đủ việc áp dụng pháp luật cho vụ án.

## 3. Competency question có kiểm soát: hai nguồn trùng tên có giữ được mức án riêng?

**Câu hỏi CQ-B2:** Với hai người/vụ khác nhau cùng tên hiển thị, nguồn A ghi 2 năm tù và nguồn B ghi 7 năm tù, graph trả được mức án riêng của cả hai nguồn không?

Đây là **fixture tổng hợp**, không phải hai vụ thật trong corpus. `fixture_payload` trong [bonus_compare.py](../scripts/bonus_compare.py) trả cùng JSON cố định cho hai builder: tên người đều là Nguyễn Văn A, tên vụ đều là “Vụ mua bán trái phép chất ma túy”; bí danh người A/B, ngày và địa điểm khác nhau. Không gọi LLM, nên kết quả kiểm tra khóa không bị nhiễu bởi trích xuất. Nạp lần lượt nguồn A rồi B.

```cypher
MATCH (p:Person)-[r:INVOLVED_IN]->(k:Case)
RETURN k.doc_id AS doc_id, k.name AS case_name, p.name AS person,
       p.aliases AS aliases, r.sentence AS sentence
ORDER BY doc_id;
```

| Graph | Case / Person | doc_id | aliases | sentence |
| --- | --- | --- | --- | --- |
| HINT | 1 / 1 | bonus-fixture-b | [người B] | 7 năm tù |
| Tự thiết kế | 2 / 2 | bonus-fixture-a | [người A] | 2 năm tù |
| Tự thiết kế | 2 / 2 | bonus-fixture-b | [người B] | 7 năm tù |

HINT MERGE theo tên nên nguồn B ghi đè doc_id, aliases và cạnh chứa mức án của nguồn A. Khóa theo `doc_id + case_index` và `doc_id + normalized_name` giữ được hai nguồn và hai mức án. Đánh đổi: người/vụ thật xuất hiện trong nhiều bài còn có thể thành nhiều node; E3 trong REPORT_KG vẫn là hạn chế cần giải quyết bằng lớp định danh liên bài.

## 4. Kiểm tra và phạm vi bằng chứng

[bonus_check.txt](bonus_check.txt) có đủ 7 dòng `[OK]`, gồm KG-2: 255 node / 506 cạnh, đường xuyên 2 KB dài 2 cạnh; KG-3: 10 dữ kiện có Điều 251. Đây là graph mẫu luật + 1 bài trên database riêng. Khi thu bằng chứng, graph bài chính vẫn là 343 node / 602 cạnh từ lần dựng lại trước đó; benchmark chính là snapshot 343 / 601 như đã ghi trong REPORT_KG.

Các file benchmark được sinh trực tiếp từ code, không sửa tay số liệu. Bằng chứng ngưỡng dùng dữ liệu thật; bằng chứng khóa dùng dữ liệu tổng hợp được đánh dấu rõ. Một lần benchmark chưa đủ để kết luận độ ổn định qua nhiều lần chạy hoặc chứng minh cả ba thay đổi đều cải thiện mọi câu hỏi.
