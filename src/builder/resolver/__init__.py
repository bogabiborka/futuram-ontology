# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyshacl", "owlrl", "pyyaml"]
# ///
"""resolver — projects the class-level aggregates into the served fq: graph as a
pipeline of PLUGINS. Each served ANGLE is one Plugin; the Resolver runs them as a DAG
and unions their output. Add an angle by writing one Plugin into DEFAULT_PLUGINS.
"""
from rdflib import RDFS   # re-exported: tests read resolver.RDFS (old module global)

from .plugin import Plugin
from .context import ResolverContext
from .engine import Resolver
from . import vocab

from .plugins.axis import AxisPlugin
from .plugins.taxonomy import TaxonomyPlugin
from .plugins.item_mass import ItemMassPlugin
from .plugins.mc_pointers import McPointerPlugin
from .plugins.elements import ElementAmountsPlugin
from .plugins.component import ComponentPlugin
from .plugins.partof import PartOfPlugin
from .plugins.labels import LabelPlugin
from .plugins.comments import CommentPlugin
from .uncertainty import UncertaintyRulesetPlugin

# Re-export the namespaces + vocabulary constants callers/tests reach for on the
# `resolver.<NAME>` surface (these used to be module globals of the monolith).
FQ = vocab.FQ
FUT = vocab.FUT
LEVEL_CLASS = vocab.LEVEL_CLASS
CLASS_LEVEL = vocab.CLASS_LEVEL
UNIT = vocab.UNIT
PRODUCT, COMPONENT, MATERIAL, ELEMENT = (
    vocab.PRODUCT, vocab.COMPONENT, vocab.MATERIAL, vocab.ELEMENT)
UNKNOWN_PRODUCT = vocab.UNKNOWN_PRODUCT
UNKNOWN_COMPONENT = vocab.UNKNOWN_COMPONENT
UNKNOWN_MATERIAL = vocab.UNKNOWN_MATERIAL
UNKNOWN_ELEMENT = vocab.UNKNOWN_ELEMENT

# The default pipeline, in declaration order (the engine topo-sorts by deps; this
# order only breaks ties). PartOf depends on elements+component, so it runs last.
DEFAULT_PLUGINS = [
    AxisPlugin(),
    TaxonomyPlugin(),              # re-emit declared part->parent subClassOf edges
    ItemMassPlugin(),
    McPointerPlugin(),
    ElementAmountsPlugin(),
    ComponentPlugin(),
    PartOfPlugin(),
    LabelPlugin(),                 # human label per projected class (derived)
    CommentPlugin(),               # rdfs:comment: the semantic notion of each class
    UncertaintyRulesetPlugin(),   # last: enriches the amounts the others minted
]


def resolve_all(source, into=None, *, only=None):
    """Run the default plugin pipeline over a composition RDF graph (or anything
    with .to_graph()). RDF graph in -> served fq: graph out. Deterministic
    best-value only; the MC band is a poc extension (poc -> builder)."""
    return Resolver(DEFAULT_PLUGINS).run(source, into=into, only=only)
