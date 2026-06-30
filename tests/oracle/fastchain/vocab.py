# (fastchain package — split from the single-file implementation; the
#  arithmetic is verbatim, only the module layout changed.)
"""Vocabulary: RDF namespaces and the model's fixed constant tables."""
from rdflib import Namespace

FUT     = Namespace("https://www.purl.org/futuram#")
EX      = Namespace("https://www.purl.org/futuram/example#")
PROV    = Namespace("http://www.w3.org/ns/prov#")
QUDT    = Namespace("http://qudt.org/schema/qudt/")
UNIT    = Namespace("http://qudt.org/vocab/unit/")
TIME    = Namespace("http://www.w3.org/2006/time#")
CEONQ   = Namespace("http://w3id.org/CEON/ontology/quantity/")
CEONPR  = Namespace("http://w3id.org/CEON/ontology/process/")
CEONPO  = Namespace("http://w3id.org/CEON/ontology/processODP/")
DQV     = Namespace("http://www.w3.org/ns/dqv#")

# the four levels, ordered top (product) -> bottom (element)
LEVELS = ("Product", "Component", "Material", "Element")
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}

# which statement fraction a derivation reads (the `use` parameter)
_PICK = {
    "best": lambda s: s.best_kgkg,
    "lo":   lambda s: s.lo_kgkg,
    "hi":   lambda s: s.hi_kgkg,
}


def _vecs_close(a, b, tol=1e-6):
    """True if two element-fraction maps agree on every element within tol."""
    for k in set(a) | set(b):
        if abs(a.get(k, 0.0) - b.get(k, 0.0)) > tol:
            return False
    return True


# allowed fraction units and their scale to kg/kg
KGKG = UNIT["KiloGM-PER-KiloGM"]
GKG  = UNIT["GM-PER-KiloGM"]
SCALE_TO_KGKG = {KGKG: 1.0, GKG: 0.001}

# YAML unit string -> QUDT unit IRI
UNIT_BY_NAME = {"kgkg": KGKG, "gkg": GKG}

# distribution kinds: YAML name -> (FUT class local-name, required param names).
# best is the centre, emitted separately; these are the remaining shape params.
# 'triangular' is the default, deriving bounds from [lo, hi] when not given.
DIST_KINDS = {
    "triangular": ("TriangularDistribution", ("lowerBound", "upperBound")),
    "uniform":    ("UniformDistribution",    ("lowerBound", "upperBound")),
    "rectangular": ("RectangularDistribution", ()),
    "normal":     ("NormalDistribution",     ("stdDev",)),
    "lognormal":  ("LogNormalDistribution",  ("logStdDev",)),
    "beta":       ("BetaDistribution",       ("alpha", "beta")),
    "gamma":      ("GammaDistribution",      ("shapeParam", "scaleParam")),
    "weibull":    ("WeibullDistribution",    ("shapeParam", "scaleParam")),
}

# DQV quality dimensions the model defines; each has a matching futuram:{Dim}Score
# metric. A statement's `quality` maps a dimension local-name to a 0..3 score.
DQV_DIMENSIONS = ("Accuracy", "Completeness", "Consistency", "Integrity",
                  "Timeliness", "Validity")

# Aggregation strategies: token <-> futuram:AggregationStrategy local-name.
# 'year-slice-mean' = declared DEFAULT of a timeless P/C base (equal slice mean,
# the only cross-year agg). IDENTITY is the IRI; dispatch compares it, not the token.
STRATEGY_IRI = {
    "equal-subclass-mean":  "EqualSubclassMeanStrategy",
    "year-slice-mean":      "YearSliceMeanStrategy",
    "drivetrain-mean":      "DrivetrainMeanStrategy",
    "mass-weighted-rollup": "MassWeightedRollupStrategy",
    "remainder":            "RemainderStrategy",
}
STRATEGY_TOKEN = {v: k for k, v in STRATEGY_IRI.items()}

# The strategy individual's full IRI, by token. This is the strategy's IDENTITY;
# dispatch on this, not on the string token. None for an unknown token.
FUT_PREFIX = "https://www.purl.org/futuram#"


def STRATEGY_INDIVIDUAL_IRI(token):
    """token (or None) -> the full futuram: IRI of the AggregationStrategy
    individual (a str), or None. The single token->IRI promotion point."""
    local = STRATEGY_IRI.get(token)
    return (FUT_PREFIX + local) if local else None


# Canonical strategy IRIs for code that dispatches on identity.
YEAR_SLICE_MEAN_IRI     = FUT_PREFIX + "YearSliceMeanStrategy"
EQUAL_SUBCLASS_MEAN_IRI = FUT_PREFIX + "EqualSubclassMeanStrategy"
DRIVETRAIN_MEAN_IRI     = FUT_PREFIX + "DrivetrainMeanStrategy"
MASS_WEIGHTED_ROLLUP_IRI = FUT_PREFIX + "MassWeightedRollupStrategy"
REMAINDER_IRI           = FUT_PREFIX + "RemainderStrategy"
