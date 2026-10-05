# Baseline ontology gợi ý

Đây là bản thực thi để so sánh bonus, không thay thế `src/graph.py`.

- Nguồn HINT: `src/graph.py` tại commit `859e8d1` của repo lab.
- Giữ prompt trích xuất, các query ghi graph và khóa tên của HINT.
- KG-2 gọi `suggested_constraints`, `add_law_article`, `extract_news_cases`, `add_news_case`.
- KG-3 thực hiện hướng dẫn Bước 5: seed → vụ → tội → Điều → khoản 1 và các khoản nhắc chất của vụ; thêm Điều được hỏi trực tiếp.
- KG-1, KG-4, parser luật, seed một bước, embedding, metering và provider dùng chung với bài chính. Lời gọi trích xuất sử dụng `json_mode=True` theo hợp đồng lab.
- Không sửa `bench_kg.py` hoặc bộ test gốc. Script benchmark có sẵn biến `LAB_SOLUTION_PACKAGE`, được đặt thành `baselines.hint` khi chạy baseline.

## Chạy lại

Từ thư mục gốc, dùng database Neo4j riêng cho so sánh:

```powershell
docker run -d --name neo4j-drug-kg-bonus -p 127.0.0.1:17687:7687 -e NEO4J_AUTH=neo4j/bonus_lab_local neo4j:5
# Chờ log "Started."; nếu container đã tồn tại thì dùng docker start neo4j-drug-kg-bonus.
python -u -X utf8 scripts/bonus_compare.py
docker stop neo4j-drug-kg-bonus
```

Mật khẩu trên chỉ dùng cho database lab cục bộ tạm thời. Có thể đổi qua `BONUS_NEO4J_PASSWORD` và cấu hình container tương ứng.

Script sinh:

- `ket_qua_benchmark_kg.hint.txt`: output trực tiếp của `bench_kg.py --judge --out ...` với baseline.
- `report/bonus_check.txt`: output kiểm tra KG-1–KG-4 của bài chính trên database riêng.
- `report/bonus_evidence.json`: Cypher, tham số và kết quả thực cho graph đầy đủ, cùng tình huống tổng hợp kiểm tra khóa.

Chạy từng phần bằng `--stage check`, `--stage benchmark`, hoặc `--stage evidence`. Phần evidence cần graph HINT đầy đủ từ benchmark trước đó, vì sau khi chụp bằng chứng nó thay graph HINT bằng các fixture trên database riêng. Graph bài chính ở cổng 7687 chỉ được đọc.

## Phạm vi kết luận

Benchmark là so sánh hai pipeline đã thực thi, gồm cả prompt trích xuất và cách chọn ngữ cảnh. Mỗi bản trích xuất tin bằng LLM riêng nên kết quả có biến động; chênh lệch điểm không chứng minh quan hệ nhân quả của riêng một thay đổi ontology. Truy vấn ngưỡng khối lượng trên dữ liệu thật và fixture dùng JSON cố định cung cấp bằng chứng trực tiếp cho thay đổi cấu trúc graph.

Fixture gồm hai người và hai vụ khác nhau được chủ động cho cùng tên hiển thị, dùng hai nguồn tổng hợp A/B và mức án 2/7 năm. Đây là dữ liệu kiểm tra tự tạo, không phải hai vụ trong corpus tin tức.
