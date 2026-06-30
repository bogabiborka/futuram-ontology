"""common.vocab — the FutuRaM model's fixed RDF facts (not algorithm) shared by
every layer: namespaces, kg/kg unit scaling, the four composition levels, the
aggregation-strategy token<->IRI bridge. ETL/builder/oracle all depend DOWN here.
"""
from rdflib import Namespace

FUT     = Namespace("https://www.purl.org/futuram#")
EX      = Namespace("https://www.purl.org/futuram/example#")
PROV    = Namespace("http://www.w3.org/ns/prov#")
QUDT    = Namespace("http://qudt.org/schema/qudt/")
UNIT    = Namespace("http://qudt.org/vocab/unit/")
TIME    = Namespace("http://www.w3.org/2006/time#")
CEONP   = Namespace("http://w3id.org/CEON/ontology/product/")
CEONQ   = Namespace("http://w3id.org/CEON/ontology/quantity/")
CEONPR  = Namespace("http://w3id.org/CEON/ontology/process/")
CEONPO  = Namespace("http://w3id.org/CEON/ontology/processODP/")
DQV     = Namespace("http://www.w3.org/ns/dqv#")
FQ      = Namespace("https://www.purl.org/futuram/query#")

# the four composition levels, ordered top (product) -> bottom (element)
LEVELS = ("Product", "Component", "Material", "Element")
LEVEL_RANK = {lv: i for i, lv in enumerate(LEVELS)}

# allowed fraction units and their scale to kg/kg
KGKG = UNIT["KiloGM-PER-KiloGM"]
GKG  = UNIT["GM-PER-KiloGM"]
SCALE_TO_KGKG = {KGKG: 1.0, GKG: 0.001}
UNIT_BY_NAME = {"kgkg": KGKG, "gkg": GKG}

# The six data-quality dimensions every composition statement MUST carry. The
# uncertainty rules need the COMPLETE vector (SHACL enforces all six per PartRelation);
# quality construction fills missing dims from DEFAULT_QUALITY (medium, +/-30% limit).
DQ_DIMENSIONS = ("Validity", "Accuracy", "Consistency", "Integrity",
                 "Timeliness", "Completeness")
DEFAULT_QUALITY = {"Validity": 2.0, "Accuracy": 2.0, "Consistency": 3.0,
                   "Integrity": 2.0, "Timeliness": 3.0, "Completeness": 2.0}


def fill_quality(quality):
    """Return a COMPLETE six-dimension DQ dict: given scores + missing dims from
    DEFAULT_QUALITY. The single place enforcing 'a statement carries all six DQ
    dimensions', shared by ETL + scenario loaders + oracle."""
    out = dict(DEFAULT_QUALITY)
    out.update({k: float(v) for k, v in (quality or {}).items()})
    return out


# aggregation-strategy token <-> futuram:AggregationStrategy individual local-name
STRATEGY_IRI = {
    "equal-subclass-mean":  "EqualSubclassMeanStrategy",
    "year-slice-mean":      "YearSliceMeanStrategy",
    "drivetrain-mean":      "DrivetrainMeanStrategy",
    "mass-weighted-rollup": "MassWeightedRollupStrategy",
    "remainder":            "RemainderStrategy",
}
STRATEGY_TOKEN = {v: k for k, v in STRATEGY_IRI.items()}

_FUT = str(FUT)


def strategy_individual_iri(token):
    """token (or None) -> the full futuram: IRI of the AggregationStrategy
    individual (a str), or None. The single token->IRI promotion point."""
    local = STRATEGY_IRI.get(token)
    return (_FUT + local) if local else None


def stmt_iri(s, source):
    """Stable content-addressed IRI: futuram:stmt_<sha1(source|whole|part|best|lo|hi|unit)>.
    Identical content -> identical IRI; a conflicting re-statement surfaces a SECOND
    IRI. Same bytes as the frozen oracle's mint, so builder+oracle agree.
    """
    import hashlib
    key = "|".join((
        str(source),
        str(s.whole), str(s.part),
        repr(float(s.best)), repr(float(s.lo)), repr(float(s.hi)),
        str(s.unit),
    ))
    return FUT["stmt_" + hashlib.sha1(key.encode("utf-8")).hexdigest()]


# Axis-strategy IRI constants for the strategies the BUILDER dispatches on directly
# (year + drivetrain slicers, equal-subclass-mean parent). The others (mass-weighted,
# remainder) go via the STRATEGY_IRI token map + TBox/partof plugin, not a constant.
YEAR_SLICE_MEAN_IRI      = _FUT + "YearSliceMeanStrategy"
EQUAL_SUBCLASS_MEAN_IRI  = _FUT + "EqualSubclassMeanStrategy"
DRIVETRAIN_MEAN_IRI      = _FUT + "DrivetrainMeanStrategy"
