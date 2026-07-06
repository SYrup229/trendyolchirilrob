import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.storage.repo import Repo


def render_invoice_html(repo: Repo, invoice_row: dict[str, Any]) -> str:
    try:
        record = json.loads(invoice_row.get("raw_response") or "{}")
    except ValueError:
        record = {}

    company = _company_block(repo)
    buyer = record.get("buyer") or {}
    currency = record.get("currency") or ""
    lines = record.get("lines") or []
    shipping = float(record.get("shipping_amount") or 0)

    # Render itemized lines: each product gets a row at the LIST price; if it has a
    # per-unit discount, a sub-row immediately under it shows the discount amount.
    line_rows = []
    idx = 0
    total_discount = 0.0
    for l in lines:
        idx += 1
        line_rows.append(_product_row(idx, l, currency))
        disc_per_unit = float(l.get("discount_per_unit") or 0)
        qty = int(l.get("quantity") or 0)
        if disc_per_unit > 0 and qty > 0:
            line_rows.append(_discount_row(l, currency))
            total_discount += disc_per_unit * qty

    # Optional shipping line (only when Trendyol returned a non-zero charge).
    if shipping > 0:
        idx += 1
        line_rows.append(_shipping_row(idx, shipping, currency))

    line_rows_html = "".join(line_rows)

    # VAT summary (group line totals by VAT rate, on the customer-paid amount).
    vat_breakdown: dict[float, float] = {}
    subtotal_net = 0.0
    for l in lines:
        rate = float(l.get("vat_percent") or 0)
        gross = float(l.get("line_total_gross") or 0)
        net = gross / (1 + rate / 100) if rate else gross
        vat_amount = gross - net
        vat_breakdown[rate] = vat_breakdown.get(rate, 0.0) + vat_amount
        subtotal_net += net
    if shipping > 0:
        # Treat shipping as 21% (RO standard transport VAT).
        rate = 21.0
        net = shipping / (1 + rate / 100)
        vat_amount = shipping - net
        vat_breakdown[rate] = vat_breakdown.get(rate, 0.0) + vat_amount
        subtotal_net += net

    vat_rows = "".join(
        f"<tr><td>TVA {rate:g}%:</td><td class='num'>{amt:.2f} {html.escape(currency)}</td></tr>"
        for rate, amt in sorted(vat_breakdown.items())
    )
    discount_total_row = ""
    if total_discount > 0:
        discount_total_row = (
            f"<tr><td>Total reduceri:</td>"
            f"<td class='num'>−{total_discount:.2f} {html.escape(currency)}</td></tr>"
        )

    total = float(record.get("total_gross") or 0)

    return _TEMPLATE.format(
        title=html.escape(f"Factura {record.get('series','')} {record.get('number','')}"),
        series=html.escape(record.get("series", "")),
        number=html.escape(record.get("number", "")),
        issue_date=html.escape(record.get("issue_date", "")),
        order_number=html.escape(record.get("order_number", "")),
        order_status=html.escape(record.get("order_status") or "—"),
        company_name=html.escape(company["name"]),
        company_cif=html.escape(company["cif"]),
        company_address=html.escape(company["address"]).replace("\n", "<br>"),
        company_extra=html.escape(company["extra"]).replace("\n", "<br>"),
        buyer_name=html.escape(buyer.get("name") or ""),
        buyer_email=html.escape(buyer.get("email") or "—"),
        buyer_address=html.escape(buyer.get("address") or "—"),
        buyer_city=html.escape(
            (buyer.get("city") or "")
            + (f", jud. {buyer['state']}" if buyer.get("state") else "")
        ),
        buyer_country=html.escape(buyer.get("country") or ""),
        currency=html.escape(currency),
        subtotal_net=f"{subtotal_net:.2f}",
        total=f"{total:.2f}",
        discount_total_row=discount_total_row,
        vat_rows=vat_rows,
        line_rows=line_rows_html,
        mentions=html.escape(f"Trendyol order {record.get('order_number','')}"),
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )


def export_invoice_html(repo: Repo, invoice_row: dict[str, Any], out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.write_text(render_invoice_html(repo, invoice_row), encoding="utf-8")
    return out_path


def suggested_filename(invoice_row: dict[str, Any]) -> str:
    try:
        record = json.loads(invoice_row.get("raw_response") or "{}")
    except ValueError:
        record = {}
    series = record.get("series") or invoice_row.get("series") or "INV"
    number = record.get("number") or invoice_row.get("number") or "0000"
    safe = f"{series}_{number}".replace("/", "_").replace("\\", "_").replace(" ", "_")
    return f"{safe}.html"


def _company_block(repo: Repo) -> dict[str, str]:
    return {
        "name": repo.get_setting("company_name") or "(Seteaza numele companiei in Setari)",
        "cif": repo.oblio_cif() or "",
        "address": repo.get_setting("company_address") or "",
        "extra": repo.get_setting("company_extra") or "",
    }


def _product_row(idx: int, line: dict[str, Any], currency: str) -> str:
    name = html.escape(line.get("oblio_name") or line.get("original_title") or "")
    code = html.escape(line.get("oblio_code") or "")
    qty = int(line.get("quantity") or 0)
    list_price = float(line.get("list_unit_price_gross") or line.get("unit_price_gross") or 0)
    vat = float(line.get("vat_percent") or 0)
    list_line_total = round(list_price * qty, 2)
    return (
        f"<tr>"
        f"<td>{idx}</td>"
        f"<td>{name}</td>"
        f"<td>{code}</td>"
        f"<td class='num'>{qty}</td>"
        f"<td class='num'>{list_price:.2f}</td>"
        f"<td class='num'>{vat:g}%</td>"
        f"<td class='num'>{list_line_total:.2f}</td>"
        f"</tr>"
    )


def _discount_row(line: dict[str, Any], currency: str) -> str:
    qty = int(line.get("quantity") or 0)
    disc_per_unit = float(line.get("discount_per_unit") or 0)
    disc_line_total = round(disc_per_unit * qty, 2)
    vat = float(line.get("vat_percent") or 0)
    return (
        f"<tr class='discount-row'>"
        f"<td></td>"
        f"<td colspan='2' style='font-style: italic; color: #b45309;'>↳ Reducere</td>"
        f"<td class='num'>{qty}</td>"
        f"<td class='num'>−{disc_per_unit:.2f}</td>"
        f"<td class='num'>{vat:g}%</td>"
        f"<td class='num' style='color: #b45309;'>−{disc_line_total:.2f}</td>"
        f"</tr>"
    )


def _shipping_row(idx: int, shipping: float, currency: str) -> str:
    return (
        f"<tr>"
        f"<td>{idx}</td>"
        f"<td>Transport</td>"
        f"<td>SHIPPING</td>"
        f"<td class='num'>1</td>"
        f"<td class='num'>{shipping:.2f}</td>"
        f"<td class='num'>21%</td>"
        f"<td class='num'>{shipping:.2f}</td>"
        f"</tr>"
    )


_TEMPLATE = """<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  @media print {{ body {{ margin: 0; }} .no-print {{ display: none; }} }}
  body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 40px; color: #111827; background: #fff; }}
  .topbar {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 28px; }}
  .topbar h1 {{ margin: 0; font-size: 30px; letter-spacing: 2px; color: #0f172a; }}
  .topbar .series {{ color: #6b7280; margin-top: 4px; font-size: 14px; }}
  .company {{ text-align: right; font-size: 13px; line-height: 1.5; }}
  .meta-bar {{ background: #f1f5f9; border: 1px solid #e2e8f0; border-radius: 6px; padding: 14px 18px; margin-bottom: 20px; }}
  .meta-bar table {{ width: 100%; border-collapse: collapse; }}
  .meta-bar td {{ padding: 4px 8px; font-size: 13px; }}
  .meta-bar .label {{ color: #64748b; font-weight: 600; width: 160px; }}
  .parties {{ display: flex; gap: 16px; margin-bottom: 24px; }}
  .party {{ flex: 1; background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 6px; padding: 14px 16px; font-size: 13px; line-height: 1.5; }}
  .party h3 {{ margin: 0 0 8px 0; font-size: 12px; color: #475569; text-transform: uppercase; letter-spacing: 1px; }}
  table.lines {{ width: 100%; border-collapse: collapse; }}
  table.lines th {{ background: #1e293b; color: #f8fafc; padding: 9px 10px; font-size: 12px; text-align: left; }}
  table.lines th.num, table.lines td.num {{ text-align: right; }}
  table.lines td {{ padding: 8px 10px; border-bottom: 1px solid #e5e7eb; font-size: 13px; }}
  table.lines tr.discount-row td {{ background: #fef3c7; border-bottom: 1px solid #fcd34d; }}
  .totals {{ display: flex; justify-content: flex-end; margin-top: 18px; }}
  .totals table {{ min-width: 320px; border-collapse: collapse; }}
  .totals td {{ padding: 6px 12px; font-size: 13px; }}
  .totals td.num {{ text-align: right; }}
  .totals tr.grand td {{ background: #1e293b; color: #f8fafc; font-size: 16px; font-weight: bold; padding: 10px 12px; }}
  .mentions {{ margin-top: 28px; color: #6b7280; font-size: 12px; font-style: italic; }}
  .footer {{ margin-top: 40px; padding-top: 14px; border-top: 1px solid #e5e7eb; color: #9ca3af; font-size: 11px; }}
</style>
</head>
<body>
  <div class="topbar">
    <div>
      <h1>FACTURĂ</h1>
      <div class="series">Seria <b>{series}</b> nr. <b>{number}</b></div>
    </div>
    <div class="company">
      <strong>{company_name}</strong><br>
      CIF: {company_cif}<br>
      {company_address}<br>
      {company_extra}
    </div>
  </div>

  <div class="meta-bar">
    <table>
      <tr><td class="label">Data emiterii:</td><td>{issue_date}</td></tr>
      <tr><td class="label">Comandă Trendyol:</td><td>{order_number}</td></tr>
      <tr><td class="label">Status comandă:</td><td>{order_status}</td></tr>
    </table>
  </div>

  <div class="parties">
    <div class="party">
      <h3>Furnizor</h3>
      <strong>{company_name}</strong><br>
      CIF: {company_cif}<br>
      {company_address}<br>
      {company_extra}
    </div>
    <div class="party">
      <h3>Client (persoană fizică)</h3>
      <strong>{buyer_name}</strong><br>
      {buyer_address}<br>
      {buyer_city} {buyer_country}<br>
      {buyer_email}
    </div>
  </div>

  <table class="lines">
    <thead>
      <tr>
        <th>#</th>
        <th>Denumire produs</th>
        <th>Cod</th>
        <th class="num">Cant.</th>
        <th class="num">Preț unitar (cu TVA)</th>
        <th class="num">TVA</th>
        <th class="num">Valoare (cu TVA)</th>
      </tr>
    </thead>
    <tbody>
      {line_rows}
    </tbody>
  </table>

  <div class="totals">
    <table>
      {discount_total_row}
      <tr><td>Total fără TVA:</td><td class="num">{subtotal_net} {currency}</td></tr>
      {vat_rows}
      <tr class="grand"><td>Total de plată:</td><td class="num">{total} {currency}</td></tr>
    </table>
  </div>

  <div class="mentions">{mentions}</div>

  <div class="footer">
    Generat local de aplicația TrendyolFull la {generated_at}. Această factură nu a fost încă transmisă către Oblio.
  </div>
</body>
</html>
"""
