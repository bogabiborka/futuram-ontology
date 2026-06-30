"""CommentPlugin — the SEMANTIC-notion angle: an rdfs:comment on every served
class saying WHAT THE CLASS IS, so a reader (or an LLM) can tell a directly-
composed SPECIFIC class from a DERIVED AGGREGATE, and — for an aggregate — by WHICH
strategy it was computed.

Why: the rdfs:label is the human NAME (e.g. "diesel vehicle"), derived as the
common prefix of an aggregate's subclasses. That name alone does not reveal that
the class is a roll-up over many subclasses rather than one specific individual —
which is exactly how a query can land on the broad aggregate when it meant a
specific member. The comment makes the distinction explicit.

The comment is built ENTIRELY from the class's own generic facts:
  * the rdfs:label of its AggregationStrategy individual (ctx.strategy_label_of) —
    the strategy's OWN self-description in the TBox, so the comment reflects WHATEVER
    strategy the class declares (equal-subclass-mean / mass-weighted-rollup /
    year-slice-mean / drivetrain-mean / remainder) and a new strategy is described
    by its own label, never assuming "mean" or any fixed wording.
  * the count of its direct subclasses (ctx.direct_subclasses).
No axis identity, no hardcoded string ("segment"/"fleet"/"year"), no ground-truth
value — purely a description of how the class is constituted.
"""
from __future__ import annotations

from rdflib import Graph, Literal, RDF, RDFS, OWL

from ..plugin import Plugin
from .. import emit_helpers as E
from ..vocab import FUT, FQ


class CommentPlugin(Plugin):
    name = "comment"
    # read the served class set the structural plugins produced (same set
    # LabelPlugin labels) so every served class gets a comment, aggregates included.
    deps = ("elements", "component", "partof", "axis", "item_mass", "taxonomy")

    def project(self, ctx, upstream: Graph) -> Graph:
        g = Graph()
        for name in self._served_classes(ctx, upstream):
            text = self._comment_for(ctx, name)
            if text:
                g.add((E.class_node(g, name), RDFS.comment, Literal(text, lang="en")))
        return g

    @staticmethod
    def _subclass_split(ctx, name):
        """Direct subclasses of `name`, split into (taxonomic_parts, slice_subs):
        a slice subclass carries `name` as a parent in its class_time `slices`
        (a year/drivetrain/… axis member); the rest are taxonomic part-subclasses.
        Generic on the slice vocab — no year/axis identity, no string match."""
        parts, slices = [], []
        for c in ctx.direct_subclasses(name):
            entry = ctx.class_time.get(c)
            sliced_of = {p for p, _ax in entry.get("slices", ())} if entry else set()
            (slices if name in sliced_of else parts).append(c)
        return parts, slices

    @classmethod
    def _comment_for(cls, ctx, name):
        """The semantic notion of `name`: whether it is a derived aggregate (over
        what), a sliced class (one cut along an axis), or a specific directly-
        composed class. Built ONLY from the class's GENERIC facts — does it have an
        aggregation strategy, how many part-subclasses vs axis-slices, is it itself a
        slice. It does NOT name the strategy or the axis (those would leak the
        resolution); the strategy's exact identity stays in the data on
        fq:aggregationStrategy, and a slice's axis on fq:sliceAxis, for a reader who
        queries them."""
        token = ctx.strategy_token_of(name)          # has-a-strategy (don't name it)
        parts, slices = cls._subclass_split(ctx, name)
        # is THIS class itself a slice of some base (one cut along an axis)?
        entry = ctx.class_time.get(name)
        is_slice = bool(entry and entry.get("slices"))

        if token and (parts or slices):
            over = []
            if parts:
                over.append(f"{len(parts)} taxonomic part-subclass"
                            + ("" if len(parts) == 1 else "es"))
            if slices:
                over.append(f"{len(slices)} axis slice"
                            + ("" if len(slices) == 1 else "s"))
            scope = (" within this slice's scope" if is_slice else "")
            return (f"Derived AGGREGATE class: computed by an aggregation strategy "
                    f"(see its fq:aggregationStrategy) over its " + " and ".join(over)
                    + f"{scope}. It is a roll-up over those members, NOT one specific "
                    f"individual — when a question names a specific member (a specific "
                    f"part, OR a specific axis-slice value), query THAT member's class "
                    f"instead.")
        if is_slice:
            # a leaf that is itself a specific slice along an axis
            return ("A specific slice class — one cut of a base class along an axis "
                    "(see its fq:sliceAxis); a directly-composed leaf, not a further "
                    "aggregate.")
        if token:
            return ("Derived class: computed by an aggregation strategy "
                    "(see its fq:aggregationStrategy).")
        if parts or slices:
            return ("Taxonomic parent class; a more specific member may be the "
                    "better match for a specific question.")
        return "A specific (directly-composed) class — not an aggregate."

    @staticmethod
    def _served_classes(ctx, upstream):
        fut = str(FUT)

        def local(s):
            return str(s)[len(fut):] if str(s).startswith(fut) else None

        served = {local(s) for s in upstream.subjects(RDF.type, OWL.Class)}
        served |= {local(s) for s in upstream.objects(None, FQ.constituent)}
        served |= set(ctx.projected_classes())
        served.discard(None)
        return served
