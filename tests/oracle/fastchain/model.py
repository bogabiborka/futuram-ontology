# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""The data model: Node and Stmt (one constituent / one composition statement)."""
from __future__ import annotations

from dataclasses import dataclass, field

from .vocab import EX, KGKG, SCALE_TO_KGKG, DIST_KINDS, LEVEL_RANK

@dataclass
class Node:
    """A constituent individual in the supply chain."""
    name: str          # local name, becomes ex:<name>
    level: str         # one of LEVELS
    cls: str           # futuram hierarchy class local-name (e.g. 'elvElectricMotor')
    # absolute item mass in kg (futuram:itemMass) — reference anchor for solving
    # kg/kg into absolute amounts. REQUIRED on Product/Component; None for
    # Material/Element (bulk matter).
    item_mass: float = None

    @property
    def iri(self):
        return EX[self.name]


@dataclass
class Stmt:
    """A composition statement: `whole` composed of `part` as a [lo, hi] mass
    fraction in `unit` (may skip levels). `best` = REQUIRED authored centre;
    `lo`/`hi` OPTIONAL (default to best); `dist`/`dist_params` = sampling shape."""
    whole: str         # Node name
    part: str          # Node name
    best: float        # REQUIRED central value (authored)
    lo: float = None   # optional lower endpoint (defaults to best)
    hi: float = None   # optional upper endpoint (defaults to best)
    unit: object = KGKG     # KGKG or GKG
    dist: str = "triangular"          # a key of DIST_KINDS
    dist_params: dict = field(default_factory=dict)  # extra/override params
    # data-quality scores (DQV dimension localname -> 0..3 score), authored
    # per statement, e.g. {"Accuracy": 2.0, "Completeness": 3.0}.
    quality: dict = field(default_factory=dict)
    # whole/part LEVEL names, stamped by the owning SupplyChain._bind_levels so
    # level-skip classification reads THIS chain's nodes, never a shared global
    # (which would leak across scenarios sharing element names like "cu").
    whole_level: str = None
    part_level: str = None

    def __post_init__(self):
        # lo/hi are the optional spread; absent => point statement at best
        if self.lo is None:
            self.lo = self.best
        if self.hi is None:
            self.hi = self.best

    @property
    def lo_kgkg(self):
        return self.lo * SCALE_TO_KGKG[self.unit]

    @property
    def hi_kgkg(self):
        return self.hi * SCALE_TO_KGKG[self.unit]

    @property
    def best_kgkg(self):
        return float(self.best) * SCALE_TO_KGKG[self.unit]

    @property
    def has_hard_lower_bound(self):
        """True iff the kind has a hard lower bound at the interval minimum
        (triangular/uniform/rectangular). Centre-based kinds (normal, ...) do NOT,
        so their lightest credible mass for an overshoot check is the best value."""
        return self.dist in ("triangular", "uniform", "rectangular")

    @property
    def floor_kgkg(self):
        """The lightest credible fraction (kg/kg) this statement contributes to a
        necessary-overshoot check: the interval minimum for bounded kinds, else
        the best value. Mirrors the SPARQL rule's COALESCE(min, bestValue)."""
        return self.lo_kgkg if self.has_hard_lower_bound else self.best_kgkg

    def distribution(self):
        """Resolve to (FUT-class-localname, {param: value}). Triangular bounds
        default to the statement's lo/hi; all other params come from
        dist_params. Raises if a required param is missing."""
        if self.dist not in DIST_KINDS:
            raise ValueError(f"unknown distribution kind {self.dist!r} "
                             f"(known: {sorted(DIST_KINDS)})")
        cls, required = DIST_KINDS[self.dist]
        params = dict(self.dist_params)
        if self.dist in ("triangular", "uniform"):
            params.setdefault("lowerBound", float(self.lo))
            params.setdefault("upperBound", float(self.hi))
        missing = [p for p in required if p not in params]
        if missing:
            raise ValueError(
                f"distribution {self.dist!r} on {self.whole}->{self.part} "
                f"is missing required parameter(s) {missing}")
        return cls, {p: float(params[p]) for p in required}

    @property
    def levels_skipped(self):
        # prefer the levels stamped by this statement's own chain; fall back to
        # the per-bind global only if unstamped (kept for ad-hoc Stmt use).
        wl = self.whole_level or _NODES_BY_NAME[self.whole].level
        pl = self.part_level or _NODES_BY_NAME[self.part].level
        return abs(LEVEL_RANK[pl] - LEVEL_RANK[wl]) > 1


# populated per-SupplyChain so Stmt can see node levels
_NODES_BY_NAME: dict[str, Node] = {}
