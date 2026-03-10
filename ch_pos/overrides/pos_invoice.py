import frappe
from frappe.utils import flt
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice


class CustomPOSInvoice(POSInvoice):
    """Extends POS Invoice for margin scheme GST on selling side."""

    def validate(self):
        super().validate()
        _apply_margin_scheme(self)


# ── doc_event handlers (called via hooks.py) ─────────────────────


def validate_margin_scheme(doc, method=None):
    """Hook: validate — apply margin scheme GST calculation if applicable."""
    _apply_margin_scheme(doc)


def create_customer_device_records(doc, method=None):
    """Hook: on_submit — register sold devices under customer's account."""
    for item in doc.items:
        serial_nos = (item.serial_no or "").strip()
        if not serial_nos:
            continue

        for sn in serial_nos.split("\n"):
            sn = sn.strip()
            if not sn:
                continue

            # Check if customer device already exists
            if frappe.db.exists("CH Customer Device", {"serial_no": sn, "customer": doc.customer}):
                continue

            device = frappe.new_doc("CH Customer Device")
            device.customer = doc.customer
            device.company = doc.company
            device.serial_no = sn
            device.item_code = item.item_code
            device.item_name = item.item_name
            device.brand = item.brand or frappe.db.get_value("Item", item.item_code, "brand")
            device.purchase_date = doc.posting_date
            device.purchase_invoice = doc.name
            device.purchase_price = flt(item.rate)
            device.purchase_store = doc.pos_profile
            device.current_status = "Active"

            # Attach warranty plan if selected
            if item.get("custom_warranty_plan"):
                plan = frappe.get_cached_doc("CH Warranty Plan", item.custom_warranty_plan)
                device.active_warranty_plan = plan.name
                device.warranty_plan_name = plan.plan_name
                device.warranty_months = plan.duration_months
                device.warranty_expiry = frappe.utils.add_months(doc.posting_date, plan.duration_months)
                device.warranty_status = "Active"

            device.insert(ignore_permissions=True)


def update_serial_lifecycle(doc, method=None):
    """Hook: on_submit — update CH Serial Lifecycle status to 'Sold'."""
    for item in doc.items:
        serial_nos = (item.serial_no or "").strip()
        if not serial_nos:
            continue

        for sn in serial_nos.split("\n"):
            sn = sn.strip()
            if not sn:
                continue

            lifecycle = frappe.db.get_value("CH Serial Lifecycle", {"serial_no": sn})
            if lifecycle:
                frappe.db.set_value("CH Serial Lifecycle", lifecycle, {
                    "lifecycle_status": "Sold",
                    "sale_date": doc.posting_date,
                    "sale_document": doc.name,
                    "sale_rate": flt(item.rate),
                    "customer": doc.customer,
                    "customer_name": doc.customer_name,
                })


def reverse_serial_lifecycle(doc, method=None):
    """Hook: on_cancel — revert serial lifecycle status to 'In Stock'."""
    for item in doc.items:
        serial_nos = (item.serial_no or "").strip()
        if not serial_nos:
            continue

        for sn in serial_nos.split("\n"):
            sn = sn.strip()
            if not sn:
                continue

            lifecycle = frappe.db.get_value("CH Serial Lifecycle", {"serial_no": sn})
            if lifecycle:
                frappe.db.set_value("CH Serial Lifecycle", lifecycle, {
                    "lifecycle_status": "In Stock",
                    "sale_date": None,
                    "sale_document": None,
                    "sale_rate": 0,
                    "customer": None,
                    "customer_name": None,
                })


def update_kiosk_token_status(doc, method=None):
    """Hook: on_submit — mark kiosk token as Converted if linked."""
    token = doc.get("custom_kiosk_token")
    if token:
        frappe.db.set_value("POS Kiosk Token", token, {
            "status": "Converted",
            "converted_invoice": doc.name,
        })


# ── Margin Scheme GST (selling side) ────────────────────────────


def _apply_margin_scheme(doc):
    """Apply margin scheme GST calculation for used/refurbished items.

    Mirrors ch_erp15 purchase-side logic:
    - Taxable margin = selling price - incoming rate (purchase cost)
    - GST applies only on the margin, not the full selling price
    - Exempted value = full amount - margin - GST on margin
    """
    has_margin = False

    for item in doc.items:
        if not item.get("custom_is_margin_item"):
            continue

        has_margin = True
        qty = flt(item.qty)
        rate = flt(item.rate)
        if qty <= 0 or rate <= 0:
            continue

        # Incoming rate (purchase cost) from serial no or valuation
        incoming = _get_incoming_rate(item)
        margin_per_unit = max(0, rate - incoming)

        item.custom_taxable_value = margin_per_unit * qty
        item.custom_exempted_value = 0  # calculated after tax pass

    if not has_margin:
        doc.custom_is_margin_scheme = 0
        return

    doc.custom_is_margin_scheme = 1

    # Sum up margin taxable across all margin items
    total_margin_taxable = sum(
        flt(item.custom_taxable_value) for item in doc.items if item.get("custom_is_margin_item")
    )
    total_non_margin = sum(
        flt(item.amount) for item in doc.items if not item.get("custom_is_margin_item")
    )

    # Recalculate tax rows for margin items
    total_gst = 0
    for tax in doc.taxes:
        if tax.charge_type != "On Net Total":
            continue

        # Tax on margin items only
        margin_tax = (total_margin_taxable * flt(tax.rate)) / 100
        # Tax on non-margin items (normal)
        non_margin_tax = (total_non_margin * flt(tax.rate)) / 100

        combined = margin_tax + non_margin_tax
        tax.tax_amount = combined
        tax.tax_amount_after_discount_amount = combined
        tax.base_tax_amount = combined
        tax.base_tax_amount_after_discount_amount = combined

        total_gst += margin_tax

    # Calculate exempted value per margin item
    for item in doc.items:
        if not item.get("custom_is_margin_item"):
            continue

        qty = flt(item.qty)
        rate = flt(item.rate)
        item_amount = rate * qty
        margin_taxable = flt(item.custom_taxable_value)

        # Proportional GST for this item
        item_gst = 0
        if total_margin_taxable > 0:
            item_gst = (margin_taxable / total_margin_taxable) * total_gst

        exempted = item_amount - margin_taxable - item_gst
        if exempted < 0:
            frappe.throw(f"Exempted value cannot be negative for item {item.item_code}")
        item.custom_exempted_value = exempted


def _get_incoming_rate(item):
    """Get purchase cost (incoming rate) for a POS Invoice item."""
    # Try from serial no first
    serial = (item.serial_no or "").strip().split("\n")[0].strip()
    if serial:
        rate = frappe.db.get_value("CH Serial Lifecycle", {"serial_no": serial}, "purchase_rate")
        if rate:
            return flt(rate)

    # Fallback to item valuation rate
    return flt(item.get("incoming_rate") or item.get("valuation_rate") or 0)
