"""Test cases — the ground-truth answers the user supplies, loaded from YAML."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Expected:
    """The ground-truth answer the user supplies: one value+unit, a list of
    values, or a MEMBERSHIP list of names (labels-only, e.g. 'which metals are
    critical') scored as a set with no numbers."""
    values: list[float]           # one or more expected numbers (empty if names-only)
    unit: str                     # the unit they are in
    labels: list[str] = field(default_factory=list)   # names per value / the name set
    names_only: bool = False      # score as a set of names, ignore numbers
    # SI ± uncertainty per value (same order/labels as `values`). Scored against
    # the answer only when score_uncertainty is True; otherwise report-only.
    uncertainties: list[float] = field(default_factory=list)
    score_uncertainty: bool = False
    # RANKING case ("rank … in decreasing order"): the answer's items must ALSO be
    # in DESCENDING value order, not just carry the right per-label values. The
    # expected `values`/`labels` are stored in the correct ranked order.
    ranked: bool = False
    # Optional per-label recovery-process IRI (metalwheel TBox class). When set,
    # the answer must also carry a "routes" map {constituent IRI: process IRI}.
    routes: dict[str, str] = field(default_factory=dict)  # label -> process IRI
    # Optional per-(element, base_metal) route rows for cases where the full
    # metal-wheel table is expected (one row per base-metal slot, not one per element).
    # Each entry is (element_IRI, base_metal_IRI, route_IRI).
    route_rows: list[tuple[str, str, str]] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, d: dict, score_uncertainty: bool = False,
                  ranked: bool = False) -> "Expected":
        unit = str(d.get("unit", "")).strip()
        # Membership answer: a bare list of names, no numbers.
        if "names" in d:
            names = [str(x) for x in d["names"]]
            return cls(values=[], unit=unit, labels=names, names_only=True)
        if "values" in d:
            v = d["values"]
            if isinstance(v, dict):
                # mapping label -> value (e.g. each metal -> its kg). Pairs names
                # with numbers; order preserved for readable reports.
                labels = [str(k) for k in v.keys()]
                vals = [float(x) for x in v.values()]
            else:
                vals = [float(x) for x in v]
                labels = [str(x) for x in d.get("labels", [])]
        else:
            vals = [float(d["value"])]
            labels = [str(d["label"])] if "label" in d else []
        # uncertainty: a {label: ±} map (matched to labels) or a scalar/list
        uncs: list[float] = []
        u = d.get("uncertainty")
        if u is not None:
            if isinstance(u, dict):
                # align to the value labels by key (fall back to 0.0 if absent)
                uncs = [float(u.get(lab, 0.0)) for lab in labels] if labels \
                       else [float(x) for x in u.values()]
            elif isinstance(u, (list, tuple)):
                uncs = [float(x) for x in u]
            else:
                uncs = [float(u)]
        # routes: either a {label: process_IRI} dict (one route per element) or a list
        # of {element, base_metal, route} dicts (full per-base-metal table).
        routes: dict[str, str] = {}
        route_rows: list[tuple[str, str, str]] = []
        r = d.get("routes")
        if r is not None:
            if isinstance(r, dict):
                routes = {str(k): str(v) for k, v in r.items()}
            elif isinstance(r, list):
                route_rows = [(str(row["element"]), str(row["base_metal"]), str(row["route"]))
                              for row in r]
        return cls(values=vals, unit=unit, labels=labels,
                   uncertainties=uncs, score_uncertainty=score_uncertainty,
                   ranked=ranked, routes=routes, route_rows=route_rows)


# The uncertainty ASK is NOT part of any SI question — the SI questions are plain
# and the ± only appears in the SI *answers* (the methodology reports it). So the
# instruction to also report the ± is appended HERE, automatically, for every
# uncertainty-scored case — never hand-written into a question's text. `question`
# stays VERBATIM from the SI; `prompt_question` is what the model is actually asked.
UNCERTAINTY_INSTRUCTION = (
    " For each reported quantity, ALSO give its ± uncertainty "
    "(absolute, in the same unit).")


@dataclass
class TestCase:
    id: str
    question: str
    expected: Expected
    backends: list[str] | None = None     # restrict to these backends if set
    # Optional PER-BACKEND ground truth: the SAME answer can have a different IRI
    # identity on each backend (e.g. the fq view exposes a year+vehicle-scoped scope
    # node `fq:<comp>_in_<veh>_Y<year>`, while the composition view's natural identity
    # is the component class `futuram:<comp>` — neither produces the other's). When a
    # backend has an entry here it is scored against THAT Expected; otherwise the
    # default `expected` applies. Values/uncertainties are normally identical across
    # backends; only the labels differ.
    expected_by_backend: dict[str, Expected] = field(default_factory=dict)

    def expected_for(self, backend_id: str | None) -> Expected:
        """The Expected to score this backend against — the per-backend override if
        one is declared for `backend_id`, else the default `expected`."""
        if backend_id and backend_id in self.expected_by_backend:
            return self.expected_by_backend[backend_id]
        return self.expected

    @property
    def prompt_question(self) -> str:
        """The exact text the model is asked: the SI-verbatim `question`, plus the
        standard uncertainty instruction iff this case scores uncertainty AND the
        question does not ALREADY ask for the uncertainty itself. The ask is
        generated, not stored, so the YAML `question` never drifts from the SI; but
        some verbatim questions (e.g. the SI-5/6/7 ranking queries) already request
        the ± in their own wording — appending the instruction there would double-ask."""
        if self.expected.score_uncertainty and not self._asks_uncertainty():
            return self.question + UNCERTAINTY_INSTRUCTION
        return self.question

    def _asks_uncertainty(self) -> bool:
        """True if the verbatim question already requests the ± uncertainty, so the
        standard instruction must NOT be appended (no redundant second ask)."""
        return "uncertaint" in self.question.lower()

    @classmethod
    def from_yaml(cls, d: dict) -> "TestCase":
        su = bool(d.get("score_uncertainty"))
        rk = bool(d.get("ranked"))
        # per-backend overrides: each inherits this case's score_uncertainty / ranked
        # flags (only the labels/values differ), unless the entry overrides them.
        ebb: dict = {}
        for be, ed in (d.get("expected_by_backend") or {}).items():
            ebb[str(be)] = Expected.from_yaml(
                ed, score_uncertainty=bool(ed.get("score_uncertainty", su)),
                ranked=bool(ed.get("ranked", rk)))
        return cls(
            id=str(d["id"]),
            question=str(d["question"]),
            expected=Expected.from_yaml(
                d["expected"], score_uncertainty=su, ranked=rk),
            backends=d.get("backends"),
            expected_by_backend=ebb,
        )


def load_testcases(path: Path) -> list[TestCase]:
    raw = yaml.safe_load(path.read_text())
    cases = raw["cases"] if isinstance(raw, dict) and "cases" in raw else raw
    return [TestCase.from_yaml(c) for c in cases]
