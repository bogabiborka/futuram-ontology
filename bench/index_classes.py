"""Index every served CLASS by its rdfs:label + rdfs:comment into a Qdrant
collection, so the bench can offer SEMANTIC class-candidate suggestions (the
`find_candidate_classes` MCP tool) — turning "which class means 'diesel passenger
car'?" into a vector lookup instead of dozens of blind SPARQL probes.

Leak-safety: the payload is ONLY the class IRI + its label + its comment. Class
labels/comments carry NO numeric values (verified: 0/8035 contain a number), so a
candidate suggestion reveals a class name (legitimate resolution) but never a data
value or golden answer.

Run inside the bench network (reaches Fuseki + Qdrant by service name):
    uv run bench/index_classes.py
or against explicit endpoints with --fq-endpoint / --vectordb-url.
"""
from __future__ import annotations

import argparse
import os
import sys

CLASS_COLLECTION = os.getenv("BENCH_CLASS_COLLECTION", "futuram_classes")

# term per class = label + comment (what the model would search); the IRI is opaque
# and deliberately NOT embedded (we never want a hit on the random identifier).
#
# SCOPE: ONLY the domain taxonomy — classes that are subclasses of one of the four
# kinds futuram:Product / Component / Material / Element. This deliberately EXCLUDES
# ChEBI classes, schema/vocabulary classes (Amount, AggregationStrategy, …), and
# anything outside the futuram namespace, so the candidate list is only the classes a
# composition question can be ABOUT.
_QUERY = """
PREFIX rdfs:    <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl:     <http://www.w3.org/2002/07/owl#>
PREFIX futuram: <https://www.purl.org/futuram#>
SELECT DISTINCT ?cls ?label ?comment WHERE {
  VALUES ?root { futuram:Product futuram:Component futuram:Material futuram:Element }
  ?cls rdfs:subClassOf* ?root .
  FILTER(?cls != ?root)
  FILTER(STRSTARTS(STR(?cls), "https://www.purl.org/futuram#"))   # futuram domain only — no ChEBI/external
  OPTIONAL { ?cls rdfs:label ?label }
  OPTIONAL { ?cls rdfs:comment ?comment }
}
"""


def _fetch_classes(endpoint: str) -> list[dict]:
    from SPARQLWrapper import SPARQLWrapper, JSON
    w = SPARQLWrapper(endpoint)
    w.setQuery(_QUERY)
    w.setReturnFormat(JSON)
    rows = w.query().convert()["results"]["bindings"]
    by_iri: dict[str, dict] = {}
    for r in rows:
        iri = r["cls"]["value"]
        d = by_iri.setdefault(iri, {"iri": iri, "label": "", "comment": ""})
        if r.get("label"):
            d["label"] = r["label"]["value"]
        if r.get("comment"):
            d["comment"] = r["comment"]["value"]
    # only classes with SOME human text are useful as semantic candidates
    return [d for d in by_iri.values() if (d["label"] or d["comment"])]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fq-endpoint",
                    default=os.getenv("BENCH_FQ_ENDPOINT",
                                      "http://bench-fuseki:3030/query/sparql"))
    ap.add_argument("--composition-endpoint",
                    default=os.getenv("BENCH_COMPOSITION_ENDPOINT",
                                      "http://bench-fuseki:3030/composition/sparql"))
    ap.add_argument("--vectordb-url",
                    default=os.getenv("VECTORDB_URL", "http://bench-vectordb:6333"))
    ap.add_argument("--collection", default=CLASS_COLLECTION)
    args = ap.parse_args()

    # reuse the SAME embedding model the rest of the bench RAG uses, so vectors are
    # comparable and the dimension matches the existing collections.
    from sparql_llm.mcp_server import embedding_model
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct

    client = QdrantClient(url=args.vectordb_url)

    endpoints = {"fq": args.fq_endpoint, "composition": args.composition_endpoint}
    docs: list[dict] = []
    for backend, ep in endpoints.items():
        try:
            cls = _fetch_classes(ep)
        except Exception as e:  # noqa: BLE001
            print(f"[index_classes] WARN {backend} ({ep}): {e}", file=sys.stderr)
            continue
        for c in cls:
            c["backend"] = backend
            c["endpoint_url"] = ep
        docs.extend(cls)
        print(f"[index_classes] {backend}: {len(cls)} classes with label/comment")

    if not docs:
        print("[index_classes] no classes fetched — nothing to index", file=sys.stderr)
        return 1

    texts = [f"{d['label']}. {d['comment']}".strip(". ") for d in docs]
    vectors = list(embedding_model.embed(texts))
    dim = len(vectors[0])

    client.recreate_collection(
        collection_name=args.collection,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE))
    client.upsert(
        collection_name=args.collection,
        points=[PointStruct(id=i, vector=v, payload={
            "iri": d["iri"], "label": d["label"], "comment": d["comment"],
            "backend": d["backend"], "endpoint_url": d["endpoint_url"],
        }) for i, (d, v) in enumerate(zip(docs, vectors))])
    print(f"[index_classes] indexed {len(docs)} classes into "
          f"'{args.collection}' (dim {dim})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
