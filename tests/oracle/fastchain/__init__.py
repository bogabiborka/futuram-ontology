"""fastchain — THE cached SupplyChain (oracle arithmetic); successor of the
retired legacy/supplychain.py, which stays the frozen REFERENCE test_fastchain.py
cross-checks (1e-9). Describe a chain in Python -> GROUND TRUTH + Turtle fixture.
"""

from .vocab import (FUT, EX, PROV, QUDT, UNIT, TIME, CEONQ, CEONPR, CEONPO,
                    DQV, LEVELS, LEVEL_RANK, KGKG, GKG, SCALE_TO_KGKG,
                    UNIT_BY_NAME, DIST_KINDS, DQV_DIMENSIONS, _PICK,
                    _vecs_close, STRATEGY_IRI, STRATEGY_TOKEN)
from .hierarchy import (_HIER_PATH, _HIER_STRATEGIES, _LEVEL_ROOTS,
                        _STATIC_SUPERCLASSES, _load_superclasses,
                        chain_superclasses, ancestors_of)
from .model import Node, Stmt
from .chain import SupplyChain, FastSupplyChain
