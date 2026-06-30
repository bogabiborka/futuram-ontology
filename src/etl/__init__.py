"""etl — CSV/Excel/YAML -> Composition-Statement RDF (what the builder projects
into the fq: graph). Inputs under input/ (futuram/, example/, test/), outputs under
output/. These path constants are the SINGLE source of truth (no hardcoded paths).
"""
import pathlib

ETL_DIR = pathlib.Path(__file__).resolve().parent
INPUT = ETL_DIR / "input"
OUTPUT = ETL_DIR / "output"

FUTURAM_INPUT = INPUT / "futuram"          # real ELV CSVs
EXAMPLE_INPUT = INPUT / "example"          # sample workbook(s)
TEST_INPUT = INPUT / "test"                # synthetic scenario YAMLs

# Convenience handles to the canonical sample/real inputs.
EXAMPLE_XLSX = EXAMPLE_INPUT / "oneCarOnly.xlsx"
ELV_CSV_GLOB = "ELV_1980_2050_*.csv"


def elv_csvs():
    """The real ELV drivetrain CSVs present under input/futuram/ (sorted).
    Only files matching exactly ELV_1980_2050_<Drivetrain>.csv are returned;
    sidecar files like *_known_limit_corrections.csv have extra underscores in
    the drivetrain position and are correctly excluded by the part-count check."""
    return sorted(p for p in FUTURAM_INPUT.glob(ELV_CSV_GLOB)
                  if len(p.stem.split("_")) == 4)


def elv_csv(drivetrain):
    """Path to one drivetrain's ELV CSV, e.g. elv_csv('BEV')."""
    return FUTURAM_INPUT / f"ELV_1980_2050_{drivetrain}.csv"
