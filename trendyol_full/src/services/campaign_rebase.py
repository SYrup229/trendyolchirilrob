"""
Campaign price rebase: take a Trendyol-exported Excel from a past campaign (with
previously-set Campaign Sale Prices) and recompute Campaign Sale Prices for a new
campaign with different discount % and Trendyol-coverage % parameters.

Formula (Option B — preserve seller take after Trendyol's discount coverage):

    pscFactor    = 1 - discount_pct * (1 - ty_cover_pct)
    clientFactor = 1 - discount_pct

    new_csp = old_csp * (clientFactor_new / clientFactor_old) * (pscFactor_old / pscFactor_new)

If a product's computed price exceeds the row's Maximum Allowed Price, it's capped
at (Max - 0.01) and flagged.
"""
import re
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.cell import Cell


# ---------------------------------------------------------------------------
# Filename param parsing
# ---------------------------------------------------------------------------

_FILENAME_RE = re.compile(
    r"(\d{1,3})\s*--?\s*discount\s*--?-?\s*(\d{1,3})\s*--?\s*trendyol[-_]?coverage",
    re.IGNORECASE,
)


def parse_filename_params(filename: str) -> tuple[float, float] | None:
    """Extract (discount_pct, ty_cover_pct) from a Trendyol-style filename, e.g.
    `35--discount---25--trendyol-coverage_2026-04-30...xlsx`.
    Returns percentages as floats (35.0, 25.0) or None if not detectable."""
    name = Path(filename).stem
    m = _FILENAME_RE.search(name)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


# ---------------------------------------------------------------------------
# Excel reading
# ---------------------------------------------------------------------------

@dataclass
class ExcelRow:
    row_index: int  # 1-based, including header
    barcode: str | None
    product_code: str | None
    name: str | None
    current_selling_price: float | None
    max_allowed_price: float | None
    campaign_sale_price: float | None


@dataclass
class ExcelDoc:
    path: Path
    sheet_name: str
    headers: list[str]
    rows: list[ExcelRow]
    column_index: dict[str, int]  # canonical-name → 0-based column index


# Canonical names used internally → list of header aliases that map to them.
_HEADER_ALIASES: dict[str, list[str]] = {
    "barcode": ["Barcode"],
    "product_code": ["Product Code"],
    "name": ["Product Name"],
    "current_selling_price": ["Current Selling Price"],
    "max_allowed_price": ["Maximum Allowed Price", "Maximum Price Allowed"],
    "campaign_sale_price": ["Campaign Sale Price"],
}


def read_excel(path: str | Path) -> ExcelDoc:
    path = Path(path)
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    if ws is None:
        raise ValueError(f"{path.name} has no active sheet.")

    headers_raw = [c.value for c in ws[1]]
    headers = [str(h).strip() if h is not None else "" for h in headers_raw]

    column_index: dict[str, int] = {}
    for canonical, aliases in _HEADER_ALIASES.items():
        for alias in aliases:
            if alias in headers:
                column_index[canonical] = headers.index(alias)
                break

    required = {"barcode", "product_code", "current_selling_price"}
    missing = required - column_index.keys()
    if missing:
        raise ValueError(
            f"{path.name}: required columns missing — {sorted(missing)}. "
            f"Found headers: {headers}"
        )

    rows: list[ExcelRow] = []
    for r in range(2, ws.max_row + 1):
        get = lambda key: ws.cell(row=r, column=column_index[key] + 1).value if key in column_index else None
        # skip completely empty rows
        if all(ws.cell(row=r, column=c + 1).value in (None, "") for c in range(ws.max_column)):
            continue
        rows.append(ExcelRow(
            row_index=r,
            barcode=_to_str(get("barcode")),
            product_code=_to_str(get("product_code")),
            name=_to_str(get("name")),
            current_selling_price=_to_float(get("current_selling_price")),
            max_allowed_price=_to_float(get("max_allowed_price")),
            campaign_sale_price=_to_float(get("campaign_sale_price")),
        ))

    return ExcelDoc(path=path, sheet_name=ws.title, headers=headers, rows=rows, column_index=column_index)


# ---------------------------------------------------------------------------
# Compute
# ---------------------------------------------------------------------------

@dataclass
class RebaseResultRow:
    target_row_index: int  # 1-based row in the output excel
    barcode: str | None
    product_code: str | None
    name: str | None
    old_csp: float | None  # from Excel A (matched)
    new_csp: float | None  # computed; None if unmatched
    max_allowed: float | None
    capped: bool
    matched: bool
    note: str = ""


@dataclass
class RebaseResult:
    rows: list[RebaseResultRow] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(1 for r in self.rows if r.matched)

    @property
    def unmatched_count(self) -> int:
        return sum(1 for r in self.rows if not r.matched)

    @property
    def capped_count(self) -> int:
        return sum(1 for r in self.rows if r.capped)


def compute_factors(discount_pct: float, ty_cover_pct: float) -> tuple[float, float]:
    """Return (clientFactor, pscFactor). Inputs are percentages (e.g. 35.0, 25.0)."""
    d = discount_pct / 100.0
    c = ty_cover_pct / 100.0
    return (1 - d), (1 - d * (1 - c))


def rebase(
    source: ExcelDoc,
    target: ExcelDoc | None,
    old_discount: float,
    old_ty_cover: float,
    new_discount: float,
    new_ty_cover: float,
) -> RebaseResult:
    """
    Compute new CSPs.
    If `target` is None we treat `source` as the layout to mirror (generate-mode),
    so every source row becomes a target row.
    """
    cf_old, pf_old = compute_factors(old_discount, old_ty_cover)
    cf_new, pf_new = compute_factors(new_discount, new_ty_cover)

    if cf_old <= 0 or pf_old <= 0 or cf_new <= 0 or pf_new <= 0:
        raise ValueError("Campaign parameters yield a non-positive factor; check your inputs.")

    rebase_ratio = (pf_old / pf_new)

    # Build lookup from source: barcode → row, product_code → row
    by_barcode: dict[str, ExcelRow] = {}
    by_code: dict[str, ExcelRow] = {}
    for sr in source.rows:
        if sr.barcode:
            by_barcode.setdefault(sr.barcode, sr)
        if sr.product_code:
            by_code.setdefault(sr.product_code, sr)

    rows_to_iterate = target.rows if target is not None else source.rows
    result = RebaseResult()
    for tr in rows_to_iterate:
        match: ExcelRow | None = None
        if tr.barcode and tr.barcode in by_barcode:
            match = by_barcode[tr.barcode]
        elif tr.product_code and tr.product_code in by_code:
            match = by_code[tr.product_code]

        old_csp = match.campaign_sale_price if match else None
        new_csp: float | None = None
        capped = False
        note = ""

        if old_csp is not None:
            raw_new = round(old_csp * rebase_ratio, 2)
            if tr.max_allowed_price is not None and raw_new > tr.max_allowed_price:
                new_csp = round(tr.max_allowed_price - 0.01, 2)
                capped = True
                note = f"Capped at Max−0.01 (computed {raw_new:.2f} > max {tr.max_allowed_price:.2f})"
            else:
                new_csp = raw_new
        elif match is None:
            note = "No match in Excel A"
        else:
            note = "Match found but Excel A has no Campaign Sale Price"

        result.rows.append(RebaseResultRow(
            target_row_index=tr.row_index,
            barcode=tr.barcode,
            product_code=tr.product_code,
            name=tr.name,
            old_csp=old_csp,
            new_csp=new_csp,
            max_allowed=tr.max_allowed_price,
            capped=capped,
            matched=match is not None,
            note=note,
        ))
    return result


# ---------------------------------------------------------------------------
# Excel writing
# ---------------------------------------------------------------------------

def write_filled_excel(
    template: ExcelDoc,
    result: RebaseResult,
    out_path: str | Path,
    kept_row_indices: set[int] | None = None,
) -> Path:
    """
    Re-open the template xlsx, fill the Campaign Sale Price column for each
    matched row, leave unmatched cells empty, and save to `out_path`.
    Preserves the workbook's original styling.

    If `kept_row_indices` is provided, any data row whose `target_row_index` is
    NOT in the set is dropped from the output entirely.
    """
    out_path = Path(out_path)
    wb = load_workbook(template.path)
    ws = wb.active

    csp_col_idx_0 = template.column_index.get("campaign_sale_price")
    if csp_col_idx_0 is None:
        raise ValueError(f"{template.path.name}: 'Campaign Sale Price' column not found, cannot write.")
    csp_col_excel = csp_col_idx_0 + 1

    by_row = {r.target_row_index: r for r in result.rows}

    # Fill CSPs for rows we'll keep.
    for excel_row, rr in by_row.items():
        if kept_row_indices is None or excel_row in kept_row_indices:
            ws.cell(row=excel_row, column=csp_col_excel).value = rr.new_csp

    # Drop unchecked rows (bottom-up, so indices don't shift while deleting).
    if kept_row_indices is not None:
        rows_to_delete = sorted(
            (idx for idx in by_row.keys() if idx not in kept_row_indices),
            reverse=True,
        )
        for excel_row in rows_to_delete:
            ws.delete_rows(excel_row, amount=1)

    wb.save(out_path)
    return out_path


def write_generated_excel(
    source: ExcelDoc,
    result: RebaseResult,
    out_path: str | Path,
    kept_row_indices: set[int] | None = None,
) -> Path:
    """
    Generate a new Excel mirroring Excel A's layout, copying every cell from the
    source workbook and overwriting the Campaign Sale Price column with computed values.

    If `kept_row_indices` is provided, only data rows whose `target_row_index` is
    in the set are emitted; the header row is always preserved and remaining rows
    are renumbered contiguously starting at 2.
    """
    out_path = Path(out_path)
    src_wb = load_workbook(source.path)
    src_ws = src_wb.active

    out_wb = Workbook()
    out_ws = out_wb.active
    out_ws.title = src_ws.title

    csp_col_idx_0 = source.column_index.get("campaign_sale_price")
    if csp_col_idx_0 is None:
        raise ValueError(f"{source.path.name}: 'Campaign Sale Price' column not found.")
    csp_col_excel = csp_col_idx_0 + 1

    by_row = {r.target_row_index: r for r in result.rows}
    out_row = 1

    for src_row in src_ws.iter_rows():
        src_row_num = src_row[0].row
        is_header = src_row_num == 1
        if not is_header and kept_row_indices is not None and src_row_num not in kept_row_indices:
            continue

        for src_cell in src_row:
            new_cell = out_ws.cell(row=out_row, column=src_cell.column, value=src_cell.value)
            if src_cell.has_style:
                new_cell.font = copy(src_cell.font)
                new_cell.fill = copy(src_cell.fill)
                new_cell.border = copy(src_cell.border)
                new_cell.alignment = copy(src_cell.alignment)
                new_cell.number_format = src_cell.number_format

        if not is_header:
            rr = by_row.get(src_row_num)
            if rr is not None:
                out_ws.cell(row=out_row, column=csp_col_excel).value = rr.new_csp

        out_row += 1

    # Copy column widths
    for col_letter, dim in src_ws.column_dimensions.items():
        out_ws.column_dimensions[col_letter].width = dim.width

    out_wb.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _to_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "null"):
        return None
    # openpyxl returns floats for numeric cells; barcodes that come in as floats
    # like 1.23456789e+12 should be normalized to int-string
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return s


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
