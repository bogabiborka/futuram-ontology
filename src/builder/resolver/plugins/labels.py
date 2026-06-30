"""LabelPlugin — the human-label angle (every served class needs a searchable label),
DERIVED from RDF by four rules: (1) SOURCE label the ETL authored; (2) SLICE = base label
+ scope phrase; (3) AGGREGATE = common PREFIX of children; (4) FALLBACK = humanise.
"""
from __future__ import annotations

import re

from rdflib import Graph, RDFS, Literal, RDF, OWL

from ..plugin import Plugin
from .. import emit_helpers as E
from ..vocab import FUT, FQ


def _scope_phrase(entry):
    """A human time-scope phrase from a class_time entry: a single year -> "<year>
    production year"; an interval -> "<start>–<end> production period". None when the
    entry has no readable time scope (a non-time axis slice, labelled via its base)."""
    if entry is None:
        return None
    if entry.get("year") is not None:
        return f"{entry['year']} production year"
    start, end = entry.get("start"), entry.get("end")
    if start is not None and end is not None:
        return f"{start}–{end} production period"
    return None


def _common_prefix(labels):
    """The longest shared PREFIX of `labels`, cut back to the last COMPLETE CLAUSE so the
    result is never a dangling fragment (empty when no head is shared). A partial head
    word after the final spaced separator is dropped; in-word hyphens survive."""
    labels = [l for l in labels if l]
    if len(labels) < 2:
        return ""
    pre = labels[0]
    for l in labels[1:]:
        i = 0
        while i < len(pre) and i < len(l) and pre[i] == l[i]:
            i += 1
        pre = pre[:i]
        if not pre:
            return ""
    # Cut at the last clause separator (spaced em/en dash, hyphen, colon, or " (")
    # unless the prefix already ends at a clause boundary; a trailing run after the last
    # separator is a partial/never-shared clause head -> drop it.
    m = list(re.finditer(r"\s+[—–\-:]\s+|\s+\(", pre))
    if m and pre[m[-1].end():].strip():        # text after the last separator
        pre = pre[:m[-1].start()]
    return pre.strip(" —–-:([")


def _split_words(s):
    """camelCase / letter-digit runs -> space-separated, lower-cased words."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)   # camelCase boundary
    s = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", s)    # letter->digit boundary
    return s.replace("_", " ").strip().lower()


def _humanise(local):
    """Humanise a class local name purely from the IRI: drop the conventional `elv`
    prefix; render the `unknown` remainder prefix as "unattributed" (the unpinned mass).
    elvElectricMotor -> "electric motor"; unknownElement -> "unattributed element"."""
    if local.startswith("unknown") and len(local) > len("unknown"):
        return "unattributed " + _split_words(local[len("unknown"):])
    if local.startswith("elv") and len(local) > len("elv"):
        return _split_words(local[len("elv"):])
    return _split_words(local)


class LabelPlugin(Plugin):
    """rdfs:label for every served class, by the four generic rules. Labels EVERY futuram
    class the served graph carries (read from upstream, not ctx.projected_classes() alone
    — rollup-only aggregates + unknown* remainders are served yet absent there).
    """

    name = "labels"
    deps = ("elements", "component", "partof", "axis", "item_mass", "taxonomy")

    def project(self, ctx, upstream: Graph) -> Graph:
        g = Graph()
        # source labels, looked up by class local-name (the merged composition
        # graph the ETL authored — rich V-code labels live here).
        src_label = {}
        for s, o in ctx.graph_in.subject_objects(RDFS.label):
            if str(s).startswith(str(FUT)):
                src_label.setdefault(str(s)[len(str(FUT)):], o)

        class_time = ctx.class_time
        cache = {}

        def derive(name, _stack=()):
            if name in cache:
                return cache[name]
            if name in _stack:                       # cycle guard
                return None
            lbl = self._derive(name, ctx, src_label, class_time,
                               lambda n: derive(n, _stack + (name,)))
            cache[name] = lbl
            return lbl

        # the class set the served graph carries: every upstream futuram owl:Class, the
        # per-class aggregates, AND every class named as an fq:constituent (the unknown*
        # remainders + constituents typed only by subClassOf) — so every row is labelled.
        def _futloc(s):
            return str(s)[len(str(FUT)):] if str(s).startswith(str(FUT)) else None
        served = {ln for s in upstream.subjects(RDF.type, OWL.Class)
                  if (ln := _futloc(s))}
        served |= {ln for s in upstream.objects(None, FQ.constituent)
                   if (ln := _futloc(s))}
        served |= set(ctx.projected_classes())

        for cls_name in served:
            lbl = derive(cls_name)
            if lbl is not None:
                cls = E.class_node(g, cls_name)
                g.add((cls, RDFS.label, lbl))
        return g

    @staticmethod
    def _derive(name, ctx, src_label, class_time, recur):
        # 1. SOURCE label.
        if name in src_label:
            return src_label[name]
        # 2. SLICE: base label + this slice's scope phrase.
        entry = class_time.get(name)
        slices = entry.get("slices", ()) if entry else ()
        if slices:
            base = slices[0][0]
            base_lbl = src_label.get(base) or recur(base)
            phrase = _scope_phrase(entry)
            if base_lbl is not None and phrase:
                return Literal(f"{base_lbl} — {phrase}",
                               lang=getattr(base_lbl, "language", None))
        # 3. AGGREGATE: common prefix of labelled direct children = the human NAME of the
        #    aggregate ("diesel vehicle"); that it IS a MEAN over its subclasses is carried
        #    in the rdfs:comment (CommentPlugin), keeping the label clean and searchable.
        kids = ctx.direct_subclasses(name)
        kid_lbls = [src_label.get(k) for k in kids]
        kid_lbls = [str(l) for l in kid_lbls if l is not None]
        prefix = _common_prefix(kid_lbls)
        if prefix:
            lang = next((l.language for k in kids
                         for l in (src_label.get(k),) if l is not None), None)
            return Literal(prefix, lang=lang)
        # 4. FALLBACK: humanised local name.
        human = _humanise(name)
        return Literal(human, lang="en") if human else None
