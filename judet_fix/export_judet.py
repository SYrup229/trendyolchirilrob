#!/usr/bin/env python3
"""
Export a CSV of (invoice series, invoice number, buyer name, city, judet) for
every invoice the app has generated.

Purpose: hand this CSV to Oblio support so they can backfill the correct judet
(county) on invoices that were issued without it. The judet is NOT stored on the
invoice itself — it is recovered from the original Trendyol order that each
invoice was built from:

    invoices.order_id  ->  orders.raw_json  ->  shipmentAddress.countyName

This covers every country the app is used for (Trendyol International):
  - Romania  (RO): judet is in `countyName`  (e.g. "Brăila", "Ilfov")
  - Bulgaria (BG): region is in `stateName`   (e.g. "Кърджали", in Cyrillic)
  - Greece   (GR): region is in `stateName`   (e.g. "Θεσσαλονίκης", in Greek)
RO leaves `stateName` empty and BG/GR leave `countyName` empty, so the single
`countyName → stateName` fallback resolves the right value for all three. The
country breakdown is printed in the run summary.

The database is opened READ-ONLY (SQLite `mode=ro`), so running this can never
modify your live data. Safe to run against the app's live DB while it's closed,
or against a copy.

Usage:
    python export_judet.py                 # uses the app's default DB, writes judet_export.csv next to this script
    python export_judet.py --db PATH       # point at a specific .sqlite3 file (e.g. a coworker's copy)
    python export_judet.py --out FILE.csv  # choose the output path
    python export_judet.py --sent-only     # only invoices actually sent to Oblio (have an Oblio link)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from pathlib import Path


def app_dir() -> Path:
    """Directory to drop the CSV in. When frozen into an .exe by PyInstaller,
    __file__ points inside a temp unpack dir, so use the exe's own folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_db_path() -> Path:
    """Same location the app uses: %APPDATA%/TrendyolFull/trendyol_full.sqlite3."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
    return base / "TrendyolFull" / "trendyol_full.sqlite3"


def open_readonly(path: Path) -> sqlite3.Connection:
    # file: URI with mode=ro guarantees we can't write to the live DB.
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


_WS = re.compile(r"\s+")


def clean(value) -> str:
    """Trim and collapse internal whitespace. Trendyol names often arrive with
    double spaces (e.g. 'Cocias  Sorin')."""
    if value is None:
        return ""
    return _WS.sub(" ", str(value)).strip()


def extract_address(raw_json: str) -> tuple[str, str, str]:
    """Return (judet, city, country) pulled from the order's shipping address.
    Never raises — a malformed/absent payload yields empty strings."""
    try:
        raw = json.loads(raw_json) if raw_json else {}
    except (ValueError, TypeError):
        return "", "", ""
    if not isinstance(raw, dict):
        return "", "", ""
    addr = raw.get("shipmentAddress") or raw.get("invoiceAddress") or {}
    if not isinstance(addr, dict):
        return "", "", ""
    judet = clean(addr.get("countyName")) or clean(addr.get("stateName"))
    city = clean(addr.get("city")) or clean(addr.get("district"))
    country = clean(addr.get("countryCode")) or clean(addr.get("country"))
    return judet, city, country


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export invoice -> judet CSV for Oblio support.")
    parser.add_argument("--db", type=Path, default=default_db_path(),
                        help="Path to trendyol_full.sqlite3 (defaults to the app's location).")
    parser.add_argument("--out", type=Path, default=app_dir() / "judet_export.csv",
                        help="Output CSV path (default: judet_export.csv next to the program).")
    parser.add_argument("--sent-only", action="store_true",
                        help="Only invoices that were actually sent to Oblio (have an Oblio link).")
    parser.add_argument("--no-pause", action="store_true",
                        help="Do not wait for a keypress before closing (for scripted/CI use).")
    args = parser.parse_args(argv)

    db_path: Path = args.db
    if not db_path.exists():
        print(f"ERROR: database not found at:\n  {db_path}\n"
              f"Pass the correct path with --db.", file=sys.stderr)
        return 2

    conn = open_readonly(db_path)

    where = "WHERE i.oblio_link IS NOT NULL AND i.oblio_link <> ''" if args.sent_only else ""
    query = f"""
        SELECT i.series        AS series,
               i.number        AS number,
               i.oblio_link    AS oblio_link,
               o.buyer_name    AS buyer_name,
               o.raw_json      AS raw_json
        FROM invoices i
        JOIN orders o ON o.id = i.order_id
        {where}
        ORDER BY i.id
    """
    try:
        rows = conn.execute(query).fetchall()
    except sqlite3.OperationalError as e:
        print(f"ERROR reading the database: {e}", file=sys.stderr)
        return 2

    total = 0
    sent = 0
    blank_judet = 0
    by_country: dict[str, int] = {}

    # utf-8-sig writes a BOM so Excel opens Romanian diacritics (ă, î, ș, ț) and
    # Bulgarian/Greek region names (Cyrillic/Greek script) correctly.
    try:
        fh = open(args.out, "w", newline="", encoding="utf-8-sig")
    except PermissionError:
        print(f"ERROR: cannot write to {args.out}\n"
              f"Is it open in Excel? Close it and run again.", file=sys.stderr)
        return 2
    with fh:
        writer = csv.writer(fh)
        writer.writerow(["invoice_series", "invoice_number", "buyer_name", "city", "judet"])
        for r in rows:
            judet, city, country = extract_address(r["raw_json"])
            writer.writerow([
                clean(r["series"]),
                clean(r["number"]),
                clean(r["buyer_name"]),
                city,
                judet,
            ])
            total += 1
            if r["oblio_link"]:
                sent += 1
            if not judet:
                blank_judet += 1
            by_country[country or "?"] = by_country.get(country or "?", 0) + 1

    conn.close()

    print(f"Database : {db_path}")
    print(f"Wrote    : {args.out}")
    print(f"Rows     : {total} invoice(s)"
          + ("" if args.sent_only else f"  ({sent} sent to Oblio, {total - sent} local draft)"))
    if by_country:
        breakdown = ", ".join(f"{c}={n}" for c, n in sorted(by_country.items(), key=lambda kv: -kv[1]))
        print(f"By country: {breakdown}")
    if blank_judet:
        print(f"WARNING  : {blank_judet} row(s) have NO judet/region in the order data — review these manually.")
    else:
        print("All rows have a judet/region filled.")
    return 0


def _run() -> int:
    # argparse's --no-pause isn't parsed yet here, so peek at argv for it.
    want_pause = "--no-pause" not in sys.argv and bool(getattr(sys.stdin, "isatty", lambda: False)())
    try:
        code = main()
    except Exception as e:  # never let a double-clicked .exe vanish on an error
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        code = 1
    if want_pause:
        try:
            input("\nDone. Press Enter to close…")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(_run())
