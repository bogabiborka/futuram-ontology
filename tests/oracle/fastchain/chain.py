# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""SupplyChain — the class, assembled from the per-concern mixins. The core
here is construction only (__init__, YAML loader, node/stmt API); everything
else lives in a mixin named after its concern."""
from __future__ import annotations

import pathlib

from .hierarchy import chain_superclasses
from .model import Node, Stmt
from .vocab import KGKG, UNIT_BY_NAME
from .validation import ValidationMixin
from .groundtruth import GroundTruthMixin
from .aggregation import AggregationMixin
from .materials import MaterialFamilyMixin
from .montecarlo import MonteCarloMixin
from .serialize import SerializationMixin


class SupplyChain(ValidationMixin, GroundTruthMixin, AggregationMixin,
                  MaterialFamilyMixin, MonteCarloMixin, SerializationMixin):
    def __init__(self, label: str, id: str = ""):
        self.label = label
        self.id = id
        self.nodes: dict[str, Node] = {}
        self.stmts: list[Stmt] = []
        # instance-root names that stand for a whole CLASS of instances, for
        # class-level aggregation across several instances of the SAME class.
        # Empty for the single-instance scenarios.
        self.instances: dict[str, float] = {}
        # scenario-level provenance/validity/production shared by all statements.
        self.provenance: dict = {}
        # extra class -> superclass edges the local hierarchy lacks (YAML's
        # subclass_of); emitted as rdfs:subClassOf. Values are LISTS of names
        # (a time slice has its base AND the matching ancestor slice).
        self.subclass_of: dict = {}
        # cache for the derived per-chain superclass map (rebuilt when
        # subclass_of changes — see the `superclasses` property). Never global.
        self._superclasses_cache = None
        self._superclasses_key = None
        # the TIME REGISTRY: class -> {"year": int} XOR {"start","end": int}, plus
        # optional "slices"/"strategy" (STRATEGY_IRI token). P/C classes carry
        # aggregates ONLY when registered here (or timeless year-slice-mean bases).
        self.class_time: dict = {}

    @property
    def superclasses(self):
        """This chain's {class -> set(direct superclasses)} map: STATIC taxonomy
        plus the chain's own subclass_of edges. Pure per-chain state (no global),
        rebuilt when subclass_of changes — two chains never contaminate ancestry."""
        key = tuple(sorted((k, tuple(v if isinstance(v, list) else [v]))
                           for k, v in self.subclass_of.items()))
        if self._superclasses_cache is None or self._superclasses_key != key:
            self._superclasses_cache = chain_superclasses(self.subclass_of)
            self._superclasses_key = key
        return self._superclasses_cache

    # ---- YAML loader (the source of truth) ------------------------------
    @classmethod
    def from_yaml(cls, path):
        """Load a scenario from a YAML file. The YAML is the only authored
        artefact; the Turtle fixture and the ground truth are both derived
        from it."""
        import yaml
        data = yaml.safe_load(pathlib.Path(path).read_text())
        # ETL-generated YAMLs carry node_time (new format); hand-authored YAMLs carry
        # class_time. Bridge: convert node_time -> class_time before loading.
        if data.get("node_time"):
            from chain_from_doc import _reslice_from_node_time
            data = _reslice_from_node_time(dict(data))
        sc = cls(label=data.get("title", ""), id=data.get("id", ""))

        # scenario-level provenance/validity/production REQUIRED by the SHACL on
        # every statement. Authored once (shared across statements) but explicit.
        prov = data.get("provenance")
        if not prov:
            raise ValueError(f"{path}: missing required 'provenance' block "
                             "(source, agent, production, validFrom[, validUntil])")
        for k in ("source", "agent", "production", "validFrom"):
            if k not in prov:
                raise ValueError(f"{path}: provenance missing required '{k}'")
        sc.provenance = dict(prov)

        # optional subclass declarations the local hierarchy lacks, so ancestor
        # aggregation rolls such a class up; kept on the chain so to_graph emits
        # them as rdfs:subClassOf (RDF rules need the same hierarchy).
        sc.subclass_of = {}
        for sub, sup in (data.get("subclass_of") or {}).items():
            sups = sup if isinstance(sup, list) else [sup]
            sc.subclass_of[sub] = list(sups)
        # the chain's own superclass map (static taxonomy + these edges) is
        # derived lazily from sc.subclass_of by the `superclasses` property — no
        # process-global mutation.

        # the time registry: every entry must carry EXACTLY ONE of a year or a
        # start+end period (ints), optionally a base class and a strategy
        # token. No silent defaults — malformed entries fail loudly.
        sc.class_time = {}
        for cls_name, spec in (data.get("class_time") or {}).items():
            sc.class_time[cls_name] = cls._parse_class_time(path, cls_name,
                                                            spec)

        for name, spec in data["nodes"].items():
            im = cls._parse_item_mass(path, name, spec)
            sc.node(name, spec["level"], spec["class"], item_mass=im)

        core = {"whole", "part", "best", "lo", "hi", "unit", "dist", "quality"}
        for s in data["statements"]:
            dist_params = {k: float(v) for k, v in s.items() if k not in core}
            # EVERY required element of a composition statement must be written
            # explicitly — no silent defaults. best (the central value), unit,
            # distribution. lo/hi (the spread) are OPTIONAL.
            for k in ("best", "unit", "dist"):
                if k not in s:
                    raise ValueError(
                        f"{path}: statement {s['whole']}->{s['part']} missing "
                        f"required '{k}' (best value / unit / distribution)")
            # a statement whose best value is zero asserts the part is ENTIRELY
            # ABSENT — it carries no information and just pads the file. Reject
            # it: omit the statement instead of writing a useless "0.0" leaf.
            if float(s["best"]) == 0.0:
                raise ValueError(
                    f"{path}: statement {s['whole']}->{s['part']} has best=0.0 "
                    f"(zero content) — a composition statement must assert a "
                    f"non-zero amount; omit the statement instead of padding "
                    f"with a zero.")
            unit = UNIT_BY_NAME[s["unit"]]
            dist = s["dist"]
            from common.vocab import fill_quality
            quality = fill_quality(s.get("quality"))   # always the full six-dim vector
            lo = float(s["lo"]) if "lo" in s else None
            hi = float(s["hi"]) if "hi" in s else None
            sc.stmt(s["whole"], s["part"], float(s["best"]), lo, hi, unit,
                    dist=dist, dist_params=dist_params, quality=quality)

        sc._note = data.get("note", "")
        return sc

    @staticmethod
    def _parse_item_mass(origin, name, spec):
        """Validate a node spec's itemMass. REQUIRED (positive float) on every
        Product/Component node — the reference anchor; FORBIDDEN on Material/
        Element (bulk matter has no item mass). No silent defaults."""
        level = spec["level"]
        im = spec.get("itemMass")
        if level in ("Product", "Component"):
            if im is None:
                raise ValueError(
                    f"{origin}: node {name!r} (level {level}) is missing "
                    f"required 'itemMass' (absolute kg of the item)")
            im = float(im)
            if im <= 0.0:
                raise ValueError(
                    f"{origin}: node {name!r} has non-positive itemMass {im}")
            return im
        if im is not None:
            raise ValueError(
                f"{origin}: node {name!r} (level {level}) must NOT carry "
                f"'itemMass' — only Product/Component items have a mass")
        return None

    @staticmethod
    def _parse_class_time(origin, cls_name, spec):
        """Validate one class_time entry: exactly one of 'year' xor ('start'+'end')
        ints; optional 'strategy' token and 'slices' = {parent, axis} edges (axis
        IRI IS the aggregating strategy; >1 axis allowed). Fail loud else."""
        from .vocab import STRATEGY_IRI
        if not isinstance(spec, dict):
            raise ValueError(f"{origin}: class_time[{cls_name}] must be a "
                             f"mapping, got {type(spec).__name__}")
        unknown = set(spec) - {"year", "start", "end", "slices", "strategy"}
        if unknown:
            raise ValueError(f"{origin}: class_time[{cls_name}] has unknown "
                             f"key(s) {sorted(unknown)}")
        has_year = "year" in spec
        has_period = "start" in spec or "end" in spec
        if has_year == has_period or (has_period and not
                                      ("start" in spec and "end" in spec)):
            raise ValueError(
                f"{origin}: class_time[{cls_name}] must carry EXACTLY ONE of "
                f"'year' or 'start'+'end' (got {sorted(spec)})")
        out = {}
        for k in ("year", "start", "end"):
            if k in spec:
                v = spec[k]
                if isinstance(v, bool) or not isinstance(v, int):
                    raise ValueError(f"{origin}: class_time[{cls_name}].{k} "
                                     f"must be an integer year, got {v!r}")
                out[k] = v
        if has_period and out["start"] > out["end"]:
            raise ValueError(f"{origin}: class_time[{cls_name}] period start "
                             f"{out['start']} > end {out['end']}")
        if "slices" in spec:
            out["slices"] = SupplyChain._parse_slices(origin, cls_name,
                                                      spec["slices"])
        if "strategy" in spec:
            if spec["strategy"] not in STRATEGY_IRI:
                raise ValueError(
                    f"{origin}: class_time[{cls_name}].strategy "
                    f"{spec['strategy']!r} is not one of "
                    f"{sorted(STRATEGY_IRI)}")
            out["strategy"] = spec["strategy"]
        return out

    @staticmethod
    def _parse_slices(origin, cls_name, raw):
        """Validate 'slices' -> [(parent_local, axis_token)]. Accepts {parent,
        axis} dicts, (parent, axis) tuples, or a bare string parent (axis defaults
        to year-slice-mean). axis must name a known AggregationStrategy."""
        from .vocab import STRATEGY_IRI
        if not isinstance(raw, list):
            raise ValueError(f"{origin}: class_time[{cls_name}].slices must be "
                             f"a list, got {type(raw).__name__}")
        out = []
        for item in raw:
            if isinstance(item, str):
                parent, axis = item, "year-slice-mean"
            elif isinstance(item, (tuple, list)):
                # the already-parsed internal form (parent, axis) — idempotent
                parent = str(item[0])
                axis = item[1] if len(item) > 1 else "year-slice-mean"
            elif isinstance(item, dict):
                if "parent" not in item:
                    raise ValueError(f"{origin}: class_time[{cls_name}].slices "
                                     f"entry {item!r} needs a 'parent'")
                parent = str(item["parent"])
                axis = item.get("axis", "year-slice-mean")
            else:
                raise ValueError(f"{origin}: class_time[{cls_name}].slices entry "
                                 f"{item!r} must be a string, tuple, or mapping")
            if axis not in STRATEGY_IRI:
                raise ValueError(
                    f"{origin}: class_time[{cls_name}].slices axis {axis!r} is "
                    f"not one of {sorted(STRATEGY_IRI)}")
            out.append((parent, axis))
        return out

    # ---- description API ------------------------------------------------
    def node(self, name, level, cls, item_mass=None):
        n = Node(name, level, cls, item_mass=item_mass)
        self.nodes[name] = n
        return n

    def stmt(self, whole, part, best, lo=None, hi=None, unit=KGKG,
             dist="triangular", dist_params=None, quality=None):
        s = Stmt(whole, part, best, lo, hi, unit, dist=dist,
                 dist_params=dist_params or {}, quality=quality or {})
        self.stmts.append(s)
        return s


# Back-compat alias: callers from the subclass era (from_graph, older code)
# import FastSupplyChain; it IS the SupplyChain now.
FastSupplyChain = SupplyChain
