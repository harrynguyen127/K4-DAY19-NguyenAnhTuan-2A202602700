"""Knowledge Graph (Neo4j) + GraphRAG over two drug-topic knowledge bases.

Contract (fixed — bench_kg.py and the tests rely on it):
    link_entity(name, known)                       -> one of `known` or None
    build_graph(graph, law_docs, news_docs, llm_fn)   load both KBs into Neo4j
        every node created from ONE document carries the property `doc_id`
    Neo4jGraph.context(question, doc_ids)         -> list[str] facts
    GraphRAGAgent.answer(question, top_k)         -> str

Ontology: report/ONTOLOGY.md. Crime and Substance bridge the two KBs; per-document
nodes keep doc_id. QuantityRule stores only mass bounds parsed unambiguously from law.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path
from typing import Any, Callable

from .models import Document
from .store import EmbeddingStore

# Canonical substance names: the ones BLHS Chương XX lists, plus common ones in Vietnamese news.
SUBSTANCES = ["Heroine", "Cocaine", "Methamphetamine", "Amphetamine", "MDMA", "XLR-11", "Ketamine",
              "cần sa", "thuốc phiện", "côca"]
CLAUSE_START = re.compile(r"^(\d+)\.\s", re.MULTILINE)
FOOTNOTE = re.compile(r"\[\d+\]")

def load_markdown_docs(folder: str | Path) -> list[Document]:
    """Read crawler output (.md with a flat `key: "value"` front matter) into Documents."""
    docs = []
    for path in sorted(Path(folder).glob("*.md")):
        raw = path.read_text(encoding="utf-8")
        _, front, body = raw.split("---", 2)
        metadata = {k: json.loads(v) for k, v in re.findall(r'^(\w+): (".*")$', front, re.MULTILINE)}
        docs.append(Document(id=metadata.get("doc_id", path.stem), content=body.strip(), metadata=metadata))
    return docs

def normalize_crime(name: str) -> str:
    """'Tội Mua bán trái phép chất ma túy' -> 'mua bán trái phép chất ma túy'."""
    name = re.sub(r"\s+", " ", name.strip().strip("\"'“”").lower())
    return name.removeprefix("tội ").strip()

def link_entity(name: str, known: list[str], normalize: Callable[[str], str] = normalize_crime) -> str | None:
    """Map a free-text mention (e.g. a charge written by a journalist) onto one canonical name in `known`."""
    target = normalize(name)
    if not target.strip():
        return None

    originals_by_normalized: dict[str, str] = {}
    for original in known:
        normalized = normalize(original)
        if normalized.strip():
            originals_by_normalized.setdefault(normalized, original)

    if target in originals_by_normalized:
        return originals_by_normalized[target]

    matches = difflib.get_close_matches(target, originals_by_normalized, n=1, cutoff=0.8)
    return originals_by_normalized[matches[0]] if matches else None

def find_substances(text: str) -> list[str]:
    lowered = text.lower()
    return [name for name in SUBSTANCES if name.lower() in lowered]

# ----------------------------------------------------------------------------------------------
# Deterministic law extraction and controlled news extraction
# ----------------------------------------------------------------------------------------------

def parse_law_article(doc: Document) -> dict[str, Any]:
    """Deterministic (regex) extraction for one 'Điều' — law text is regular enough to skip the LLM."""
    article_id = doc.metadata["article"]                       # "Điều 251 BLHS"
    title = doc.metadata["title"].split(". ", 1)[-1]           # "Tội mua bán trái phép chất ma túy"
    body = FOOTNOTE.sub("", doc.content)
    starts = list(CLAUSE_START.finditer(body))
    clauses = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        text = body[start.start():end].strip()
        first_line = text.splitlines()[0]
        penalty = re.search(r"\bbị ((?:phạt|tù|cảnh cáo).+?)(?::|$)", first_line)
        clauses.append({
            "id": f"{article_id} khoản {start.group(1)}",
            "number": int(start.group(1)),
            "penalty": penalty.group(1).rstrip(".") if penalty else "",
            "text": text,
            "substances": find_substances(text),
        })
    return {
        "id": article_id,
        "law": doc.metadata.get("law", ""),
        "title": title,
        "doc_id": doc.id,
        "crime": normalize_crime(title) if title.startswith("Tội ") else None,
        "clauses": clauses,
    }

NEWS_EXTRACTION_PROMPT = """Trích xuất sự kiện từ MỘT bài báo thành một JSON object, không thêm lời giải thích.
Chỉ ghi điều bài báo khẳng định; phân biệt cáo buộc, bản án sơ thẩm và phúc thẩm.
Một vụ án là một case, dù có nhiều bị cáo hoặc nhiều kiện hàng. Không tách case theo người.
Người được minh oan/hủy khởi tố vẫn có thể nằm trong people với role phù hợp; không gán tội cho họ.
Nếu không có vụ việc cụ thể, trả {{"cases": []}}. Không suy đoán tên chất, số lượng, tội hay mức án.
Chọn tên tội/chất nguyên văn từ danh sách chuẩn nếu đủ bằng chứng, nếu không thì để trống hoặc bỏ qua.
Ghi mọi chất được bài xác định, kể cả khi không có khối lượng (amount_raw = "").
Với mỗi chất chỉ ghi một amount_raw khi bài nêu rõ tổng lượng của vụ; không tự cộng các lần thu giữ.
Mức án trong sentence chỉ là án đã tuyên cho chính người đó, không ghi khung hình phạt hay án đang đề nghị.
date là ngày vụ việc/phiên xử ĐÃ diễn ra. Nếu có nhiều ngày sự kiện, để chuỗi rỗng.
Ngày dùng YYYY-MM-DD. Chỉ suy ra năm cho "hôm nay" từ ngày đăng; không đoán năm của ngày/tháng khác.

JSON schema (mọi khóa đều bắt buộc):
{{"cases": [{{"name": "tên vụ ngắn", "summary": "1-2 câu", "date": "", "location": "",
  "charges": ["tên tội"],
  "substances": [{{"name": "tên chất", "amount_raw": "nguyên văn lượng hoặc chuỗi rỗng"}}],
  "people": [{{"name": "họ tên", "aliases": ["bí danh"], "role": "vai trò theo bài",
              "charge": "tội của riêng người này hoặc chuỗi rỗng", "sentence": "án đã tuyên hoặc chuỗi rỗng"}}]
}}]}}

TÊN TỘI CHUẨN: {crimes}
TÊN CHẤT CHUẨN: {substances}
NGÀY ĐĂNG: {published_at}
TIÊU ĐỀ: {title}
NỘI DUNG:
{content}"""


def normalize_substance(name: str) -> str:
    """Normalize spelling and a few unambiguous substance aliases."""
    value = re.sub(r"\s+", " ", name.strip()).casefold()
    return {"heroin": "heroine", "ketamin": "ketamine"}.get(value, value)


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def extract_news_cases(doc: Document, llm_fn: Callable[..., str], known_crimes: list[str]) -> list[dict]:
    """Extract a news article once and map all bridge names back to canonical law names."""
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances="; ".join(SUBSTANCES),
        published_at=doc.metadata.get("document_version", ""),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        payload = json.loads(llm_fn(prompt, json_mode=True))
    except json.JSONDecodeError as error:
        raise ValueError(f"JSON trích xuất không hợp lệ cho {doc.id}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError(f"JSON trích xuất thiếu danh sách cases cho {doc.id}")

    cases = []
    for raw in payload["cases"]:
        if not isinstance(raw, dict):
            continue
        charges = raw.get("charges") if isinstance(raw.get("charges"), list) else []
        people = raw.get("people") if isinstance(raw.get("people"), list) else []
        substances = raw.get("substances") if isinstance(raw.get("substances"), list) else []
        case = {
            "name": _string(raw.get("name")) or _string(doc.metadata.get("title")),
            "summary": _string(raw.get("summary")),
            "date": _string(raw.get("date")),
            "location": _string(raw.get("location")),
            "charges": list(dict.fromkeys(c for x in charges
                                          if (c := link_entity(_string(x), known_crimes)))),
            "substances": [],
            "people": [],
        }
        for item in substances:
            if not isinstance(item, dict):
                continue
            canonical = link_entity(_string(item.get("name")), SUBSTANCES, normalize_substance)
            if canonical and canonical not in {s["name"] for s in case["substances"]}:
                amount = _string(item.get("amount_raw") or item.get("amount"))
                case["substances"].append({"name": canonical, "amount_raw": amount})
        for item in people:
            if not isinstance(item, dict) or not _string(item.get("name")):
                continue
            aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
            case["people"].append({
                "name": _string(item["name"]),
                "aliases": list(dict.fromkeys(a for x in aliases if (a := _string(x)))),
                "role": _string(item.get("role")),
                "charge": link_entity(_string(item.get("charge")), known_crimes) or "",
                "sentence": _string(item.get("sentence")),
            })
        case["charges"] = list(dict.fromkeys(case["charges"] +
                                             [p["charge"] for p in case["people"] if p["charge"]]))
        cases.append(case)
    return cases


MASS = r"([0-9]+(?:[.,][0-9]+)?)\s*(kilôgam|kilogram|kg|gam|g)\b"
MASS_RANGE = re.compile(rf"có khối lượng từ {MASS}\s+đến dưới {MASS}", re.IGNORECASE)
MASS_MIN = re.compile(rf"có khối lượng {MASS}\s+trở lên", re.IGNORECASE)
POINT_LINE = re.compile(r"^([a-zđ])\)\s*(.+)$", re.MULTILINE | re.IGNORECASE)
RULE_SUBSTANCES = set(SUBSTANCES[:7])


def _grams(number: str, unit: str) -> float:
    return float(number.replace(",", ".")) * (1000 if unit.lower() in {"kilôgam", "kilogram", "kg"} else 1)


def extract_quantity_rules(clause: dict) -> list[dict]:
    """Parse only simple named-chemical mass rules; leave plant forms and mixtures as text."""
    rules = []
    for point, line in POINT_LINE.findall(clause["text"]):
        match = MASS_RANGE.search(line) or MASS_MIN.search(line)
        if not match:
            continue
        names = re.split(r",\s*|\s+hoặc\s+", line[:match.start()])
        canonical = [link_entity(name, SUBSTANCES, normalize_substance) for name in names]
        if not canonical or any(name not in RULE_SUBSTANCES for name in canonical):
            continue
        lower = _grams(match.group(1), match.group(2))
        upper = _grams(match.group(3), match.group(4)) if match.re is MASS_RANGE else None
        for name in canonical:
            rules.append({
                "id": f"{clause['id']} điểm {point} {name}", "clause_id": clause["id"],
                "point": point, "substance": name, "min_g": lower, "max_g": upper,
                "min_inclusive": True, "max_inclusive": False if upper is not None else None,
                "raw_text": f"{point}) {line}",
            })
    return rules


def parse_amount(amount_raw: str) -> tuple[float | None, str]:
    """Keep the original amount and derive grams only for a single explicit mass."""
    matches = list(re.finditer(MASS, amount_raw, re.IGNORECASE))
    if len(matches) != 1:
        return None, ""
    amount = _grams(matches[0].group(1), matches[0].group(2))
    lowered = amount_raw.casefold()
    comparator = (">" if "hơn" in lowered or "trên" in lowered else
                  "~" if "gần" in lowered or "khoảng" in lowered else "=")
    return amount, comparator

# ----------------------------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------------------------

class Neo4jGraph:
    """Thin wrapper over the official neo4j driver."""

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self.driver = GraphDatabase.driver(uri, auth=(user, password), notifications_min_severity="OFF")
        self.driver.verify_connectivity()

    def close(self) -> None:
        self.driver.close()

    def run(self, cypher: str, **params: Any) -> list[dict]:
        records, _, _ = self.driver.execute_query(cypher, params)
        return [record.data() for record in records]

    def reset(self) -> None:
        """Delete every node, relationship and constraint (bench_kg.py calls this before build_graph)."""
        self.run("MATCH (n) DETACH DELETE n")
        for row in self.run("SHOW CONSTRAINTS YIELD name RETURN name"):
            self.run(f"DROP CONSTRAINT `{row['name']}` IF EXISTS")

    def stats(self) -> dict[str, int]:
        nodes = self.run("MATCH (n) RETURN count(n) AS n")[0]["n"]
        rels = self.run("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        return {"nodes": nodes, "relationships": rels}

    def seed_facts(self, question: str, doc_ids: list[str], skip_labels: tuple[str, ...] = (),
                   limit: int = 60) -> tuple[list[str], list[str]]:
        """Ontology-independent first step: seed nodes + their 1-hop edges as text facts.

        Seeds = nodes whose `doc_id` is in doc_ids, or whose `name`/`aliases` appear in the question.
        Returns (seed elementIds, facts). Nodes with a label in skip_labels are left out of the facts.
        """
        seeds = self.run(
            """
            MATCH (n)
            WHERE n.doc_id IN $doc_ids
               OR (n.name IS :: STRING AND size(n.name) >= 3 AND toLower($q) CONTAINS toLower(n.name))
               OR any(a IN coalesce(n.aliases, []) WHERE size(a) >= 3 AND toLower($q) CONTAINS toLower(a))
            RETURN elementId(n) AS id
            """,
            q=question, doc_ids=doc_ids,
        )
        seed_ids = [row["id"] for row in seeds]
        edges = self.run(
            """
            MATCH (s)-[r]-(m)
            WHERE elementId(s) IN $ids
              AND none(l IN labels(s) + labels(m) WHERE l IN $skip)
            WITH DISTINCT r LIMIT $limit
            WITH startNode(r) AS a, r, endNode(r) AS b
            RETURN labels(a)[0] AS a_label, coalesce(a.name, a.id) AS a_name, type(r) AS rel,
                   properties(r) AS props, labels(b)[0] AS b_label, coalesce(b.name, b.id) AS b_name
            """,
            ids=seed_ids, skip=list(skip_labels), limit=limit,
        )
        facts = []
        for e in edges:
            props = ", ".join(f"{k}: {v}" for k, v in e["props"].items() if v)
            facts.append(f"({e['a_label']}: {e['a_name']}) -[{e['rel']}{' {' + props + '}' if props else ''}]-> "
                         f"({e['b_label']}: {e['b_name']})")
        return seed_ids, facts

    # ---------------------------------------------------------------- KG-3

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        """Expand seed nodes through Crime/QuantityRule and return compact, sourced facts."""
        if max_facts <= 0:
            return []

        q = question.casefold()
        question_substances = find_substances(question)
        article_numbers = list(dict.fromkeys(re.findall(r"điều\s+(\d+)", q)))
        requested_clauses = {int(n) for n in re.findall(r"khoản\s+(\d+)", q)}
        definition = re.search(r"(.+?)\s+là gì", q)
        phrase = " ".join(re.findall(r"\w+", definition.group(1))[-2:]) if definition else ""
        wants_highest = "cao nhất" in q or "tối đa" in q
        wants_law_detail = any(word in q for word in ("khoản", "khung", "mức phạt", "hình phạt"))
        aggregate_substances = (question_substances if any(phrase in q for phrase in
                                ("những vụ", "các vụ", "vụ việc nào")) else [])
        seed_ids, one_hop = self.seed_facts(
            question, doc_ids, skip_labels=("Clause", "QuantityRule"), limit=min(8, max_facts),
        )
        facts: list[str] = []
        seen: set[str] = set()

        def add(fact: str) -> None:
            if fact and fact not in seen:
                facts.append(fact)
                seen.add(fact)

        def clause_fact(row: dict, details: list[str] | None = None) -> str:
            """Keep the sanction line and only the relevant point of a long BLHS clause."""
            text = row["text"] or ""
            if row["article_id"].endswith("BLHS"):
                first = text.splitlines()[0].strip() if text else ""
                excerpt = " ".join([first, *(details or [])]).strip()
            else:
                excerpt = re.sub(r"\s+", " ", text).strip()
            return (f"[{row['article_id']} - {row['title']}] "
                    f"khoản {row['number']}: {excerpt}")

        named_cases = self.run(
            """MATCH (p:Person)-[:INVOLVED_IN]->(k:Case)
               WHERE toLower($question) CONTAINS toLower(p.name)
                  OR any(alias IN coalesce(p.aliases, [])
                         WHERE toLower($question) CONTAINS toLower(alias))
               RETURN DISTINCT k.id AS id""",
            question=question,
        )
        named_case_ids = [row["id"] for row in named_cases]
        direct_law_only = (bool(article_numbers) and not named_case_ids and
                           not any(doc_id.startswith("news-") for doc_id in doc_ids))
        cases = [] if direct_law_only else self.run(
            """MATCH (k:Case)
               WHERE (elementId(k) IN $ids OR
                      EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids })
                 AND (size($named_case_ids) = 0 OR k.id IN $named_case_ids)
                 AND (size($aggregate_substances) = 0 OR EXISTS {
                      MATCH (k)-[:INVOLVES]->(sub:Substance)
                      WHERE sub.name IN $aggregate_substances })
               RETURN k.id AS id, k.name AS name, k.summary AS summary, k.doc_id AS doc_id
               ORDER BY CASE WHEN k.doc_id IN $doc_ids THEN 0 ELSE 1 END, k.id
               LIMIT $limit""",
            ids=seed_ids, doc_ids=doc_ids, named_case_ids=named_case_ids,
            aggregate_substances=aggregate_substances, limit=min(20, max_facts),
        )
        if not named_case_ids and not aggregate_substances:
            retrieved_cases = [case for case in cases if case["doc_id"] in doc_ids]
            if retrieved_cases:
                cases = retrieved_cases
        case_ids = [case["id"] for case in cases]
        names = {case["id"]: case["name"] for case in cases}
        for case in cases:
            add(f"Vụ '{case['name']}' (nguồn {case['doc_id']}): {case['summary'] or 'không có tóm tắt'}")

        if case_ids:
            focus_crimes: list[str] = []
            people = self.run(
                """MATCH (p:Person)-[r:INVOLVED_IN]->(k:Case)
                   WHERE k.id IN $case_ids
                   RETURN k.id AS case_id, p.name AS name, p.aliases AS aliases,
                          r.role AS role, r.charge AS charge, r.sentence AS sentence
                   ORDER BY k.id, p.name""",
                case_ids=case_ids,
            )
            named_people = [p for p in people if p["name"].casefold() in q or
                            any(alias.casefold() in q for alias in (p["aliases"] or []))]
            if named_people:
                people = named_people
                focus_crimes = list(dict.fromkeys(p["charge"] for p in people if p["charge"]))
            elif "tử hình" in q:
                people = [p for p in people if "tử hình" in (p["sentence"] or "").casefold()]
                focus_crimes = list(dict.fromkeys(p["charge"] for p in people if p["charge"]))
            for person in people:
                aliases = f" (bí danh: {', '.join(person['aliases'])})" if person["aliases"] else ""
                parts = [f"{person['name']}{aliases} trong vụ '{names[person['case_id']]}'"]
                if person["role"] and "bị bắt" not in q:
                    parts.append(f"vai trò {person['role']}")
                if person["charge"]:
                    parts.append(f"tội {person['charge']}")
                if person["sentence"]:
                    parts.append(f"án đã tuyên {person['sentence']}")
                add("; ".join(parts) + ".")

            links = self.run(
                """MATCH (k:Case)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)
                   WHERE k.id IN $case_ids
                     AND (size($focus_crimes) = 0 OR c.name IN $focus_crimes)
                   RETURN DISTINCT k.id AS case_id, c.name AS crime, a.id AS article_id
                   ORDER BY case_id, article_id""",
                case_ids=case_ids, focus_crimes=focus_crimes,
            )
            for link in links:
                add(f"Vụ '{names[link['case_id']]}' được nêu với tội {link['crime']}; "
                    f"tội này do {link['article_id']} quy định.")

            amounts = self.run(
                """MATCH (k:Case)-[r:INVOLVES]->(s:Substance)
                   WHERE k.id IN $case_ids
                   RETURN k.id AS case_id, s.name AS substance,
                          r.amount_raw AS amount_raw, r.amount_g AS amount_g,
                          r.comparator AS comparator
                   ORDER BY case_id, substance""",
                case_ids=case_ids,
            )
            for amount in amounts:
                description = f": {amount['amount_raw']}" if amount["amount_raw"] else ""
                add(f"Vụ '{names[amount['case_id']]}' liên quan {amount['substance']}{description}.")

            clauses = self.run(
                """MATCH (k:Case)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)
                         -[:HAS_CLAUSE]->(cl:Clause)
                   WHERE k.id IN $case_ids
                     AND (size($focus_crimes) = 0 OR c.name IN $focus_crimes)
                   RETURN DISTINCT k.id AS case_id, a.id AS article_id, a.title AS title,
                          cl.id AS clause_id, cl.number AS number, cl.penalty AS penalty,
                          cl.text AS text
                   ORDER BY article_id, number""",
                case_ids=case_ids, focus_crimes=focus_crimes,
            )
            selected = {row["clause_id"] for row in clauses if row["number"] == 1 or
                        row["number"] in requested_clauses}
            details_by_clause: dict[str, list[str]] = {}
            matching_rules = self.run(
                """MATCH (k:Case)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)
                         -[:HAS_CLAUSE]->(cl:Clause)-[:HAS_RULE]->(rule:QuantityRule)
                         -[:FOR_SUBSTANCE]->(s:Substance)<-[r:INVOLVES]-(k)
                   WHERE k.id IN $case_ids AND r.amount_g IS NOT NULL
                     AND (size($focus_crimes) = 0 OR c.name IN $focus_crimes)
                     AND r.comparator IN ['=', '>'] AND rule.min_g <= r.amount_g
                     AND (rule.max_g IS NULL OR
                          (r.comparator = '=' AND r.amount_g < rule.max_g))
                   RETURN DISTINCT cl.id AS clause_id, rule.raw_text AS raw_text,
                          s.name AS substance""",
                case_ids=case_ids, focus_crimes=focus_crimes,
            )
            for rule in matching_rules:
                selected.add(rule["clause_id"])
                details_by_clause.setdefault(rule["clause_id"], []).append(rule["raw_text"])

            if wants_highest:
                by_article: dict[str, list[dict]] = {}
                for row in clauses:
                    if "tù" in (row["penalty"] or "").casefold():
                        by_article.setdefault(row["article_id"], []).append(row)
                for rows in by_article.values():
                    selected.add(max(rows, key=lambda row: row["number"])["clause_id"])

            if wants_law_detail and question_substances and not matching_rules:
                mentions = self.run(
                    """MATCH (k:Case)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)
                             -[:HAS_CLAUSE]->(cl:Clause)-[:MENTIONS]->(s:Substance)
                             <-[:INVOLVES]-(k)
                       WHERE k.id IN $case_ids AND s.name IN $substances
                         AND (size($focus_crimes) = 0 OR c.name IN $focus_crimes)
                       RETURN DISTINCT cl.id AS clause_id, s.name AS substance""",
                    case_ids=case_ids, substances=question_substances, focus_crimes=focus_crimes,
                )
                for mention in mentions:
                    selected.add(mention["clause_id"])
                    row = next((r for r in clauses if r["clause_id"] == mention["clause_id"]), None)
                    if row:
                        lines = [line.strip() for line in row["text"].splitlines()
                                 if mention["substance"].casefold() in line.casefold()]
                        details_by_clause.setdefault(row["clause_id"], []).extend(lines[:1])

            for row in clauses:
                if row["clause_id"] in selected:
                    add(clause_fact(row, list(dict.fromkeys(details_by_clause.get(row["clause_id"], [])))))

        # A question may point straight to an article, even when no news case was seeded.
        if article_numbers:
            law = ("BLHS" if "blhs" in q or "hình sự" in q else
                   "Luật PCMT" if "pcmt" in q or "phòng, chống ma túy" in q else None)
            direct = self.run(
                """MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
                   WHERE any(number IN $numbers WHERE a.id STARTS WITH ('Điều ' + number + ' '))
                     AND ($law IS NULL OR a.law = $law)
                   RETURN a.id AS article_id, a.title AS title, cl.id AS clause_id,
                          cl.number AS number, cl.penalty AS penalty, cl.text AS text
                   ORDER BY article_id, number""",
                numbers=article_numbers, law=law,
            )
            highest_direct: set[str] = set()
            if wants_highest:
                for article_id in {row["article_id"] for row in direct}:
                    imprisonment = [row for row in direct if row["article_id"] == article_id
                                    and "tù" in (row["penalty"] or "").casefold()]
                    if imprisonment:
                        highest_direct.add(max(imprisonment, key=lambda row: row["number"])["clause_id"])
            definition_rows = [row for row in direct if phrase and re.match(
                rf"^\d+\.\s*{re.escape(phrase)}\b", row["text"].casefold())]
            definition_ids = {row["clause_id"] for row in definition_rows}
            for row in direct:
                if (row["number"] == 1 or row["number"] in requested_clauses or
                        row["clause_id"] in highest_direct or row["clause_id"] in definition_ids or
                        any(s.casefold() in row["text"].casefold() for s in question_substances)):
                    details = [line.strip() for line in row["text"].splitlines()
                               if any(s.casefold() in line.casefold() for s in question_substances)]
                    add(clause_fact(row, details[:2]))

        # Law-only vector hits need their clause text, not merely Article--Clause edge labels.
        law_rows = self.run(
            """MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
               WHERE a.doc_id IN $doc_ids
               RETURN a.id AS article_id, a.title AS title, cl.id AS clause_id,
                      cl.number AS number, cl.text AS text
               ORDER BY article_id, number""",
            doc_ids=doc_ids,
        )
        if phrase:
            matches = [row for row in law_rows if phrase in row["text"].casefold()]
            exact = [row for row in matches if re.match(
                rf"^\d+\.\s*{re.escape(phrase)}\b", row["text"].casefold())]
            for row in (exact[:1] or matches[:2]):
                add(clause_fact(row))
        elif not case_ids and not article_numbers:
            terms = {term for term in re.findall(r"\w+", q) if len(term) >= 4}
            scored = sorted(law_rows, key=lambda row: len(terms & set(
                re.findall(r"\w+", row["text"].casefold()))), reverse=True)
            for row in scored[:2]:
                if terms & set(re.findall(r"\w+", row["text"].casefold())):
                    add(clause_fact(row))

        if not direct_law_only:
            for fact in one_hop:
                if "(Person:" in fact and "INVOLVED_IN" in fact:
                    continue  # The explicit person facts above include role, charge and sentence.
                if not cases or any(case["name"] in fact for case in cases):
                    add(fact)
        return facts[:max_facts]

# ---------------------------------------------------------------------------------------------- KG-2

def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                llm_fn: Callable[..., str]) -> None:
    """Load the ontology in report/ONTOLOGY.md, using regex for law and one LLM call per news doc."""
    for label, key in (
        ("Article", "id"), ("Clause", "id"), ("QuantityRule", "id"),
        ("Crime", "name"), ("Substance", "name"), ("NewsArticle", "id"),
        ("Case", "id"), ("Person", "id"), ("Location", "id"),
    ):
        graph.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    articles = [parse_law_article(doc) for doc in law_docs]
    known_crimes = list(dict.fromkeys(article["crime"] for article in articles if article["crime"]))
    for article in articles:
        graph.run(
            """MERGE (a:Article {id: $id})
               SET a.title = $title, a.law = $law, a.doc_id = $doc_id""",
            id=article["id"], title=article["title"], law=article["law"], doc_id=article["doc_id"],
        )
        if article["crime"]:
            graph.run(
                """MATCH (a:Article {id: $article_id})
                   MERGE (c:Crime {name: $crime})
                   SET c.source_doc_ids = CASE WHEN $doc_id IN coalesce(c.source_doc_ids, [])
                       THEN c.source_doc_ids ELSE coalesce(c.source_doc_ids, []) + $doc_id END
                   MERGE (a)-[:DEFINES]->(c)""",
                article_id=article["id"], crime=article["crime"], doc_id=article["doc_id"],
            )
        for clause in article["clauses"]:
            graph.run(
                """MATCH (a:Article {id: $article_id})
                   MERGE (cl:Clause {id: $id})
                   SET cl.number = $number, cl.penalty = $penalty,
                       cl.text = $text, cl.doc_id = $doc_id
                   MERGE (a)-[:HAS_CLAUSE]->(cl)""",
                article_id=article["id"], doc_id=article["doc_id"],
                id=clause["id"], number=clause["number"],
                penalty=clause["penalty"], text=clause["text"],
            )
            for substance in clause["substances"]:
                graph.run(
                    """MATCH (cl:Clause {id: $clause_id})
                       MERGE (s:Substance {name: $name})
                       SET s.source_doc_ids = CASE WHEN $doc_id IN coalesce(s.source_doc_ids, [])
                           THEN s.source_doc_ids ELSE coalesce(s.source_doc_ids, []) + $doc_id END
                       MERGE (cl)-[:MENTIONS]->(s)""",
                    clause_id=clause["id"], name=substance, doc_id=article["doc_id"],
                )
            for rule in extract_quantity_rules(clause):
                graph.run(
                    """MATCH (cl:Clause {id: $clause_id})
                       MERGE (q:QuantityRule {id: $id})
                       SET q.point = $point, q.min_g = $min_g, q.max_g = $max_g,
                           q.min_inclusive = $min_inclusive, q.max_inclusive = $max_inclusive,
                           q.raw_text = $raw_text, q.doc_id = $doc_id
                       MERGE (cl)-[:HAS_RULE]->(q)
                       MERGE (s:Substance {name: $substance})
                       SET s.source_doc_ids = CASE WHEN $doc_id IN coalesce(s.source_doc_ids, [])
                           THEN s.source_doc_ids ELSE coalesce(s.source_doc_ids, []) + $doc_id END
                       MERGE (q)-[:FOR_SUBSTANCE]->(s)""",
                    **rule, doc_id=article["doc_id"],
                )

    for doc in news_docs:
        graph.run(
            """MERGE (n:NewsArticle {id: $doc_id})
               SET n.doc_id = $doc_id, n.title = $title, n.source_url = $source_url""",
            doc_id=doc.id, title=doc.metadata.get("title", ""),
            source_url=doc.metadata.get("source_url", ""),
        )
        for index, case in enumerate(extract_news_cases(doc, llm_fn, known_crimes), start=1):
            case_id = f"{doc.id}:case:{index}"
            graph.run(
                """MATCH (n:NewsArticle {id: $doc_id})
                   MERGE (k:Case {id: $id})
                   SET k.name = $name, k.summary = $summary, k.date = $date,
                       k.source_title = $source_title, k.doc_id = $doc_id
                   MERGE (n)-[:REPORTS]->(k)""",
                id=case_id, doc_id=doc.id, name=case["name"], summary=case["summary"],
                date=case["date"], source_title=doc.metadata.get("title", ""),
            )
            for crime in case["charges"]:
                graph.run(
                    """MATCH (k:Case {id: $case_id})
                       MERGE (c:Crime {name: $crime})
                       SET c.source_doc_ids = CASE WHEN $doc_id IN coalesce(c.source_doc_ids, [])
                           THEN c.source_doc_ids ELSE coalesce(c.source_doc_ids, []) + $doc_id END
                       MERGE (k)-[:CHARGED_WITH]->(c)""",
                    case_id=case_id, crime=crime, doc_id=doc.id,
                )
            for item in case["substances"]:
                amount_g, comparator = parse_amount(item["amount_raw"])
                graph.run(
                    """MATCH (k:Case {id: $case_id})
                       MERGE (s:Substance {name: $name})
                       SET s.source_doc_ids = CASE WHEN $doc_id IN coalesce(s.source_doc_ids, [])
                           THEN s.source_doc_ids ELSE coalesce(s.source_doc_ids, []) + $doc_id END
                       MERGE (k)-[r:INVOLVES]->(s)
                       SET r.amount_raw = $amount_raw, r.amount_g = $amount_g,
                           r.comparator = $comparator""",
                    case_id=case_id, name=item["name"], doc_id=doc.id,
                    amount_raw=item["amount_raw"], amount_g=amount_g, comparator=comparator,
                )
            if case["location"]:
                location = case["location"]
                location_key = re.sub(r"\s+", " ", location.casefold())
                graph.run(
                    """MATCH (k:Case {id: $case_id})
                       MERGE (l:Location {id: $id})
                       SET l.name = $name, l.doc_id = $doc_id
                       MERGE (k)-[:LOCATED_IN]->(l)""",
                    case_id=case_id, id=f"{doc.id}:location:{location_key}",
                    name=location, doc_id=doc.id,
                )
            for person in case["people"]:
                person_key = re.sub(r"\s+", " ", person["name"].casefold())
                graph.run(
                    """MATCH (k:Case {id: $case_id})
                       MERGE (p:Person {id: $id})
                       SET p.name = $name, p.aliases = $aliases, p.doc_id = $doc_id
                       MERGE (p)-[r:INVOLVED_IN]->(k)
                       SET r.role = $role, r.charge = $charge, r.sentence = $sentence""",
                    case_id=case_id, id=f"{doc.id}:person:{person_key}", doc_id=doc.id,
                    **person,
                )

# ---------------------------------------------------------------------------------------------- KG-4

GRAPH_PROMPT = """Trả lời câu hỏi chỉ dựa trên ngữ cảnh (đoạn văn bản và dữ kiện từ knowledge graph).
Nêu rõ số Điều luật khi có. Nếu ngữ cảnh không đủ, nói không đủ thông tin.

Dữ kiện knowledge graph:
{facts}

Đoạn văn bản:
{chunks}

Câu hỏi: {question}
Trả lời:"""

class GraphRAGAgent:
    """Hybrid GraphRAG: the same vector top-k as flat RAG, plus facts expanded from the graph."""

    def __init__(self, store: EmbeddingStore, graph: Neo4jGraph, llm_fn: Callable[[str], str]) -> None:
        self.store = store
        self.graph = graph
        self.llm_fn = llm_fn

    def answer(self, question: str, top_k: int = 3) -> str:
        chunks = self.store.search(question, top_k=top_k)
        doc_ids = list(dict.fromkeys(chunk["metadata"]["doc_id"] for chunk in chunks))
        facts = self.graph.context(question, doc_ids)
        prompt = GRAPH_PROMPT.format(
            facts="\n".join(f"- {fact}" for fact in facts),
            chunks="\n\n".join(f"[{i}] {chunk['content']}" for i, chunk in enumerate(chunks, start=1)),
            question=question,
        )
        return self.llm_fn(prompt)
