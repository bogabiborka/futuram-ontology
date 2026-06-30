# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""Smoke test: a tiny motor->cu chain, show ground truth."""
from .chain import SupplyChain

sc = SupplyChain("smoke")
# to_graph requires the scenario-level provenance block (as every YAML authors)
sc.provenance = {"source": "smoke", "agent": "smoke", "production": "smoke",
                 "validFrom": "2020-01-01"}
sc.node("motor", "Component", "elvElectricMotor")
sc.node("cu", "Material", "pureCu")
sc.node("cuel", "Element", "Copper")
sc.stmt("motor", "cu", best=0.15, lo=0.14, hi=0.16)
sc.stmt("cu", "cuel", best=0.995, lo=0.99, hi=1.0)
print("conservation:", dict(sc.conservation()))
print("coarse_fine:", sc.coarse_fine())
# full_metadata needs per-statement conf/repr (the YAMLs author them); the
# smoke chain has none, so emit the bare structural graph
print(f"graph triples: {len(sc.to_graph(full_metadata=False))}")
