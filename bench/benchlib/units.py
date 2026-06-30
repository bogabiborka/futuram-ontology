"""Unit normalisation — turn any (value, unit) into a canonical kg/kg fraction
(for composition fractions) or pass an absolute kg through. Lets us compare the
model's answer to the expected one regardless of which unit it reported in."""
from __future__ import annotations

# factor maps a unit to "multiply the numeric value by this to get the canonical
# quantity". Fractions canonicalise to kg/kg (dimensionless); masses to kg.
_FRACTION_UNITS = {
    "kg/kg": 1.0, "kgpkg": 1.0, "kilogm-per-kilogm": 1.0, "fraction": 1.0,
    "": 1.0, "1": 1.0, "dimensionless": 1.0,
    "g/kg": 1e-3, "gpkg": 1e-3, "gm-per-kilogm": 1e-3,
    "mg/kg": 1e-6, "ppm": 1e-6,
    "%": 1e-2, "percent": 1e-2, "pct": 1e-2,
}
_MASS_UNITS = {
    "kg": 1.0, "kilogm": 1.0, "kilogram": 1.0, "kilograms": 1.0,
    "g": 1e-3, "gm": 1e-3, "gram": 1e-3, "grams": 1e-3,
    "t": 1e3, "tonne": 1e3, "ton": 1e3,
}


def _norm_unit_key(u: str) -> str:
    u = (u or "").strip().lower()
    u = u.rsplit("/", 1)[-1] if u.startswith("http") else u   # IRI -> localname
    u = u.rsplit("#", 1)[-1]
    return u


def canonical(value: float, unit: str) -> tuple[float, str]:
    """Return (canonical_value, dimension) where dimension is 'fraction' or
    'mass'. Unknown units pass through as a 'raw' dimension (compared as-is)."""
    key = _norm_unit_key(unit)
    if key in _FRACTION_UNITS:
        return value * _FRACTION_UNITS[key], "fraction"
    if key in _MASS_UNITS:
        return value * _MASS_UNITS[key], "mass"
    return value, "raw"
