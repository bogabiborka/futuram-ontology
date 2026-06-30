"""resolver.balance — the generic BALANCE operation so every constituent level sums to
1.0. balance(g, subject, named_sum, level_class) tops a level up with one
futuram:unknown<level> filler ROW (fq:amount = the gap); overshoot gets none (SHACL).
"""
from __future__ import annotations

from .vocab import CLASS_LEVEL, UNKNOWN_FOR_LEVEL
from . import emit_helpers as E


def balance(g, subject, named_sum, level_class):
    gap = 1.0 - float(named_sum)
    if gap <= 1e-9:
        return
    unknown_cls = UNKNOWN_FOR_LEVEL[CLASS_LEVEL[level_class]]
    E.amount(g, subject, subject, unknown_cls, gap, level_class=level_class)
