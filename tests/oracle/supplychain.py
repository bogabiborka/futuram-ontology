# /// script
# requires-python = ">=3.9"
# dependencies = ["rdflib", "pyyaml"]
# ///
"""supplychain — compatibility facade over the fastchain package (the implementation).
legacy/supplychain.py is the retired single-file original, the frozen arithmetic reference
test_fastchain.py cross-checks. Keeps the historical import surface working; IS fastchain.
"""
import pathlib
import sys


from . import fastchain as _impl

# Re-export the package's public surface (including underscore names like
# _STATIC_SUPERCLASSES). The taxonomy map is immutable (per-chain ancestry lives
# on SupplyChain.superclasses), so the copied bindings stay correct.
globals().update({k: v for k, v in vars(_impl).items()
                  if not k.startswith("__")})
