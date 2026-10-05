# Báo cáo Day 19 — Flat RAG vs GraphRAG

**Họ tên:** Nguyễn Anh Tuấn  **MSSV:** 2A202602700  **Ngày:** 05-10-2026

Số liệu mục 1, 2 và 4 lấy từ [ket_qua_benchmark_kg.txt](../ket_qua_benchmark_kg.txt), chạy với --judge, 176 chunks, top_k=3, graph 343 node / 601 cạnh. Ontology nằm trong [ONTOLOGY.md](ONTOLOGY.md).

## 1. Chi phí (10 điểm)

~~~text
== Indexing (one-off)
pipeline  calls    in_tok  out_tok       USD  seconds
flat        176     56072        0   0.00112     47.5
graph       196     94838     4976   0.00992    131.3

== Querying (mean per question)
pipeline  recall  judge   in_tok  out_tok       USD  seconds
flat        0.43   1.00      694       47   0.00013     1.45
graph       0.89   2.00     1468       72   0.00026     1.87
~~~

| Chỉ số | Flat | Graph | Graph / Flat |
| --- | ---: | ---: | ---: |
| Indexing USD | 0.00112 | 0.00992 | ×8.86 |
| Indexing giây | 47.5 | 131.3 | ×2.76 |
| Mỗi câu: USD | 0.00013 | 0.00026 | ×2.00 |
| Mỗi câu: giây | 1.45 | 1.87 | ×1.29 |
| Mỗi câu: in_tok | 694 | 1468 | ×2.12 |

**Chi phí tăng thêm đến từ đâu?** Khi indexing, cả hai pipeline tạo embedding cho 176 chunks; GraphRAG còn gọi LLM để trích xuất 20 bài báo và nạp các thực thể, quan hệ. Khi hỏi, GraphRAG giữ các chunks của Flat RAG rồi thêm dữ kiện graph vào prompt, nên trung bình số token đầu vào tăng từ 694 lên 1468 và chi phí hỏi tăng gấp đôi. Phần indexing tăng thêm 0.00880 USD; GraphRAG không có điểm hòa vốn về chi phí thuần ở mức giá mỗi câu đo được, nên giá trị câu trả lời nhiều bước phải bù cho khoản tăng này.

## 2. Từng câu hỏi (10 điểm)

| Câu | Loại | Flat recall / judge | Graph recall / judge | Thắng | Vì sao |
| --- | --- | ---: | ---: | --- | --- |
| Q1 | single-hop-law | 1.00 / 2 | 1.00 / 2 | Hòa | Định nghĩa tiền chất nằm ngay trong đoạn luật truy hồi. |
| Q2 | single-hop-news | 1.00 / 2 | 1.00 / 2 | Hòa | Tên hai bị cáo lãnh án tử hình có ngay trong một bài báo. |
| Q3 | cross-kb | 0.00 / 0 | 1.00 / 2 | Graph | Vụ Lê Minh Thành nối sang Điều 251 và khung cơ bản. |
| Q4 | cross-kb | 0.00 / 0 | 1.00 / 2 | Graph | Bí danh Hoàng Nato nối qua vụ án, tội danh tới Điều 255 và mức chung thân. |
| Q5 | cross-kb-multi-hop | 0.60 / 1 | 1.00 / 2 | Graph | Graph nêu đúng khoản 4 Điều 250 cho hơn 9,6 kg MDMA. |
| Q6 | aggregation | 0.00 / 1 | 0.33 / 2 | Graph theo judge | Graph liệt kê ba loại vụ, nhưng recall từ khóa không công nhận hai vụ thiếu tên người. |

Hai câu đơn bước đều hòa; ba câu cần nối tin tức với luật (Q3–Q5) đều nghiêng về GraphRAG. Q6 cần đọc câu trả lời và đối chiếu cả hai thang đo.

## 3. Phân tích lỗi (20 điểm)

Các truy vấn dưới đây chạy trên graph đầy đủ dựng lại từ cùng 18 văn bản luật và 20 bài báo. Vì trích xuất tin tức bằng LLM có biến động, lần dựng lại có 343 node / 602 cạnh, còn file benchmark ghi 343 node / 601 cạnh; các hàng bằng chứng dưới đây vẫn hiện diện.

### Lỗi E3: Một người thành bốn node

- **Hiện tượng:** Dương Minh Tuấn, bí danh Hoàng Nato, xuất hiện thành bốn node Person theo bốn bài báo. Ảnh [kg_my_case.png](img/kg_my_case.png) cũng cho thấy bốn node Person khi truy vấn tên này.
- **Bằng chứng:** Truy vấn Neo4j:

~~~cypher
MATCH (p:Person)
WHERE 'Hoàng Nato' IN coalesce(p.aliases, [])
RETURN p.name AS name, p.id AS id, p.doc_id AS doc_id
ORDER BY doc_id;
~~~

~~~text
name             id                                               doc_id
Dương Minh Tuấn  news-100260920221957595:person:dương minh tuấn  news-100260920221957595
Dương Minh Tuấn  news-100260922111804786:person:dương minh tuấn  news-100260922111804786
Dương Minh Tuấn  news-100260924095400982:person:dương minh tuấn  news-100260924095400982
Dương Minh Tuấn  news-100260925144412498:person:dương minh tuấn  news-100260925144412498
~~~

Các [bài 1](../data/drug_news/news-100260920221957595.md), [bài 2](../data/drug_news/news-100260922111804786.md), [bài 3](../data/drug_news/news-100260924095400982.md), [bài 4](../data/drug_news/news-100260925144412498.md) đều nhận diện cùng tên, bí danh và bối cảnh vụ việc.

- **Nguyên nhân:** Ở bước dựng graph và thiết kế ontology, khóa Person.id được tạo bằng doc_id + ':person:' + tên chuẩn hóa. MERGE chỉ gộp các lần nhắc trong cùng bài; người xuất hiện ở bài khác luôn có khóa khác. Thiết kế này giữ xuất xứ nhưng thiếu lớp định danh người liên bài.
- **Đề xuất sửa:** Trong `src/graph.py` và ontology, giữ node lần nhắc theo bài để truy vết nguồn, thêm node Person chuẩn và quan hệ REFERS_TO từ từng lần nhắc. Chỉ hợp nhất khi tên, bí danh và bối cảnh (vụ việc, địa phương, tuổi nếu có) khớp đủ tin cậy; trường hợp mơ hồ cần kiểm tra thủ công. Việc so khớp và rà soát làm tăng thời gian dựng graph; MERGE chỉ theo họ tên tuy rẻ hơn nhưng dễ gộp nhầm người trùng tên.

### Lỗi E4: Recall từ khóa và judge mâu thuẫn

- **Hiện tượng:** Q6 của GraphRAG được judge=2 nhưng recall=0.33; câu trả lời liệt kê ba vụ theo mô tả, thiếu tên hai nhân vật mà danh sách từ khóa yêu cầu.
- **Bằng chứng:** Trích nguyên văn Q6 GraphRAG trong [file kết quả](../ket_qua_benchmark_kg.txt):

~~~text
--- Q6 [aggregation] graph recall=0.33 judge=2 2.94s
Các vụ việc trong tin tức có liên quan đến ma túy MDMA bao gồm:

1. Vụ 'Vụ vận chuyển ma túy từ Đức về Việt Nam' - liên quan đến MDMA với tổng khối lượng hơn 9,6kg.
2. Vụ 'Vụ án mua bán trái phép chất ma túy' - liên quan đến MDMA.
3. Vụ 'Vụ án tổ chức sử dụng ma túy tại Viện Pháp y tâm thần Trung ương' - liên quan đến MDMA với khối lượng 0,686g.
~~~

Trong [benchmark_kg.json](../data/benchmark_kg.json), must_include của Q6 là ["Cái Quang Huy", "Lê Minh Thành", "Pháp y tâm thần"]. Chỉ cụm cuối xuất hiện nguyên văn nên phép đo cho 1/3 = 0.33. Mô tả vụ thứ nhất tương ứng vụ Cái Quang Huy; vụ thứ hai quá chung để người đọc xác định chắc đó là vụ Lê Minh Thành.

- **Nguyên nhân:** Ở bước đánh giá, keyword_recall trong bench_kg.py chỉ đếm chuỗi con, không nhận ra cách gọi cùng một vụ bằng tiêu đề hoặc mô tả. Judge dùng LLM so với đáp án vàng nên nhận ra phần lớn ý nghĩa, nhưng điểm 2 có thể quá rộng tay với vụ thứ hai thiếu dấu hiệu nhận diện. Đây là lỗi thiết kế phép đo, không chứng minh graph thiếu ba vụ.
- **Đề xuất sửa:** Trong `data/benchmark_kg.json` và `bench_kg.py`, gắn ID vụ chuẩn cho từng đáp án vàng, chấm riêng độ phủ vụ và độ rõ định danh; đối chiếu tên người, tiêu đề và doc_id như các bí danh có kiểm chứng. Giữ điểm LLM judge như tín hiệu bổ sung, nhưng yêu cầu giải thích từng vụ được ghép với nguồn nào trước khi cho điểm tối đa. Cần công gắn nhãn đáp án vàng và thêm token nếu yêu cầu judge giải thích.

### Lỗi E6: Quan hệ người–vụ thiếu tội danh dù bài gốc nêu rõ

- **Hiện tượng:** Bốn cạnh INVOLVED_IN của bài về đường dây hơn 36 kg ma túy có charge='', dù bài nêu tội của cả bốn bị cáo.
- **Bằng chứng:** Truy vấn Neo4j:

~~~cypher
MATCH (p:Person)-[r:INVOLVED_IN]->(k:Case {doc_id:'news-100260928173914514'})
WHERE coalesce(r.charge, '') = ''
RETURN p.name AS person, r.role AS role, r.sentence AS sentence, r.charge AS charge
ORDER BY person;
~~~

~~~text
person             role    sentence          charge
Trần Ngọc Thảo     bị cáo  2 năm 6 tháng tù  ""
Võ Nữ Minh Thư     bị cáo  8 năm tù          ""
Đinh Đức Tuấn      bị cáo  2 năm 6 tháng tù  ""
Đỗ Thị Ngọc Yến    bị cáo  8 năm 6 tháng tù  ""
~~~

[Bài báo gốc](../data/drug_news/news-100260928173914514.md) viết: “Cùng tội danh trên” cho Đỗ Thị Ngọc Yến và Võ Nữ Minh Thư, tức tội mua bán trái phép chất ma túy ở câu trước; Đinh Đức Tuấn và Trần Ngọc Thảo “về tội tổ chức sử dụng trái phép chất ma túy”.

Trường hợp rỗng hợp lý để đối chiếu: truy vấn `MATCH (p:Person)-[r:INVOLVED_IN]->(k:Case {doc_id:'news-100260917203001265'}) WHERE p.name = 'Nguyễn Hữu Đức' RETURN p.name, r.role, r.charge` trả về `Nguyễn Hữu Đức | người liên quan | ""`. [Bài về Cái Quang Huy](../data/drug_news/news-100260917203001265.md) nêu rõ cơ quan điều tra không đủ chứng cứ Đức biết kiện hàng chứa ma túy và đã hủy quyết định khởi tố; vì vậy không nên tự điền tội danh cho Đức.

- **Nguyên nhân:** Ở bước LLM trích xuất tin tức, trường people[].charge bị bỏ trống khi tội danh được nói bằng tham chiếu “cùng tội danh trên” hoặc gắn với hai người trong một câu. Bước extract_news_cases đổi giá trị rỗng thành '', và build_graph chép nó vào cạnh INVOLVED_IN. Có tội danh ở mức Case không đủ để suy ra tội của từng người, vì một vụ có nhiều tội khác nhau.
- **Đề xuất sửa:** Trong NEWS_EXTRACTION_PROMPT và extract_news_cases của `src/graph.py`, bổ sung kiểm tra sau trích xuất cho mẫu “cùng tội danh trên” và câu “A và B … về tội X”, rồi đối chiếu từng people[].charge với câu nguồn trước khi nạp graph. Chỉ điền tội khi văn bản quy trách nhiệm rõ cho người đó; giữ rỗng cho người chưa bị truy tố. Bước đối chiếu làm tăng thời gian xử lý hoặc token nếu nhờ LLM kiểm lại; quy tắc sai phạm vi câu có thể gán tội của bị cáo này cho bị cáo khác.

## 4. Kết luận (5 điểm)

Với câu chỉ cần một đoạn luật hoặc một bài báo, Flat RAG đủ: Q1 và Q2 cùng đạt recall=1.00, judge=2 ở cả hai pipeline. Với câu phải đi từ người hoặc vụ trong tin tức sang Điều luật và khoản tương ứng, nên dùng KG: Q3–Q5 có judge GraphRAG đều bằng 2, trong khi Flat lần lượt 0, 0, 1. Đổi lại, GraphRAG tốn 0.00992 USD để indexing so với 0.00112 USD, và mỗi câu trung bình 0.00026 USD so với 0.00013 USD. Với câu tổng hợp như Q6, graph giúp tìm các vụ liên quan nhưng vẫn cần kiểm tra định danh và cách chấm điểm.

## 5. Tự kiểm (5 điểm)

~~~text
$ pytest tests/ -q
................................................                         [100%]
48 passed in 0.12s

$ python -u -X utf8 scripts/bonus_compare.py --stage check
[OK] Dữ liệu: 18 điều luật, 20 bài báo
[OK] KG-1 link_entity
[OK] Neo4j kết nối được
[provider] chat = openai:gpt-4o-mini | embedding = openai:text-embedding-3-small
[OK] KG-2 build_graph: 255 node / 506 cạnh, đường xuyên 2 KB dài 2 cạnh
[OK] KG-3 context: 10 dữ kiện, có Điều 251
[OK] KG-4 GraphRAGAgent.answer
[OK] Chi phí check: 1 lần gọi LLM, $0.00067. Graph nhỏ (luật + 1 bài) vẫn còn trong Neo4j để bạn xem; chạy --judge để dựng graph đầy đủ.
~~~

Script bonus gọi nguyên bản `bench_kg.py --check` trên Neo4j riêng ở cổng 17687. Output cũng được lưu ở [bonus_check.txt](bonus_check.txt). Graph bài chính giữ đầy đủ 18 văn bản luật và 20 bài báo.

Ảnh Neo4j: [kg_count.png](img/kg_count.png), [kg_cross_kb.png](img/kg_cross_kb.png), [kg_my_case.png](img/kg_my_case.png). Người đã chọn cho kg_my_case.png: **Dương Minh Tuấn (Hoàng Nato)**.

## Bonus: đối chiếu ontology gợi ý

Đã sinh [ket_qua_benchmark_kg.hint.txt](../ket_qua_benchmark_kg.hint.txt) bằng benchmark gốc với package HINT riêng. GraphRAG HINT đạt recall 0.63 / judge 1.33; bản tự thiết kế trong benchmark chính đạt 0.89 / 2.00. Q5 của HINT trả nhầm Điều 251, còn bản mới nêu khoản 4 Điều 250. Chi phí indexing mới tăng từ 0.00925 lên 0.00992 USD; token đầu vào mỗi câu giảm từ 3307 xuống 1468 trong hai lần đo này.

Mục 7 của [ONTOLOGY.md](ONTOLOGY.md) đã điền bằng chứng trước/sau. [BONUS_EVIDENCE.md](BONUS_EVIDENCE.md) chứa câu trả lời nguyên văn, Cypher và kết quả: so 9600 g MDMA với ngưỡng 100 g trên dữ liệu thật, cùng fixture tổng hợp chứng minh bảo toàn hai nguồn và mức án khi tên trùng. Hai lần benchmark có nhiễu LLM; fixture dùng JSON cố định để kiểm tra riêng việc chọn khóa.

## Vấn đề gặp phải (không tính điểm)

Lệnh python bench_kg.py --check lần đầu dừng trước khi truy cập graph vì console PowerShell dùng cp1252, không in được chữ Việt trong dòng [OK]. Chạy lại bằng python -X utf8 bench_kg.py --check thì hoàn tất; không sửa benchmark hay test. Lần dựng lại graph đầy đủ có 602 cạnh, lệch một cạnh so với snapshot benchmark 601 cạnh do trích xuất tin tức bằng LLM không hoàn toàn tất định.
