# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "requests"]
# ///
"""fuseki_client — thin SPARQL client over the Fuseki `futuram` datasource (base
http://localhost:3031, FUSEKI_FUTURAM_BASE), which serves <base>/composition
(baseline composition futuram: dataset) and <base>/query (query-optimized fq: dataset);
plus helpers for "how much of E in X".
"""
import os

import requests

# The one served datasource (docker-compose service `futuram`): the real ELV fleet.
INSTANCES = {
    "futuram": os.environ.get("FUSEKI_FUTURAM_BASE", "http://localhost:3031"),
}
FUSEKI_BASE = os.environ.get("FUSEKI_BASE", INSTANCES["futuram"])
FUT = "https://www.purl.org/futuram#"
FQ = "https://www.purl.org/futuram/query#"

PREFIXES = f"""
PREFIX futuram: <{FUT}>
PREFIX fq: <{FQ}>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
"""


class FusekiClient:
    """SPARQL client for one Fuseki dataset (default the fq: /query dataset)."""

    def __init__(self, dataset="query", base=None, timeout=30):
        self.base = (base or FUSEKI_BASE).rstrip("/")
        self.dataset = dataset
        self.timeout = timeout

    @property
    def endpoint(self):
        return f"{self.base}/{self.dataset}/sparql"

    def query(self, sparql, with_prefixes=True):
        """Run a SELECT/ASK; return a list of dict rows (var -> value string)."""
        q = (PREFIXES + sparql) if with_prefixes else sparql
        resp = requests.post(
            self.endpoint,
            data={"query": q},
            headers={"Accept": "application/sparql-results+json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if "boolean" in data:                       # ASK
            return data["boolean"]
        out = []
        for b in data["results"]["bindings"]:
            out.append({k: v["value"] for k, v in b.items()})
        return out

    # -- convenience over the fq: pattern. Constituent KIND is declared via
    # `<constituent> rdfs:subClassOf futuram:<Level>` (not a flat fq:level marker),
    # so asking at the wrong level returns nothing (levels are AllDisjointClasses).
    def how_much(self, element, in_class, level="Element"):
        """How much of `element` (or component/material) is in class `in_class`.
        Returns a list of {amount, low, high} rows (one per source graph
        carrying that class)."""
        rows = self.query(f"""
            SELECT ?amount ?low ?high WHERE {{
              futuram:{in_class} fq:contains ?a .
              ?a fq:constituent futuram:{element} ;
                 fq:amount ?amount .
              futuram:{element} rdfs:subClassOf futuram:{level} .
              OPTIONAL {{ ?a fq:amountLow ?low }}
              OPTIONAL {{ ?a fq:amountHigh ?high }}
            }}""")
        return rows

    def constituents_of(self, in_class, level="Element"):
        """Every constituent of `level` in `in_class` with its amount."""
        return self.query(f"""
            SELECT ?element ?amount WHERE {{
              futuram:{in_class} fq:contains ?a .
              ?a fq:constituent ?element ; fq:amount ?amount .
              ?element rdfs:subClassOf futuram:{level} .
            }} ORDER BY DESC(?amount)""")

    def ping(self):
        """True if the dataset answers a trivial query."""
        try:
            self.query("SELECT * WHERE { ?s ?p ?o } LIMIT 1")
            return True
        except Exception:                           # noqa: BLE001
            return False
