"""
ingest.py -- Launch Market Analysis, Step 1: Ingestion
Downloads GCAT files (Jonathan McDowell's General Catalog of
Artificial Space Objects), parses them, and loads them into DuckDB.

Usage:
    python ingest.py            # download (if needed) + load
    python ingest.py --refresh  # force re-download of all files

Output: gcat.duckdb with raw_* tables and cleaned launches/payloads tables.
"""

import argparse
import io
import re
from pathlib import Path

import duckdb
import pandas as pd
import requests

# ---------------------------------------------------------------
# Config
# ---------------------------------------------------------------

DATA_DIR = Path("data/raw")
DB_PATH = "gcat.duckdb"

GCAT_FILES = {
    "launch":  "https://planet4589.org/space/gcat/tsv/launch/launch.tsv",
    "satcat":  "https://planet4589.org/space/gcat/tsv/cat/satcat.tsv",
    "sites":   "https://planet4589.org/space/gcat/tsv/tables/sites.tsv",
    "lv":      "https://planet4589.org/space/gcat/tsv/tables/lv.tsv",
    "orgs":    "https://planet4589.org/space/gcat/tsv/tables/orgs.tsv",
}

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}

# ---------------------------------------------------------------
# Download
# ---------------------------------------------------------------

def download_file(name: str, url: str, refresh: bool = False) -> Path:
    """Download one GCAT file to data/raw/, skipping if already present."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    dest = DATA_DIR / f"{name}.tsv"

    if dest.exists() and not refresh:
        print(f"  [skip] {dest} already exists")
        return dest

    print(f"  [get ] {url}")
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()  # crash loudly if the download failed
    dest.write_bytes(resp.content)
    print(f"  [save] {dest} ({len(resp.content):,} bytes)")
    return dest


# ---------------------------------------------------------------
# Parse
# ---------------------------------------------------------------

def read_gcat_tsv(path: Path) -> pd.DataFrame:
    """
    Read a GCAT TSV file into a DataFrame.

    GCAT convention: the FIRST line is the header, prefixed with '#'.
    Any other line starting with '#' (e.g. '# Updated 2026 ...')
    is a comment and gets skipped.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

    header = lines[0].lstrip("#").strip().split("\t")
    data_lines = [ln for ln in lines[1:] if ln and not ln.startswith("#")]

    df = pd.read_csv(
        io.StringIO("\n".join(data_lines)),
        sep="\t",
        names=header,
        dtype=str,           # read everything as text; convert types later
        na_values=["-"],     # GCAT uses '-' for "no value"
        keep_default_na=False,
    )
    return df


def parse_vague_date(raw: str):
    """
    Parse GCAT's 'vague date' format into a real date.

    Examples seen in the data:
        '2023 Jan 15 0430:00'  -> 2023-01-15
        '2023 Jan 15'          -> 2023-01-15
        '2023 Jan'             -> 2023-01-01  (day unknown)
        '2023'                 -> 2023-01-01  (month unknown)
        '2023 Jan 15?'         -> 2023-01-15  (uncertain, flag stripped)

    Returns pd.NaT if unparseable. For market-level trend analysis,
    defaulting unknown day/month to 1 is a reasonable simplification --
    document this in your README limitations section.
    """
    if raw is None or not isinstance(raw, str) or not raw.strip():
        return pd.NaT

    tokens = raw.replace("?", " ").split()
    try:
        year = int(tokens[0])
        month = MONTHS.get(tokens[1][:3], 1) if len(tokens) > 1 else 1
        day = 1
        if len(tokens) > 2:
            day_match = re.match(r"(\d{1,2})", tokens[2])
            if day_match:
                day = int(day_match.group(1))
        return pd.Timestamp(year=year, month=month, day=day)
    except (ValueError, IndexError):
        return pd.NaT


# ---------------------------------------------------------------
# Clean / transform
# ---------------------------------------------------------------

def build_launches(raw: pd.DataFrame) -> pd.DataFrame:
    """
    Clean the launch list down to orbital launch attempts.

    LaunchCode: first letter = category ('O' = orbital),
    second letter = outcome ('S' success, 'F' failure, 'U' unknown,
    'E' pad explosion). Sometimes followed by digits (partial success %).
    """
    df = raw.copy()

    # Keep orbital launch attempts only
    df = df[df["LaunchCode"].str.startswith("O", na=False)]

    df["launch_date"] = df["Launch_Date"].map(parse_vague_date)
    df["launch_year"] = df["launch_date"].dt.year

    # Outcome: 2nd character of LaunchCode
    df["outcome_code"] = df["LaunchCode"].str[1]
    df["success"] = df["outcome_code"].map(
        {"S": True, "F": False, "E": False}
    )  # 'U' and anything else -> None (unknown)

    out = df[[
        "Launch_Tag", "launch_date", "launch_year",
        "LV_Type", "Agency", "Launch_Site", "Launch_Pad",
        "outcome_code", "success",
    ]].rename(columns={
        "Launch_Tag": "launch_tag",
        "LV_Type": "vehicle",
        "Agency": "agency_code",
        "Launch_Site": "site_code",
        "Launch_Pad": "pad",
    })
    return out.reset_index(drop=True)


def build_payloads(raw_satcat: pd.DataFrame) -> pd.DataFrame:
    """
    Filter the object catalog to payloads and clean it.

    Type starts with 'P'  -> payload (vs R rocket stage, C component,
    D debris). Mass is kg at orbital insertion; MassFlag '?' means
    it's an estimate (~20% accuracy per GCAT docs).

    Join key: Piece is the COSPAR designation like '2020-032A';
    stripping the trailing letter(s) gives the Launch_Tag ('2020-032')
    used in the launch list.
    """
    df = raw_satcat.copy()

    df = df[df["Type"].str.startswith("P", na=False)]

    df["mass_kg"] = pd.to_numeric(df["Mass"], errors="coerce")
    df["mass_is_estimate"] = df["MassFlag"].fillna("").str.contains(r"\?")
    df["launch_date"] = df["LDate"].map(parse_vague_date)

    # '2020-032A' -> '2020-032'
    df["launch_tag"] = df["Piece"].str.extract(r"^(\d{4}-\d+)")

    out = df[[
        "JCAT", "Piece", "Name", "launch_tag", "launch_date",
        "Owner", "State", "Manufacturer",
        "mass_kg", "mass_is_estimate", "OpOrbit",
    ]].rename(columns={
        "JCAT": "jcat_id",
        "Piece": "cospar_id",
        "Name": "payload_name",
        "Owner": "owner_code",
        "State": "state_code",
        "Manufacturer": "manufacturer_code",
        "OpOrbit": "orbit_category",
    })
    return out.reset_index(drop=True)


# ---------------------------------------------------------------
# Load into DuckDB
# ---------------------------------------------------------------

def load_duckdb(tables: dict[str, pd.DataFrame]) -> None:
    con = duckdb.connect(DB_PATH)
    for name, df in tables.items():
        con.execute(f"CREATE OR REPLACE TABLE {name} AS SELECT * FROM df")
        n = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        print(f"  [duck] {name}: {n:,} rows")
    con.close()


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true",
                    help="force re-download of all GCAT files")
    args = ap.parse_args()

    print("1) Downloading GCAT files...")
    paths = {name: download_file(name, url, args.refresh)
             for name, url in GCAT_FILES.items()}

    print("\n2) Parsing TSVs...")
    raw = {name: read_gcat_tsv(p) for name, p in paths.items()}
    for name, df in raw.items():
        print(f"  [read] {name}: {len(df):,} rows, {len(df.columns)} cols")

    print("\n3) Building clean tables...")
    launches = build_launches(raw["launch"])
    payloads = build_payloads(raw["satcat"])
    print(f"  launches (orbital only): {len(launches):,}")
    print(f"  payloads (Type=P only):  {len(payloads):,}")

    print("\n4) Loading DuckDB...")
    load_duckdb({
        # raw layer -- untouched, for reference and re-processing
        "raw_launch": raw["launch"],
        "raw_satcat": raw["satcat"],
        "raw_sites":  raw["sites"],
        "raw_lv":     raw["lv"],
        "raw_orgs":   raw["orgs"],
        # clean layer -- what your analysis queries hit
        "launches": launches,
        "payloads": payloads,
    })

    print("\n5) Sanity checks...")
    con = duckdb.connect(DB_PATH)
    print("\n  Orbital launches per year, last 5 years:")
    print(con.execute("""
        SELECT launch_year, COUNT(*) AS launches,
               SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successes
        FROM launches
        WHERE launch_year >= 2021
        GROUP BY launch_year ORDER BY launch_year
    """).df().to_string(index=False))

    print("\n  Payload mass to orbit per year, last 5 years (tonnes):")
    print(con.execute("""
        SELECT EXTRACT(year FROM launch_date) AS yr,
               ROUND(SUM(mass_kg)/1000, 1) AS tonnes,
               COUNT(*) AS payloads
        FROM payloads
        WHERE launch_date >= '2021-01-01'
        GROUP BY yr ORDER BY yr
    """).df().to_string(index=False))
    con.close()

    print("\nDone. Database written to", DB_PATH)


if __name__ == "__main__":
    main()
