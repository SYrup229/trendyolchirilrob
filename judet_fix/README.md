# judet_fix

Exports a CSV of **invoice series, invoice number, buyer name, city, județ** for
every invoice the app has generated — to send to Oblio support so they can
backfill the correct județ on invoices issued without it.

## Easiest way: run the .exe (no Python needed)

Download **JudetExport.exe** from the repo's [Releases](../../releases) page
(built automatically by GitHub Actions, same as the main app), put it anywhere,
and **double-click it**. It reads the app's database, writes `judet_export.csv`
next to itself, prints a summary, and waits for a keypress so you can read it.
Close the main app first so the database isn't mid-write.

## Why this works

The județ is **not** stored on the invoice. It is recovered from the original
Trendyol order each invoice was built from:

```
invoices.order_id -> orders.raw_json -> shipmentAddress.countyName   (Romania)
                                     -> shipmentAddress.stateName     (Bulgaria / Greece)
```

Works for **every country the app is used for** (verified against the live DB):

| Country | Region field | Fill rate | Example |
|---|---|---|---|
| Romania (RO)  | `countyName` | 100% | Ilfov, Salaj, Prahova |
| Bulgaria (BG) | `stateName`  | 96%  | Кърджали, Благоевград |
| Greece (GR)   | `stateName`  | 96%  | Θεσσαλονίκης, Κυκλάδων |

RO leaves `stateName` empty and BG/GR leave `countyName` empty, so a single
`countyName -> stateName` fallback picks the right value everywhere. The database
is opened **read-only**, so running this can never change your live data.

## Run from source instead

Use the project's Python (the `.venv`):

```
# This machine's data (default DB location), writes judet_export.csv here:
python export_judet.py

# A coworker's data - copy his trendyol_full.sqlite3 from
#   %APPDATA%\TrendyolFull\trendyol_full.sqlite3
# then point at it:
python export_judet.py --db "C:\path\to\his\trendyol_full.sqlite3"

# Only invoices actually sent to Oblio (skip local drafts):
python export_judet.py --sent-only

# Custom output path:
python export_judet.py --out "C:\Users\me\Desktop\judet_for_oblio.csv"
```

## Build the .exe yourself

```
python build_judet_exe.py    # outputs dist/JudetExport.exe
```

Needs only PyInstaller (`pip install pyinstaller`); the script itself uses only
the Python standard library, so the exe is small and dependency-free.

## Output

`judet_export.csv`, UTF-8 with BOM so Excel shows Romanian diacritics (ă, î, ș, ț)
and Bulgarian/Greek region names (Cyrillic/Greek script) correctly on a
double-click. Columns:

| Column | Source |
|---|---|
| invoice_series | `invoices.series` |
| invoice_number | `invoices.number` |
| buyer_name | `orders.buyer_name` (whitespace collapsed) |
| city | `shipmentAddress.city` (fallback `district`) |
| judet | `shipmentAddress.countyName` (fallback `stateName`) |

The run prints a summary: total rows, sent-to-Oblio vs draft, country breakdown,
and a warning count for any row with no județ/region in the order data.

## Notes

- **BG/GR "județ"** is really the province/region, stored in `stateName`
  (Cyrillic for BG, Greek for GR). Use `--sent-only` or filter to Romanian rows
  if Oblio only needs RO counties.
- **Spelling**: values are taken verbatim from Trendyol, which mixes diacritic
  and non-diacritic Romanian spellings (e.g. `București` vs `Bucuresti`). If
  Oblio needs one canonical spelling per county, a normalization map can be added.

## CLI options

```
--db PATH      database to read (default: %APPDATA%\TrendyolFull\trendyol_full.sqlite3)
--out FILE     output CSV path (default: judet_export.csv next to the program)
--sent-only    only invoices with an Oblio link
--no-pause     don't wait for a keypress before closing (for scripts/CI)
```
