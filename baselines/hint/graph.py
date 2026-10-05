"""Suggested ontology baseline, based on starter commit 859e8d1.

The HINT extraction prompt and write queries below retain the starter's name keys.
KG-1 and KG-4 share the submitted implementation. KG-2 calls the HINT helpers and
KG-3 implements the clause-1/substance traversal in LAB_GUIDE.md, steps 4 and 5.
The extraction call uses json_mode=True as required by the lab's LLM contract.
This baseline has no QuantityRule or per-document Person/Case identity layer.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from src.graph import (
    GraphRAGAgent, Neo4jGraph as BaseGraph, SUBSTANCES, find_substances,
    link_entity, load_markdown_docs, normalize_crime, parse_law_article,
)
from src.models import Document


NEWS_EXTRACTION_PROMPT = """Bạn trích xuất knowledge graph từ một bài báo tiếng Việt về ma túy.
Chỉ dùng thông tin có trong bài. Trả về JSON đúng dạng:
{{"cases": [{{
  "name": "tên ngắn của vụ việc, ví dụ: Vụ mua bán 36kg ma túy tại TP.HCM",
  "summary": "1-2 câu tóm tắt",
  "date": "ngày xảy ra/xét xử nếu có, dạng YYYY-MM-DD hoặc chuỗi rỗng",
  "location": "tỉnh/thành phố, chuỗi rỗng nếu không rõ",
  "charges": ["tội danh, BẮT BUỘC chọn đúng nguyên văn từ DANH SÁCH TỘI DANH"],
  "substances": [{{"name": "tên chất, dùng tên chuẩn trong DANH SÁCH CHẤT nếu khớp", "amount": "khối lượng nếu có"}}],
  "people": [{{"name": "họ tên", "aliases": ["biệt danh"], "role": "bị cáo|bị can|nghi phạm|người liên quan|cán bộ",
               "charge": "tội danh của người này (từ DANH SÁCH TỘI DANH) hoặc chuỗi rỗng",
               "sentence": "mức án nếu có, ví dụ: tử hình, 8 năm tù"}}]
}}]}}
Bài không nói về vụ việc cụ thể (tuyên truyền, hội nghị...) thì trả về {{"cases": []}}.

DANH SÁCH TỘI DANH: {crimes}
DANH SÁCH CHẤT: {substances}

Tiêu đề: {title}
Nội dung:
{content}"""


def extract_news_cases(doc: Document, llm_fn: Callable[..., str], known_crimes: list[str]) -> list[dict]:
    prompt = NEWS_EXTRACTION_PROMPT.format(
        crimes="; ".join(known_crimes), substances=", ".join(SUBSTANCES),
        title=doc.metadata.get("title", ""), content=doc.content[:12000],
    )
    try:
        cases = json.loads(llm_fn(prompt, json_mode=True)).get("cases", [])
    except (json.JSONDecodeError, AttributeError):
        return []
    for case in cases:
        case["charges"] = sorted({c for c in (link_entity(x, known_crimes) for x in case.get("charges", [])) if c})
        for person in case.get("people", []):
            person["charge"] = link_entity(person.get("charge") or "", known_crimes) or ""
    return cases


class Neo4jGraph(BaseGraph):
    def suggested_constraints(self) -> None:
        for label, key in [("Article", "id"), ("Clause", "id"), ("Crime", "name"), ("Case", "name"),
                           ("Substance", "name"), ("Person", "name"), ("Location", "name")]:
            self.run(f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{key} IS UNIQUE")

    def add_law_article(self, article: dict) -> None:
        self.run(
            """MERGE (a:Article {id: $id}) SET a.title = $title, a.law = $law, a.doc_id = $doc_id
            FOREACH (crime IN CASE WHEN $crime IS NULL THEN [] ELSE [$crime] END |
                MERGE (c:Crime {name: crime}) MERGE (a)-[:DEFINES]->(c))
            WITH a
            UNWIND $clauses AS clause
            MERGE (cl:Clause {id: clause.id})
              SET cl.number = clause.number, cl.penalty = clause.penalty, cl.text = clause.text, cl.doc_id = $doc_id
            MERGE (a)-[:HAS_CLAUSE]->(cl)
            FOREACH (s IN clause.substances | MERGE (sub:Substance {name: s}) MERGE (cl)-[:MENTIONS]->(sub))""",
            **article,
        )

    def add_news_case(self, case: dict, doc: Document) -> None:
        self.run(
            """MERGE (k:Case {name: $name})
              SET k.summary = $summary, k.date = $date, k.doc_id = $doc_id, k.source_title = $title
            FOREACH (loc IN CASE WHEN $location = '' THEN [] ELSE [$location] END |
                MERGE (l:Location {name: loc}) MERGE (k)-[:LOCATED_IN]->(l))
            FOREACH (crime IN $charges | MERGE (c:Crime {name: crime}) MERGE (k)-[:CHARGED_WITH]->(c))
            FOREACH (s IN $substances | MERGE (sub:Substance {name: s.name}) MERGE (k)-[r:INVOLVES]->(sub)
                SET r.amount = s.amount)
            FOREACH (p IN $people | MERGE (person:Person {name: p.name})
                SET person.aliases = coalesce(p.aliases, [])
                MERGE (person)-[r:INVOLVED_IN]->(k) SET r.role = p.role, r.charge = p.charge, r.sentence = p.sentence)""",
            name=case.get("name") or doc.metadata.get("title", doc.id),
            summary=case.get("summary", ""), date=case.get("date", ""), location=case.get("location", ""),
            charges=case.get("charges", []), people=[p for p in case.get("people", []) if p.get("name")],
            substances=[s for s in case.get("substances", []) if s.get("name")],
            doc_id=doc.id, title=doc.metadata.get("title", ""),
        )

    def context(self, question: str, doc_ids: list[str], max_facts: int = 60) -> list[str]:
        seed_ids, facts = self.seed_facts(question, doc_ids)
        cases = self.run(
            """MATCH (k:Case)
               WHERE elementId(k) IN $ids
                  OR EXISTS { MATCH (s)--(k) WHERE elementId(s) IN $ids }
               RETURN elementId(k) AS id, k.name AS name, k.summary AS summary
               ORDER BY k.name""", ids=seed_ids,
        )
        for case in cases:
            facts.append(f"Vụ việc '{case['name']}': {case['summary']}")
        clauses = self.run(
            """MATCH (k:Case)-[:CHARGED_WITH]->(:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
               WHERE elementId(k) IN $ids
                 AND (cl.number = 1 OR EXISTS {
                   MATCH (k)-[:INVOLVES]->(:Substance)<-[:MENTIONS]-(cl)
                 })
               RETURN DISTINCT a.id AS article_id, a.title AS title, cl.number AS number, cl.text AS text
               ORDER BY article_id, number""", ids=[c["id"] for c in cases],
        )
        numbers = re.findall(r"[Đđ]iều\s+(\d+)", question)
        if numbers:
            clauses += self.run(
                """MATCH (a:Article)-[:HAS_CLAUSE]->(cl:Clause)
                   WHERE any(number IN $numbers WHERE a.id STARTS WITH 'Điều ' + number + ' ')
                     AND (cl.number = 1 OR EXISTS {
                       MATCH (cl)-[:MENTIONS]->(s:Substance) WHERE s.name IN $substances
                     })
                   RETURN DISTINCT a.id AS article_id, a.title AS title, cl.number AS number, cl.text AS text
                   ORDER BY article_id, number""", numbers=numbers, substances=find_substances(question),
            )
        for clause in clauses:
            facts.append(f"[{clause['article_id']} - {clause['title']}] khoản {clause['number']}: {clause['text']}")
        return list(dict.fromkeys(facts))[:max_facts]


def build_graph(graph: Neo4jGraph, law_docs: list[Document], news_docs: list[Document],
                llm_fn: Callable[..., str]) -> None:
    graph.suggested_constraints()
    articles = [parse_law_article(doc) for doc in law_docs]
    crimes = list(dict.fromkeys(a["crime"] for a in articles if a["crime"]))
    for article in articles:
        graph.add_law_article(article)
    for doc in news_docs:
        for case in extract_news_cases(doc, llm_fn, crimes):
            graph.add_news_case(case, doc)
