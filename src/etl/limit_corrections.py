"""Load and apply known DQV-adjustment corrections for composition CSVs.

The source CSVs carry an uncertainty limit that depends on the alloy family
(the ``Alloy`` column in 13_VehicleCompositionAdditionalInformation.xlsx), which
is not preserved in the exploded CSV. The TBox derives its limit purely from the
DQ6 profile, so sub-alloys that share a DQ profile but carry different intended
limits cannot be distinguished by the TBox band derivation alone. For those
combinations, a ``<stem>_known_limit_corrections.csv`` file next to the source
CSV records the corrections: for a given (comp_key, mat_key, full DQ6 input
profile), override one DQV dimension value so the TBox wsum falls in the band
that yields the source's intended uncertainty limit.

Each row is a specific (comp, mat, DQ6) → (dim, new_value) override. The input
DQ6 profile columns are: dq_validity, dq_accuracy, dq_consistency, dq_integrity,
dq_timeliness, dq_completeness. The override columns are: override_dim (e.g.
'Accuracy') and override_value (int 1-4).

The correction table is kept separate from the transformation code so these
overrides are recorded and reviewable in one place.
"""
from __future__ import annotations

import csv
import pathlib


def load(path) -> dict:
    """Load ``<stem>_known_limit_corrections.csv`` next to *path*.

    Returns ``{(comp_key, mat_key, dqV, dqAc, dqCo, dqI, dqT, dqCp): {dim: int}}``
    where all DQ6 values are ints (no wildcards — every correction is fully
    specific to prevent unintended matches).
    Empty dict if the sidecar file does not exist.
    """
    stem = pathlib.Path(path).stem
    corrections_path = pathlib.Path(path).parent / f"{stem}_known_limit_corrections.csv"
    if not corrections_path.exists():
        return {}
    out: dict = {}
    with open(corrections_path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(r for r in fh if not r.lstrip().startswith("#")):
            comp = (row.get("comp_key") or "").strip()
            mat = (row.get("mat_key") or "").strip()
            dim = (row.get("override_dim") or "").strip()
            val_s = (row.get("override_value") or "").strip()
            if not comp or not mat or not dim or not val_s:
                continue
            try:
                dqV  = int(row["dq_validity"])
                dqAc = int(row["dq_accuracy"])
                dqCo = int(row["dq_consistency"])
                dqI  = int(row["dq_integrity"])
                dqT  = int(row["dq_timeliness"])
                dqCp = int(row["dq_completeness"])
                val  = int(val_s)
            except (ValueError, KeyError):
                continue
            key = (comp, mat, dqV, dqAc, dqCo, dqI, dqT, dqCp)
            out.setdefault(key, {})[dim] = val
    return out


def lookup(corrections: dict, comp_key: str, mat_key: str,
           dq_validity=None, dq_accuracy=None, dq_consistency=None,
           dq_integrity=None, dq_timeliness=None, dq_completeness=None):
    """Return a DQV override dict ``{dim: new_value}`` for the given row, or
    ``None`` if no correction applies.

    The lookup is exact on all 8 key fields: comp_key, mat_key, and all six DQ
    dimension values. No wildcards — each correction targets a specific observed
    profile.
    """
    if not corrections:
        return None

    def _i(v):
        return int(v) if v is not None else None

    key = (comp_key, mat_key,
           _i(dq_validity), _i(dq_accuracy), _i(dq_consistency),
           _i(dq_integrity), _i(dq_timeliness), _i(dq_completeness))
    return corrections.get(key)
