"""Run the hint benchmark and save graph-level bonus evidence on an isolated Neo4j.

Start the local comparison container first (commands in baselines/hint/README.md).
This uses the unmodified bench_kg.py through its LAB_SOLUTION_PACKAGE extension.
Only the comparison database is reset; the submitted graph is queried read-only.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from baselines.hint import graph as hint
from src import Document
from src import graph as solution


MASS_QUERY = """MATCH (k:Case)-[r:INVOLVES]->(s:Substance {name:'MDMA'})
MATCH (k)-[:CHARGED_WITH]->(c:Crime)<-[:DEFINES]-(a:Article)-[:HAS_CLAUSE]->(cl:Clause)
MATCH (cl)-[:HAS_RULE]->(qr:QuantityRule)-[:FOR_SUBSTANCE]->(s)
WHERE k.doc_id = $news_doc AND a.id = $article_id AND cl.number = 4
  AND qr.max_g IS NULL AND qr.min_inclusive = true
  AND r.comparator IN ['=', '>', '>='] AND r.amount_g >= qr.min_g
RETURN k.name AS case_name, k.doc_id AS doc_id, a.id AS article,
       cl.number AS clause, qr.point AS point, qr.min_g AS threshold_g,
       r.amount_g AS amount_g, r.comparator AS comparator, cl.penalty AS penalty
ORDER BY case_name"""

IDENTITY_QUERY = """MATCH (p:Person)-[r:INVOLVED_IN]->(k:Case)
RETURN k.doc_id AS doc_id, k.name AS case_name, p.name AS person,
       p.aliases AS aliases, r.sentence AS sentence
ORDER BY doc_id"""


def isolated_env(uri: str) -> dict[str, str]:
    source_uri = os.getenv('NEO4J_URI', 'bolt://localhost:7687')
    target, source = urlparse(uri), urlparse(source_uri)
    if target.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('The bonus database must be local.')
    if target.port is None or target.port == source.port:
        raise ValueError('Use a separate port for the bonus database; it will be reset.')
    env = dict(os.environ)
    env.update(NEO4J_URI=uri, NEO4J_USER='neo4j',
               NEO4J_PASSWORD=os.getenv('BONUS_NEO4J_PASSWORD', 'bonus_lab_local'),
               PYTHONIOENCODING='utf-8')
    return env


def run_check(env: dict[str, str]) -> None:
    env = {**env, 'LAB_SOLUTION_PACKAGE': 'src'}
    result = subprocess.run([sys.executable, '-X', 'utf8', 'bench_kg.py', '--check'],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding='utf-8', check=True)
    (ROOT / 'report/bonus_check.txt').write_text(result.stdout, encoding='utf-8')
    print(result.stdout, end='', flush=True)


def run_benchmark(env: dict[str, str]) -> None:
    env = {**env, 'LAB_SOLUTION_PACKAGE': 'baselines.hint'}
    subprocess.run([sys.executable, '-u', '-X', 'utf8', 'bench_kg.py', '--judge',
                    '--out', 'ket_qua_benchmark_kg.hint.txt'], cwd=ROOT, env=env, check=True)


def fixture_payload(prompt: str, **kwargs) -> str:
    """Controlled synthetic inputs: two distinct people/cases with equal display names."""
    first = 'SYNTHETIC_SOURCE_A' in prompt
    return json.dumps({'cases': [{
        'name': 'Vụ mua bán trái phép chất ma túy',
        'summary': 'Tình huống tổng hợp A' if first else 'Tình huống tổng hợp B',
        'date': '2025-01-01' if first else '2025-02-01',
        'location': 'Hà Nội' if first else 'Đà Nẵng',
        'charges': ['mua bán trái phép chất ma túy'], 'substances': [],
        'people': [{'name': 'Nguyễn Văn A', 'aliases': ['người A' if first else 'người B'],
                    'role': 'bị cáo', 'charge': 'mua bán trái phép chất ma túy',
                    'sentence': '2 năm tù' if first else '7 năm tù'}],
    }]}, ensure_ascii=False)


def controlled_identity(graph: solution.Neo4jGraph, builder) -> dict:
    graph.reset()
    laws = [d for d in solution.load_markdown_docs(ROOT / 'data/drug_law')
            if d.id == 'blhs-dieu-251']
    if len(laws) != 1:
        raise RuntimeError('The fixture requires the actual Điều 251 law document.')
    news = [Document(f'bonus-fixture-{suffix}', f'SYNTHETIC_SOURCE_{marker}',
                     {'title': f'Tình huống tổng hợp {marker}'})
            for suffix, marker in [('a', 'A'), ('b', 'B')]]
    builder(graph, laws, news, fixture_payload)
    return {'rows': graph.run(IDENTITY_QUERY),
            'counts': graph.run('MATCH (n) WHERE n:Case OR n:Person '
                                'RETURN labels(n)[0] AS label,count(n) AS n ORDER BY label')}


def save_evidence(env: dict[str, str]) -> None:
    submitted = solution.Neo4jGraph(os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
                                  os.getenv('NEO4J_USER', 'neo4j'),
                                  os.getenv('NEO4J_PASSWORD', 'password123'))
    baseline = hint.Neo4jGraph(env['NEO4J_URI'], env['NEO4J_USER'], env['NEO4J_PASSWORD'])
    try:
        params = {'news_doc': 'news-100260917203001265', 'article_id': 'Điều 250 BLHS'}
        evidence = {
            'method': 'Real-corpus queries before fixture resets. Submitted graph is read-only. '
                      'Identity fixture uses fixed synthetic JSON, not LLM extraction.',
            'hint_origin': '859e8d1:src/graph.py HINT helpers + LAB_GUIDE.md steps 4/5',
            'full_graph_stats': {'hint': baseline.stats(), 'submitted': submitted.stats()},
            'mass_competency_question': 'Vụ Cái Quang Huy có vượt ngưỡng MDMA của khoản 4 '
                                       'Điều 250 không? Trả về số gam và điểm/khoản bằng Cypher.',
            'mass_query': MASS_QUERY, 'mass_params': params,
            'mass_results': {'hint': baseline.run(MASS_QUERY, **params),
                             'submitted': submitted.run(MASS_QUERY, **params)},
            'hint_raw_amount': baseline.run(
                "MATCH (k:Case)-[r:INVOLVES]->(:Substance {name:'MDMA'}) "
                'WHERE k.doc_id=$news_doc RETURN k.name AS name,r.amount AS amount', **params),
            'hint_has_law_clause': baseline.run(
                'MATCH (a:Article {id:$article_id})-[:HAS_CLAUSE]->(cl:Clause {number:4}) '
                'RETURN a.id AS article,cl.number AS number,cl.penalty AS penalty', **params),
            'identity_fixture': {
                'notice': 'Synthetic fixture, not news evidence: two different people/cases '
                          'in two documents intentionally share display names.',
                'query': IDENTITY_QUERY,
            },
        }
        # The baseline corpus has already been recorded above; only the isolated DB changes here.
        evidence['identity_fixture']['hint'] = controlled_identity(baseline, hint.build_graph)
        evidence['identity_fixture']['submitted'] = controlled_identity(baseline, solution.build_graph)
        path = ROOT / 'report/bonus_evidence.json'
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Saved {path.relative_to(ROOT)}', flush=True)
        print(json.dumps(evidence['mass_results'], ensure_ascii=False), flush=True)
        print(json.dumps(evidence['identity_fixture'], ensure_ascii=False), flush=True)
    finally:
        baseline.close()
        submitted.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['all', 'check', 'benchmark', 'evidence'], default='all')
    parser.add_argument('--uri', default='bolt://localhost:17687')
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    env = isolated_env(args.uri)
    if args.stage in {'all', 'check'}:
        run_check(env)
    if args.stage in {'all', 'benchmark'}:
        run_benchmark(env)
    if args.stage in {'all', 'evidence'}:
        save_evidence(env)


if __name__ == '__main__':
    main()
