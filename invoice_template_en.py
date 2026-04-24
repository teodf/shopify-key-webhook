from __future__ import annotations

import argparse
import base64
import json
import subprocess
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path
from typing import Any


TWOPLACES = Decimal("0.01")


def money(value: str | int | float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def amount(value: Decimal, currency: str = "EUR") -> str:
    return f"{money(value):,.2f} {currency}"


def percent(value: Decimal) -> str:
    rounded = value.quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    return f"{format(rounded.normalize(), 'f')} %"


def money_close(left: Decimal, right: Decimal, tolerance: Decimal = Decimal("0.01")) -> bool:
    return abs(money(left) - money(right)) <= tolerance


def html_lines(lines: list[str]) -> str:
    return "<br>".join(escape(line) for line in lines if line)


def format_date(value: date | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    months = [
        "Jan",
        "Feb",
        "Mar",
        "Apr",
        "May",
        "Jun",
        "Jul",
        "Aug",
        "Sep",
        "Oct",
        "Nov",
        "Dec",
    ]
    return f"{value.day} {months[value.month - 1]} {value.year}"


def image_data_uri(path: Path | None) -> str | None:
    if path is None or not path.exists():
        return None

    mime_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
    }
    mime_type = mime_types.get(path.suffix.lower(), "application/octet-stream")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def presentment_amount(data: dict[str, Any], *keys: str) -> Decimal | None:
    value = nested(data, *keys, "presentmentMoney", "amount")
    if value is None:
        return None
    return money(value)


def presentment_currency(data: dict[str, Any], default: str = "EUR") -> str:
    paths = [
        ("totalPriceSet", "presentmentMoney", "currencyCode"),
        ("currentTotalPriceSet", "presentmentMoney", "currencyCode"),
        ("currentSubtotalPriceSet", "presentmentMoney", "currencyCode"),
    ]
    for path in paths:
        value = nested(data, *path)
        if value:
            return str(value)
    return default


def parse_shopify_date(value: str | None) -> date:
    if not value:
        return date.today()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def clean_parts(*parts: Any) -> list[str]:
    return [str(part).strip() for part in parts if part is not None and str(part).strip()]


def shopify_party_from_address(
    address: dict[str, Any] | None,
    fallback_customer: dict[str, Any] | None = None,
) -> Party:
    address = address or {}
    fallback_customer = fallback_customer or {}
    name = " ".join(
        clean_parts(
            address.get("firstName") or fallback_customer.get("firstName"),
            address.get("lastName") or fallback_customer.get("lastName"),
        )
    )
    company = address.get("company")
    address_lines = clean_parts(
        company,
        address.get("address1"),
        address.get("address2"),
        " ".join(clean_parts(address.get("zip"), address.get("city"))),
        address.get("province"),
        address.get("country"),
    )
    return Party(
        name=name or company or fallback_customer.get("email") or "Customer",
        address_lines=address_lines,
        email=fallback_customer.get("email"),
        phone=address.get("phone") or fallback_customer.get("phone"),
    )


def default_seller() -> Party:
    return Party(
        name="CAPTEUR 4L",
        address_lines=["3340 Rue Avon", "Saint-Hubert", "J3Y 5A8", "Canada"],
    )


def invoice_country_code(order: dict[str, Any]) -> str:
    address = order.get("shippingAddress") or order.get("billingAddress") or {}
    country = str(address.get("country") or "").strip()
    known_codes = {
        "canada": "CA",
        "france": "FR",
        "united states": "US",
        "usa": "US",
        "united kingdom": "GB",
        "uk": "GB",
        "germany": "DE",
        "spain": "ES",
        "italy": "IT",
    }
    if len(country) == 2:
        return country.upper()
    return known_codes.get(country.lower(), country[:2].upper() or "XX")


def line_description(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "Item").strip()
    variant = str(item.get("variantTitle") or "").strip()

    details = []
    if variant and variant.lower() != "default title":
        details.append(variant)

    return f"{title} - {', '.join(details)}" if details else title


def original_line_total(item: dict[str, Any]) -> Decimal:
    total = presentment_amount(item, "originalTotalSet")
    if total is not None:
        return total

    unit = presentment_amount(item, "originalUnitPriceSet") or Decimal("0.00")
    quantity = Decimal(str(item.get("currentQuantity") or 0))
    return money(unit * quantity)


def shopify_tax_lines(order: dict[str, Any]) -> list[TaxLine]:
    taxes = []
    for tax_line in order.get("taxLines", []):
        amount_value = presentment_amount(tax_line, "priceSet")
        if amount_value is None:
            continue
        taxes.append(
            TaxLine(
                title=str(tax_line.get("title") or "Tax"),
                rate=Decimal(str(tax_line.get("ratePercentage") or 0)),
                amount=amount_value,
            )
        )
    return taxes


def discount_title(order: dict[str, Any]) -> str:
    for discount in order.get("discountApplications", []):
        typename = discount.get("__typename")
        candidates = [
            discount.get("title"),
            discount.get("code"),
            nested(discount, str(typename), "title") if typename else None,
            nested(discount, str(typename), "code") if typename else None,
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
    return "Discount applied"


def infer_tax_included(
    current_total: Decimal | None,
    current_subtotal: Decimal,
    shipping_total: Decimal,
    tax_total: Decimal,
) -> bool:
    if current_total is None or not tax_total:
        return False

    total_if_tax_included = current_subtotal + shipping_total
    total_if_tax_excluded = current_subtotal + shipping_total + tax_total

    if money_close(current_total, total_if_tax_included):
        return True
    if money_close(current_total, total_if_tax_excluded):
        return False
    return False


def resolved_shipping_total(
    explicit_shipping: Decimal,
    current_total: Decimal | None,
    current_subtotal: Decimal,
    current_tax: Decimal,
    tax_included: bool,
) -> Decimal:
    if current_total is None:
        return explicit_shipping

    expected_total = (
        current_subtotal + explicit_shipping
        if tax_included
        else current_subtotal + explicit_shipping + current_tax
    )
    if money_close(current_total, expected_total):
        return explicit_shipping

    tax_component = Decimal("0.00") if tax_included else current_tax
    return money(max(current_total - current_subtotal - tax_component, Decimal("0.00")))


def invoice_from_shopify_payload(payload: dict[str, Any]) -> Invoice:
    order = payload["order"]
    currency = presentment_currency(order)
    issue_date = parse_shopify_date(order.get("createdAt"))
    seller = default_seller()
    tax_lines = shopify_tax_lines(order)

    billing = shopify_party_from_address(order.get("billingAddress"), order.get("customer"))
    shipping = shopify_party_from_address(
        order.get("shippingAddress") or order.get("billingAddress"),
        order.get("customer"),
    )

    original_lines: list[InvoiceLine] = []
    original_subtotal = Decimal("0.00")
    for item in order.get("lineItems", []):
        quantity = Decimal(str(item.get("currentQuantity") or 0))
        original_total = original_line_total(item)
        original_subtotal += original_total
        unit_price = money(original_total / quantity) if quantity else Decimal("0.00")
        original_lines.append(
            InvoiceLine(
                description=line_description(item),
                quantity=quantity,
                unit_price_ex_vat=unit_price,
                vat_rate=Decimal("0"),
            )
        )

    current_subtotal = presentment_amount(order, "currentSubtotalPriceSet")
    if current_subtotal is None:
        current_subtotal = money(original_subtotal)

    current_tax = presentment_amount(order, "currentTotalTaxSet") or Decimal("0.00")
    current_total = presentment_amount(order, "currentTotalPriceSet")
    explicit_shipping = presentment_amount(order, "totalShippingPriceSet") or Decimal("0.00")
    tax_included = infer_tax_included(
        current_total,
        current_subtotal,
        explicit_shipping,
        current_tax,
    )
    shipping_total = resolved_shipping_total(
        explicit_shipping,
        current_total,
        current_subtotal,
        current_tax,
        tax_included,
    )

    combined_tax_rate = money(sum((tax.rate for tax in tax_lines), Decimal("0")))
    vat_rate = (
        combined_tax_rate
        if tax_lines
        else
        (current_tax / current_subtotal * Decimal("100")).quantize(TWOPLACES)
        if current_subtotal and current_tax
        else Decimal("0")
    )

    lines = [
        InvoiceLine(
            description=line.description,
            quantity=line.quantity,
            unit_price_ex_vat=line.unit_price_ex_vat,
            vat_rate=vat_rate,
        )
        for line in original_lines
    ]

    discount = money(original_subtotal - current_subtotal)
    if discount > 0:
        lines.append(
            InvoiceLine(
                description=discount_title(order),
                quantity=Decimal("1"),
                unit_price_ex_vat=-discount,
                vat_rate=vat_rate,
            )
        )

    if not lines:
        lines.append(
            InvoiceLine(
                description="Shopify order",
                quantity=Decimal("1"),
                unit_price_ex_vat=current_subtotal,
                vat_rate=vat_rate,
            )
        )

    order_name = str(order.get("name") or "").strip()
    invoice_suffix = order_name.lstrip("#") or str(payload.get("orderId", "ORDER"))
    country_code = invoice_country_code(order)

    return Invoice(
        invoice_number=f"INV-{country_code}-{invoice_suffix}",
        issue_date=issue_date,
        supply_date=issue_date,
        seller=seller,
        bill_to=billing,
        ship_to=shipping,
        merchant=seller,
        lines=lines,
        tax_lines=tax_lines,
        tax_included=tax_included,
        currency=currency,
        additional_details="",
        shipping_ex_vat=shipping_total,
        shipping_vat_rate=Decimal("0"),
        footer_note=f"Provided by: {seller.name}",
    )


@dataclass(frozen=True)
class Party:
    name: str
    address_lines: list[str] = field(default_factory=list)
    email: str | None = None
    phone: str | None = None
    vat_id: str | None = None
    company_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Party":
        return cls(
            name=data["name"],
            address_lines=list(data.get("address_lines", [])),
            email=data.get("email"),
            phone=data.get("phone"),
            vat_id=data.get("vat_id"),
            company_id=data.get("company_id"),
        )


@dataclass(frozen=True)
class InvoiceLine:
    description: str
    quantity: Decimal
    unit_price_ex_vat: Decimal
    vat_rate: Decimal

    @property
    def net_total(self) -> Decimal:
        return money(self.quantity * self.unit_price_ex_vat)

    @property
    def vat_total(self) -> Decimal:
        return money(self.net_total * self.vat_rate / Decimal("100"))

    @property
    def gross_total(self) -> Decimal:
        return money(self.net_total + self.vat_total)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InvoiceLine":
        return cls(
            description=data["description"],
            quantity=Decimal(str(data["quantity"])),
            unit_price_ex_vat=money(data["unit_price_ex_vat"]),
            vat_rate=Decimal(str(data.get("vat_rate", "0"))),
        )


@dataclass(frozen=True)
class TaxLine:
    title: str
    rate: Decimal
    amount: Decimal

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaxLine":
        return cls(
            title=data["title"],
            rate=Decimal(str(data.get("rate", "0"))),
            amount=money(data.get("amount", "0")),
        )


@dataclass(frozen=True)
class Invoice:
    invoice_number: str
    issue_date: date
    seller: Party
    bill_to: Party
    ship_to: Party | None
    merchant: Party
    lines: list[InvoiceLine]
    tax_lines: list[TaxLine] = field(default_factory=list)
    tax_included: bool = False
    currency: str = "EUR"
    supply_date: date | None = None
    additional_details: str = ""
    shipping_ex_vat: Decimal = Decimal("0.00")
    shipping_vat_rate: Decimal = Decimal("0.00")
    footer_note: str = ""

    @property
    def subtotal_ex_vat(self) -> Decimal:
        return money(sum((line.net_total for line in self.lines), Decimal("0.00")))

    @property
    def vat_total(self) -> Decimal:
        if self.tax_lines:
            return money(sum((tax.amount for tax in self.tax_lines), Decimal("0.00")))
        line_vat = sum((line.vat_total for line in self.lines), Decimal("0.00"))
        shipping_vat = self.shipping_vat_total
        return money(line_vat + shipping_vat)

    @property
    def shipping_vat_total(self) -> Decimal:
        return money(self.shipping_ex_vat * self.shipping_vat_rate / Decimal("100"))

    @property
    def total(self) -> Decimal:
        if self.tax_included:
            return money(self.subtotal_ex_vat + self.shipping_ex_vat)
        return money(self.subtotal_ex_vat + self.vat_total + self.shipping_ex_vat)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Invoice":
        return cls(
            invoice_number=data["invoice_number"],
            issue_date=date.fromisoformat(data["issue_date"]),
            supply_date=(
                date.fromisoformat(data["supply_date"])
                if data.get("supply_date")
                else None
            ),
            seller=Party.from_dict(data["seller"]),
            bill_to=Party.from_dict(data["bill_to"]),
            ship_to=Party.from_dict(data["ship_to"]) if data.get("ship_to") else None,
            merchant=Party.from_dict(data["merchant"]),
            lines=[InvoiceLine.from_dict(item) for item in data["lines"]],
            tax_lines=[TaxLine.from_dict(item) for item in data.get("tax_lines", [])],
            tax_included=bool(data.get("tax_included", False)),
            currency=data.get("currency", "EUR"),
            additional_details=data.get("additional_details", ""),
            shipping_ex_vat=money(data.get("shipping_ex_vat", "0")),
            shipping_vat_rate=Decimal(str(data.get("shipping_vat_rate", "0"))),
            footer_note=data.get("footer_note", ""),
        )


def render_party(party: Party, include_ids: bool = False) -> str:
    parts = [f"<strong>{escape(party.name)}</strong>"]
    if party.address_lines:
        parts.append(html_lines(party.address_lines))
    if party.email:
        parts.append(escape(party.email))
    if party.phone:
        parts.append(escape(party.phone))
    if include_ids and party.company_id:
        parts.append(f"Company ID: {escape(party.company_id)}")
    return "<p>" + "</p><p>".join(parts) + "</p>"


def render_invoice_html(invoice: Invoice, logo_src: str | None = None) -> str:
    rows = []
    for line in invoice.lines:
        rows.append(
            f"""
            <tr>
              <td>{escape(line.description)}</td>
              <td class="num">{line.quantity:g}</td>
              <td class="num">{amount(line.unit_price_ex_vat, invoice.currency)}</td>
              <td class="num">{percent(line.vat_rate)}</td>
              <td class="num">{amount(line.net_total, invoice.currency)}</td>
            </tr>
            """
        )

    ship_to = invoice.ship_to or invoice.bill_to
    additional_details = (
        escape(invoice.additional_details) if invoice.additional_details else "&nbsp;"
    )
    footer_note = (
        escape(invoice.footer_note)
        if invoice.footer_note
        else invoice.seller.name
    )
    logo_html = (
        f'<img class="logo" src="{escape(logo_src)}" alt="Footbar logo">'
        if logo_src
        else '<div class="logo-fallback" aria-label="Footbar logo"></div>'
    )
    if invoice.tax_lines:
        tax_rows = "\n".join(
            f"""
      <div class="totals-row">
        <span>{escape(tax.title)} ({percent(tax.rate)}{", included" if invoice.tax_included else ""})</span>
        <span>{amount(tax.amount, invoice.currency)}</span>
      </div>
            """
            for tax in invoice.tax_lines
        )
    else:
        tax_rows = f"""
      <div class="totals-row">
        <span>{"Tax included" if invoice.tax_included else "Tax"}</span>
        <span>{amount(invoice.vat_total, invoice.currency)}</span>
      </div>
        """

    shipping_vat_row = ""
    if not invoice.tax_lines and invoice.shipping_vat_total:
        shipping_vat_row = f"""
      <div class="totals-row">
        <span>Shipping tax ({percent(invoice.shipping_vat_rate)})</span>
        <span>{amount(invoice.shipping_vat_total, invoice.currency)}</span>
      </div>
        """
    subtotal_label = "Subtotal (tax incl.)" if invoice.tax_included else "Subtotal"
    shipping_label = "Shipping (tax incl.)" if invoice.tax_included else "Shipping"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Invoice {escape(invoice.invoice_number)}</title>
  <style>
    @page {{
      size: A4;
      margin: 0;
    }}

    * {{
      box-sizing: border-box;
    }}

    body {{
      margin: 0;
      background: #ffffff;
      color: #202124;
      font-family: Arial, Helvetica, sans-serif;
      font-size: 11px;
      line-height: 1.45;
    }}

    .page {{
      width: 210mm;
      min-height: 297mm;
      margin: 0 auto;
      background: #ffffff;
      color: #202124;
      padding: 12mm 8mm 8mm;
      position: relative;
    }}

    .top {{
      display: grid;
      grid-template-columns: 1.1fr 1fr 1fr;
      gap: 12mm;
      align-items: start;
      border-bottom: 1px solid #f2f2f2;
      padding-bottom: 7mm;
    }}

    h1 {{
      margin: 0;
      color: #33363b;
      font-size: 24px;
      letter-spacing: 0;
      font-weight: 800;
    }}

    h2 {{
      margin: 0 0 5px;
      color: #3c3f45;
      font-size: 11px;
      font-weight: 800;
    }}

    p {{
      margin: 0 0 4px;
    }}

    strong {{
      color: #3c3f45;
      font-weight: 800;
    }}

    .right {{
      text-align: right;
    }}

    .brand-strip {{
      display: grid;
      grid-template-columns: 1.3fr 1fr;
      border-bottom: 1px solid #d8dadd;
    }}

    .brand {{
      min-height: 30mm;
      padding: 7mm 0;
      display: flex;
      align-items: center;
      gap: 9mm;
    }}

    .logo {{
      width: 24mm;
      height: auto;
      display: block;
      flex: 0 0 auto;
    }}

    .logo-fallback {{
      width: 24mm;
      height: 18mm;
      border-radius: 50%;
      background: #ffa875;
      flex: 0 0 auto;
    }}

    .details-box {{
      background: #f4f4f4;
      color: #2f3136;
      padding: 5mm 7mm;
      min-height: 30mm;
    }}

    .dates {{
      padding-top: 5mm;
    }}

    .parties {{
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 10mm;
      padding: 4mm 0 16mm;
    }}

    table {{
      width: 100%;
      border-collapse: collapse;
    }}

    th {{
      color: #3c3f45;
      font-weight: 800;
      text-align: left;
      border-bottom: 1px solid #f2f2f2;
      padding: 0 0 3mm;
    }}

    td {{
      border-bottom: 1px solid #f2f2f2;
      padding: 3mm 0;
      color: #4a4d54;
    }}

    .num {{
      text-align: right;
      white-space: nowrap;
    }}

    .totals {{
      width: 88mm;
      margin-left: auto;
      margin-top: 8mm;
    }}

    .totals-row {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8mm;
      border-bottom: 1px solid #f2f2f2;
      padding: 2.2mm 0;
    }}

    .totals-row.total {{
      color: #3c3f45;
      font-weight: 800;
    }}

    .footer {{
      position: absolute;
      left: 8mm;
      right: 8mm;
      bottom: 8mm;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 8mm;
      font-size: 10px;
    }}

    @media print {{
      body {{
        background: #ffffff;
      }}

      .page {{
        margin: 0;
      }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="top">
      <div>
        <h1>INVOICE</h1>
      </div>
      <div>
        <h2>Invoice number</h2>
        <p><strong>{escape(invoice.invoice_number)}</strong></p>
      </div>
      <div class="right">
        <h2>Invoice total</h2>
        <p><strong>{amount(invoice.total, invoice.currency)}</strong></p>
      </div>
    </section>

    <section class="brand-strip">
      <div class="brand">
        {logo_html}
        <div class="dates">
          <h2>Issue date</h2>
          <p>{format_date(invoice.issue_date)}</p>
          <h2>Supply date</h2>
          <p>{format_date(invoice.supply_date or invoice.issue_date)}</p>
        </div>
      </div>
      <div class="details-box">
        <h2>Additional details</h2>
        <p>{additional_details}</p>
      </div>
    </section>

    <section class="parties">
      <div>
        <h2>Bill to</h2>
        {render_party(invoice.bill_to)}
      </div>
      <div>
        <h2>Ship to</h2>
        {render_party(ship_to)}
      </div>
      <div class="right">
        <h2>Merchant</h2>
        {render_party(invoice.merchant, include_ids=True)}
      </div>
    </section>

    <table>
      <thead>
        <tr>
          <th>Description</th>
          <th class="num">Quantity</th>
          <th class="num">Unit price</th>
          <th class="num">Tax rate</th>
          <th class="num">Amount</th>
        </tr>
      </thead>
      <tbody>
        {"".join(rows)}
      </tbody>
    </table>

    <section class="totals">
      <div class="totals-row">
        <span>{subtotal_label}</span>
        <span>{amount(invoice.subtotal_ex_vat, invoice.currency)}</span>
      </div>
      {tax_rows}
      <div class="totals-row">
        <span>{shipping_label}</span>
        <span>{amount(invoice.shipping_ex_vat, invoice.currency)}</span>
      </div>
      {shipping_vat_row}
      <div class="totals-row total">
        <span>Total</span>
        <span>{amount(invoice.total, invoice.currency)}</span>
      </div>
    </section>

    <footer class="footer">
      <div>{footer_note}</div>
      <div>Issued on {format_date(invoice.issue_date)}<br>Page 1 of 1 for {escape(invoice.invoice_number)}</div>
    </footer>
  </main>
</body>
</html>
"""


def sample_invoice_from_pdf() -> Invoice:
    seller = default_seller()
    customer = Party(
        name="THIBAULT CHEVALLIER",
        address_lines=["58 rue de Clichy", "75009 Paris 09", "France"],
    )
    return Invoice(
        invoice_number="INV-FR-124",
        issue_date=date(2026, 4, 21),
        supply_date=date(2026, 4, 21),
        seller=seller,
        bill_to=customer,
        ship_to=customer,
        merchant=seller,
        additional_details="",
        lines=[
            InvoiceLine(
                description="Footbar sensor - Black",
                quantity=Decimal("1"),
                unit_price_ex_vat=money("49.17"),
                vat_rate=Decimal("20"),
            )
        ],
        shipping_ex_vat=money("0"),
        shipping_vat_rate=Decimal("0"),
        footer_note="Provided by: CAPTEUR 4L",
    )


def load_invoice(path: Path) -> Invoice:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if isinstance(data, dict) and "order" in data:
        return invoice_from_shopify_payload(data)
    return Invoice.from_dict(data)


def write_invoice_html(
    invoice: Invoice, output_path: Path, logo_path: Path | None = None
) -> None:
    logo_src = image_data_uri(logo_path)
    output_path.write_text(render_invoice_html(invoice, logo_src), encoding="utf-8")


def write_invoice_pdf(
    html_path: Path,
    pdf_path: Path,
    chrome_path: Path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
) -> None:
    if not chrome_path.exists():
        raise FileNotFoundError(
            f"Chrome executable not found at {chrome_path}. "
            "Install Chrome or pass --chrome with the executable path."
        )

    html_uri = html_path.resolve().as_uri()
    subprocess.run(
        [
            str(chrome_path),
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path.resolve()}",
            html_uri,
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render an English invoice template inspired by vat_invoice_INV-FR-124.pdf."
    )
    parser.add_argument(
        "--data",
        type=Path,
        help=(
            "Optional JSON file containing invoice data or the Shopify order payload. "
            "Uses the PDF sample when omitted."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("invoice_template_en.html"),
        help="HTML output path.",
    )
    parser.add_argument(
        "--logo",
        type=Path,
        default=Path("logo-footbar.png"),
        help="Logo image to embed in the generated HTML.",
    )
    parser.add_argument(
        "--pdf",
        nargs="?",
        const="",
        default=None,
        help=(
            "Also render an A4 PDF. Pass a path, or pass --pdf without a value "
            "to use the HTML output name with .pdf."
        ),
    )
    parser.add_argument(
        "--chrome",
        type=Path,
        default=Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        help="Chrome executable used for PDF rendering.",
    )
    args = parser.parse_args()

    invoice = load_invoice(args.data) if args.data else sample_invoice_from_pdf()
    write_invoice_html(invoice, args.output, args.logo)
    print(f"Wrote {args.output}")

    if args.pdf is not None:
        pdf_path = args.output.with_suffix(".pdf") if args.pdf == "" else Path(args.pdf)
        write_invoice_pdf(args.output, pdf_path, args.chrome)
        print(f"Wrote {pdf_path}")


if __name__ == "__main__":
    main()
