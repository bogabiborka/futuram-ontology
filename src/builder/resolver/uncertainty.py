"""resolver.uncertainty — the data-quality -> uncertainty rule, an RDF
futuram:UncertaintyRuleset READ off the TBox (no hardcoded bands): (1) DQ scores -> MEAN
-> DQS band; (2) DQS -> relative uncertainty; (3) combine by RootSumOfSquares. RELATIVE.
"""
from __future__ import annotations

import math
from collections import defaultdict

from rdflib import Graph, Literal, RDF, XSD

from .plugin import Plugin
from .vocab import FQ, FUT, local


# The single ruleset this build uses. One ruleset is declared (futuram:FuturamDQS);
# selecting among several would be a build-config concern — kept as the lone
# UncertaintyRuleset individual found in the TBox, erroring if that is ambiguous.
def _the_ruleset(tbox: Graph):
    rs = list(tbox.subjects(RDF.type, FUT.UncertaintyRuleset))
    if not rs:
        raise ValueError(
            "no futuram:UncertaintyRuleset in the TBox — is "
            "ontology/tbox/uncertainty-ruleset.ttl loaded?")
    if len(rs) > 1:
        raise ValueError(
            f"ambiguous: {len(rs)} futuram:UncertaintyRuleset individuals; "
            "this build expects exactly one")
    return rs[0]


class RulesetReader:
    """The uncertainty rule, read from the TBox graph. Pure: every number comes
    from the RDF (bands, percentages, the combination operator's identity), nothing
    is hardcoded here. Reusable across plugins and directly unit-testable."""

    def __init__(self, tbox: Graph, ruleset=None):
        self.g = tbox
        self.ruleset = ruleset if ruleset is not None else _the_ruleset(tbox)
        self._bands = self._load_bands()
        self._default_agg = self.g.value(self.ruleset, FUT.defaultDqvAggregation)
        self._method = self.g.value(self.ruleset, FUT.combinationMethod)
        self._dim_weights = self._load_dim_weights()
        self._limit_bands = self._load_limit_bands()

    # ---- the data-quality -> uncertaintyLimit rule (weighted sum + bands) ---
    def _load_dim_weights(self):
        """{dqv:Dimension IRI -> weight} for the uncertainty-limit weighted sum."""
        out = {}
        for w in self.g.objects(self.ruleset, FUT.hasDimensionWeight):
            dim = self.g.value(w, FUT.weightDimension)
            val = self.g.value(w, FUT.weightValue)
            if dim is not None and val is not None:
                out[dim] = float(val)
        return out

    def _load_limit_bands(self):
        """Sorted [(wsum_lo, wsum_hi, limit)] rows of the weighted-sum -> limit table."""
        bands = []
        for b in self.g.objects(self.ruleset, FUT.hasLimitBand):
            lo = self.g.value(b, FUT.weightedSumLowerBound)
            hi = self.g.value(b, FUT.weightedSumUpperBound)
            lim = self.g.value(b, FUT.bandUncertaintyLimit)
            if lo is not None and hi is not None and lim is not None:
                bands.append((float(lo), float(hi), float(lim)))
        return sorted(bands, key=lambda r: r[0])

    def dimension_of_metric(self, metric_iri):
        """futuram:<Dim>Score metric IRI -> its dqv:Dimension (via dqv:inDimension),
        over this reader's TBox graph. None if the metric is unknown."""
        if metric_iri is None:
            return None
        return _dimension_of_metric(self.g, metric_iri)

    def limit_from_scores(self, scores):
        """The rectangular uncertaintyLimit DERIVED from a statement's per-dimension DQ
        scores: weighted sum (futuram:hasDimensionWeight) banded to a limit. None if any
        weighted dimension is missing (calibrated for the COMPLETE six-dim vector)."""
        have = {d: float(v) for d, v in scores if v is not None}
        if not have:
            return None
        # all weighted dimensions must be measured (the rule is over the full vector)
        if not set(self._dim_weights).issubset(have):
            return None
        wsum = sum(self._dim_weights.get(d, 1.0) * v for d, v in have.items())
        for lo, hi, lim in self._limit_bands:
            if lo <= wsum < hi:
                return lim
        return None

    # ---- Eq.(1)/Eq.(2): the band table ------------------------------------
    def _load_bands(self):
        """Sorted [(mean_lo, mean_hi, dqs, unc)] rows. `unc` is the band's relative
        uncertainty: the point futuram:uncertaintyValue, or the midpoint of the
        uncertaintyLowerBound/UpperBound range (the range itself stays in the RDF)."""
        bands = []
        for b in self.g.subjects(RDF.type, FUT.DqsBand):
            lo = self.g.value(b, FUT.meanLowerBound)
            if lo is None:
                continue          # no mean interval at all — skip
            hi_node = self.g.value(b, FUT.meanUpperBound)
            hi = float(hi_node) if hi_node is not None else math.inf  # open top band
            dqs = self.g.value(b, FUT.dqsValue)
            pt = self.g.value(b, FUT.uncertaintyValue)
            if pt is not None:
                unc = float(pt)
            else:
                ulo = self.g.value(b, FUT.uncertaintyLowerBound)
                uhi = self.g.value(b, FUT.uncertaintyUpperBound)
                unc = (float(ulo) + float(uhi)) / 2.0 if ulo is not None and uhi is not None else None
            bands.append((float(lo), hi, int(dqs), unc))
        return sorted(bands, key=lambda r: r[0])

    def band_for_mean(self, mean_dq):
        """The (dqs, relative_uncertainty) the mean data quality falls into (Eq.1
        band -> Eq.2 percentage). Half-open [lo, hi); the top band is open (hi=inf).
        None if no band covers it (mean below the lowest band's lower bound)."""
        for lo, hi, dqs, unc in self._bands:
            if lo <= mean_dq < hi:
                return dqs, unc
        return None

    # ---- Eq.(1) first arrow: mean of the per-dimension scores --------------
    def mean_data_quality(self, scores, dqv_rule=None):
        """Combine a statement's per-dimension scores into one mean data quality per the
        futuram:DqvAggregation rule (`dqv_rule`, default the ruleset's). Honours the
        rule's missing-dimension policy. None if no usable score (e.g. RequireAll gap)."""
        rule = dqv_rule if dqv_rule is not None else self._default_agg
        present = [float(v) for _dim, v in scores if v is not None]
        if not present:
            return None
        policy = self.g.value(rule, FUT.onMissingDimension) if rule is not None else None
        if policy == FUT.RequireAllDimensions and rule is not None:
            expected = set(self.g.objects(rule, FUT.expectsDimension))
            have = {d for d, v in scores if d is not None and v is not None}
            if expected and not expected <= have:
                return None
        # EXTRA-WEIGHT term (the FutuRaM weighted mean): if the rule declares
        # futuram:extraWeightDimension, the mean is over the present scores PLUS one
        # synthetic 7th element = mean(the extra-weight dimensions' scores).
        extra = set(self.g.objects(rule, FUT.extraWeightDimension)) if rule is not None else set()
        if extra:
            ext_vals = [float(v) for d, v in scores if d in extra and v is not None]
            if ext_vals:
                terms = present + [sum(ext_vals) / len(ext_vals)]
                return sum(terms) / len(terms)
        return sum(present) / len(present)

    def dqv_rule_of_strategy(self, strategy_iri):
        """The futuram:DqvAggregation IRI a strategy declares (futuram:
        dqvAggregationRule), or None to fall back to the ruleset default."""
        if strategy_iri is None:
            return None
        return self.g.value(strategy_iri, FUT.dqvAggregationRule)

    # ---- per-statement: scores -> (mean_dq, dqs, relative_uncertainty) -----
    def statement_uncertainty(self, scores, dqv_rule=None):
        mean = self.mean_data_quality(scores, dqv_rule)
        if mean is None:
            return None
        band = self.band_for_mean(mean)
        if band is None:
            return None
        dqs, unc = band
        return {"mean": mean, "dqs": dqs, "uncertainty": unc}

    # ---- Eq.(3): combine several statements' uncertainties -----------------
    def combine(self, contributions):
        """Combine per-statement RELATIVE uncertainties into one aggregate by the
        ruleset's combinationMethod (Eq.3): the CONTRIBUTION-WEIGHTED relative RSS
        sqrt(sum (u_i*v_i)^2) / sum v_i over `contributions` of (relative_u, value)."""
        pairs = [(u, v) for u, v in contributions if u is not None and v is not None]
        if not pairs:
            return None
        total = sum(v for _u, v in pairs)
        if total <= 0:
            return None
        if self._method == FUT.RootSumOfSquares:
            abs_sigma = math.sqrt(sum((u * v) ** 2 for u, v in pairs))
            return abs_sigma / total
        raise ValueError(
            f"unknown combination method {self._method!r}; teach RulesetReader.combine "
            "the new futuram:CombinationMethod individual")


# IRI of each dqv:<Dimension> the score's metric (futuram:<Dim>Score) is in. The
# ETL stamps dqv:isMeasurementOf futuram:<Dim>Score and the TBox links that metric
# to its dqv:Dimension via dqv:inDimension; we resolve dimension off the TBox.
def _dimension_of_metric(tbox, metric_iri):
    from rdflib import Namespace
    DQV = Namespace("http://www.w3.org/ns/dqv#")
    return tbox.value(metric_iri, DQV.inDimension)


def _statement_scores(comp_graph, tbox, relation_iri):
    """The (dimension_iri, value) per-dimension DQ scores carried on one
    PartRelation, read off the composition graph's dqv:hasQualityMeasurement."""
    from rdflib import Namespace
    DQV = Namespace("http://www.w3.org/ns/dqv#")
    out = []
    for qm in comp_graph.objects(relation_iri, DQV.hasQualityMeasurement):
        val = comp_graph.value(qm, DQV.value)
        metric = comp_graph.value(qm, DQV.isMeasurementOf)
        dim = _dimension_of_metric(tbox, metric) if metric is not None else None
        out.append((dim, None if val is None else float(val)))
    return out


def _statement_best_value(comp_graph, relation_iri):
    """The best value (kg/kg) a PartRelation states, the WEIGHT its uncertainty
    enters Eq.3 with. Reached relation -hasQuantity-> interval -hasBestValue->
    qudt:QuantityValue -numericValue. 0.0 if absent (drops the term harmlessly)."""
    from rdflib import Namespace
    QUDT = Namespace("http://qudt.org/schema/qudt/")
    q = comp_graph.value(relation_iri, FUT.hasQuantity)
    if q is None:
        return 0.0
    bv = comp_graph.value(q, FUT.hasBestValue)
    if bv is None:
        return 0.0
    num = comp_graph.value(bv, QUDT.numericValue)
    return float(num) if num is not None else 0.0


_SQRT3 = math.sqrt(3.0)


def _statement_rectangular_limit(comp_graph, relation_iri):
    """The futuram:uncertaintyLimit (rectangular relative half-width) a PartRelation
    carries on its distribution, or None. The served relative uncertainty (sigma) is
    limit / sqrt(3) -- 1-to-1 with the reference CSV's uncertainty% / 100."""
    q = comp_graph.value(relation_iri, FUT.hasQuantity)
    if q is None:
        return None
    dist = comp_graph.value(q, FUT.hasDistribution)
    if dist is None:
        return None
    lim = comp_graph.value(dist, FUT.uncertaintyLimit)
    return float(lim) if lim is not None else None


def _is_rectangular(comp_graph, relation_iri):
    """True iff a relation's distribution is a futuram:RectangularDistribution (so its
    half-width is derived from the DQ scores via the uncertaintyLimitStrategy)."""
    q = comp_graph.value(relation_iri, FUT.hasQuantity)
    if q is None:
        return False
    dist = comp_graph.value(q, FUT.hasDistribution)
    return dist is not None and (dist, RDF.type, FUT.RectangularDistribution) in comp_graph


class UncertaintyRulesetPlugin(Plugin):
    """Projects the data-quality -> uncertainty rule, enriching the upstream fq:Amount
    nodes (never minting them) in two layers: PER-STATEMENT (futuram:* on each
    PartRelation) and AGGREGATED (fq:* on each fq:Amount, Eq.3-combining its sources).
    """

    name = "uncertainty"
    deps = ("elements", "component", "partof")

    def project(self, ctx, upstream) -> Graph:
        g = Graph()
        reader = RulesetReader(ctx.tbox)
        comp = ctx.graph_in

        # 1. per statement: compute + stamp; bucket by (whole class, part class) so the
        #    aggregate step can Eq.3-combine the right statements per amount. Each
        #    contribution is (relative_uncertainty, value, mean, dqs).
        #    Also build a per-edge-node lookup (whole_node, part_node) -> (u, v, mean, dqs)
        #    for instance-level indirect-path traversal in step 1.5.
        by_pair = defaultdict(list)          # (whole_cls, part_cls) -> [(u, v, mean, dqs)]
        sigma_by_node = {}                   # (whole_node, part_node) -> (u, v, mean, dqs)
        for whole_cls, relations in ctx.statement_iris_by_whole_class().items():
            # The per-statement DQ mean uses the ruleset's DEFAULT DqvAggregation (same
            # rule for every leaf), NOT the whole-class's dqvAggregationRule (that governs
            # class-level rollup; using it here made identical vectors yield diff means).
            dqv_rule = None       # -> reader falls back to the ruleset default
            for rel in relations:
                scores = _statement_scores(comp, ctx.tbox, rel)
                # DQS / mean DQ are DESCRIPTIVE recomputes; the served uncertainty is
                # sigma = uncertaintyLimit / sqrt(3), the limit DERIVED from the SAME DQ
                # scores by the uncertaintyLimitStrategy. A directly-asserted limit wins.
                res = reader.statement_uncertainty(scores, dqv_rule)
                limit = _statement_rectangular_limit(comp, rel)
                if limit is None and _is_rectangular(comp, rel):
                    limit = reader.limit_from_scores(scores)
                if limit is not None:
                    sigma = limit / _SQRT3
                    mean = res["mean"] if res else None
                    dqs = res["dqs"] if res else None
                    res = {"uncertainty": sigma, "mean": mean, "dqs": dqs}
                elif res is None:
                    continue
                self._stamp_statement(g, rel, res)
                part = comp.value(rel, FUT.refersTo)
                part_cls = ctx.node_class(local(part)) if part is not None else None
                if part_cls is not None:
                    val = _statement_best_value(comp, rel)
                    by_pair[(whole_cls, part_cls)].append(
                        (res["uncertainty"], val, res["mean"], res["dqs"]))
                    # also index by instance node pair (whole_node = the CS subject's node)
                    cs = next(iter(comp.subjects(FUT.hasPartRelation, rel)), None)
                    w_res = comp.value(predicate=FUT.hasCompositionStatement, object=cs) if cs else None
                    if w_res is not None and part is not None:
                        wn = local(w_res)
                        pn = local(part)
                        sigma_by_node[(wn, pn)] = (res["uncertainty"], val,
                                                    res["mean"], res["dqs"])

        # 1.5 Build indirect lookups for multi-hop amounts that have no direct PartRelation.
        #
        # Walk the INSTANCE-LEVEL adjacency from each top-instance root so that only the
        # one PartRelation that belongs to THAT instance contributes (not the N copies from
        # all sibling-class instances that a class-keyed lookup would inject).
        #
        # Covered paths:
        #   product→material  (1 intermediate hop: product→component→material)
        #   product→element   (2 intermediate hops: product→component→material→element)
        #   component→element (1 intermediate hop: component→material→element)
        #
        # sigma_by_node[(whole_node, part_node)] carries the single-statement sigma;
        # we scale its best value by the path fraction so reader.combine gets the right
        # absolute-fraction weight.
        indirect_by_pair = {}               # (whole_cls, part_cls) -> [(u, v_scaled, mean, dqs)]
        idx_adj = ctx.index.adj             # whole_node -> [(part_node, best, lo, hi, floor, dist, params)]
        for root in ctx.top_instances():
            prod_cls = ctx.node_class(root)
            if prod_cls is None:
                continue
            for comp_edge in idx_adj.get(root, []):
                comp_node = comp_edge[0]
                f_comp = comp_edge[1]       # best kg/kg fraction product→component
                comp_cls = ctx.node_class(comp_node)
                if comp_cls is None:
                    continue
                for mat_edge in idx_adj.get(comp_node, []):
                    mat_node = mat_edge[0]
                    f_mat = mat_edge[1]     # best kg/kg fraction component→material
                    mat_cls = ctx.node_class(mat_node)
                    if mat_cls is None:
                        continue
                    if ctx.node_level(mat_node) != "Material":
                        continue
                    # product→material (1 hop): use the comp→mat statement sigma
                    pm_key = (prod_cls, mat_cls)
                    if pm_key not in by_pair:
                        cm_entry = sigma_by_node.get((comp_node, mat_node))
                        if cm_entry is not None:
                            u, v, mean, dq_ = cm_entry
                            indirect_by_pair.setdefault(pm_key, []).append(
                                (u, v * f_comp, mean, dq_))
                    for elem_edge in idx_adj.get(mat_node, []):
                        elem_node = elem_edge[0]
                        f_elem = elem_edge[1]   # best kg/kg fraction material→element
                        elem_cls = ctx.node_class(elem_node)
                        if elem_cls is None:
                            continue
                        if ctx.node_level(elem_node) != "Element":
                            continue
                        me_entry = sigma_by_node.get((mat_node, elem_node))
                        if me_entry is None:
                            continue
                        u_me, v_me, mean_me, dq_me = me_entry
                        # component→element (1 hop): mat→elem sigma, scaled by f_mat
                        ce_key = (comp_cls, elem_cls)
                        if ce_key not in by_pair:
                            indirect_by_pair.setdefault(ce_key, []).append(
                                (u_me, v_me * f_mat, mean_me, dq_me))
                        # product→element (2 hops): mat→elem sigma, scaled by f_comp*f_mat
                        pe_key = (prod_cls, elem_cls)
                        if pe_key not in by_pair:
                            indirect_by_pair.setdefault(pe_key, []).append(
                                (u_me, v_me * f_comp * f_mat, mean_me, dq_me))

        # 2. per fq:Amount: a DIRECT statement amount gets the Eq.3 combination of its
        #    PartRelation pair's statements + descriptive mean/DQS; AGGREGATED amounts
        #    already carry their uncertainty, so here we only add fq:uncertaintyMethod.
        #    INDIRECT product→material amounts get the scaled RSS from indirect_by_pair.
        for amount in upstream.subjects(RDF.type, FQ.Amount):
            whole = upstream.value(amount, FQ.whole)
            const = upstream.value(amount, FQ.constituent)
            if whole is None or const is None:
                continue
            key = (local(whole), local(const))
            contribs = by_pair.get(key)
            if contribs:
                combined = reader.combine([(u, v) for u, v, _m, _d in contribs])
                if combined is None:
                    continue
                means = [m for _u, _v, m, _d in contribs if m is not None]
                dqss = [d for _u, _v, _m, d in contribs if d is not None]
                self._stamp_amount(g, reader, amount, combined, means, dqss)
            elif upstream.value(amount, FQ.relativeUncertainty) is not None:
                # aggregated amount: value plugin already stamped relativeUncertainty;
                # name the method that produced it.
                g.add((amount, FQ.uncertaintyMethod, reader.ruleset))
            else:
                # indirect product→material: combine via product→component fractions.
                ind_contribs = indirect_by_pair.get(key)
                if not ind_contribs:
                    continue
                combined = reader.combine([(u, v) for u, v, _m, _d in ind_contribs])
                if combined is None:
                    continue
                means = [m for _u, _v, m, _d in ind_contribs if m is not None]
                dqss = [d for _u, _v, _m, d in ind_contribs if d is not None]
                self._stamp_amount(g, reader, amount, combined, means, dqss)
        return g

    @staticmethod
    def _stamp_statement(g, rel, res):
        # mean / dqs are the DESCRIPTIVE recompute (may be absent if the statement
        # carried only a limit and no DQ scores); the uncertainty is always present.
        if res.get("mean") is not None:
            g.add((rel, FUT.hasMeanDataQuality,
                   Literal(round(res["mean"], 9), datatype=XSD.double)))
        if res.get("dqs") is not None:
            g.add((rel, FUT.hasDataQualityScore,
                   Literal(int(res["dqs"]), datatype=XSD.integer)))
        g.add((rel, FUT.hasRelativeUncertainty,
               Literal(round(res["uncertainty"], 9), datatype=XSD.double)))

    @staticmethod
    def _stamp_amount(g, reader, amount, combined, means, dqss):
        g.add((amount, FQ.relativeUncertainty,
               Literal(round(combined, 9), datatype=XSD.double)))
        g.add((amount, FQ.uncertaintyMethod, reader.ruleset))
        if means:
            g.add((amount, FQ.meanDataQuality,
                   Literal(round(sum(means) / len(means), 9), datatype=XSD.double)))
        if dqss:
            # the conservative DQS of an aggregate: its worst (highest) statement.
            g.add((amount, FQ.dqs, Literal(int(max(dqss)), datatype=XSD.integer)))
