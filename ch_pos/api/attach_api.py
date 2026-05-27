# Copyright (c) 2025, GoStack and contributors
# Attach Prompt API — used by the POS attach panel
# Fetches attach rules and logs offers/accepts/skips to CH Attach Log

import frappe
from frappe import _
from frappe.utils import nowdate, now_datetime, flt


@frappe.whitelist()
def get_attach_offers(item_code, pos_profile=None) -> dict:
    """Return all applicable attach offers (Warranty, VAS, Accessory) for a sold item."""
    if not item_code:
        return {"warranty_plans": [], "attach_rules": []}

    # 1. Warranty plans (existing API)
    item = frappe.get_cached_doc("Item", item_code)
    warranty_plans = _get_warranty_plans(
        item_code=item_code,
        item_group=item.item_group,
        brand=item.brand,
    )

    # 2. Attach rules (VAS + Accessories) via CH Attach Rule
    from ch_pos.pos_core.doctype.ch_attach_rule.ch_attach_rule import (
        get_attach_rules_for_item,
    )
    rules = get_attach_rules_for_item(item_code)

    # 3. Phase-3 VAS Attach Rule layer (brand/category/price-band aware)
    # Reuses existing POS payload shape so front-end rendering remains unchanged.
    vas_rules = []
    try:
        item_price = frappe.db.get_value(
            "CH Item Price",
            {"item_code": item_code, "channel": "POS", "status": "Active"},
            "selling_price",
        )
        if frappe.db.exists("DocType", "VAS Attach Rule"):
            from ch_item_master.ch_item_master.vas.api import get_vas_attach_offers
            vas_rules = get_vas_attach_offers(
                item_code=item_code,
                selling_price=flt(item_price or 0),
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "VAS attach offers failed")

    return {
        "warranty_plans": warranty_plans,
        "attach_rules": rules + (vas_rules or []),
    }


def _get_warranty_plans(item_code, item_group=None, brand=None):
    """Retrieve warranty plans applicable to this item."""
    if not frappe.db.table_exists("tabCH Warranty Plan"):
        return []

    filters = {"is_active": 1}
    plans = frappe.get_all("CH Warranty Plan",
        filters=filters,
        fields=["name", "plan_name", "plan_type", "duration_months", "price",
                "service_item", "applicable_item_group", "applicable_brand"],
        order_by="price asc",
    )

    matched = []
    for p in plans:
        # Match by item_group or brand (or catch-all with no filter)
        ig = p.applicable_item_group
        br = p.applicable_brand
        if ig and ig != item_group:
            continue
        if br and br != brand:
            continue
        matched.append(p)

    return matched


@frappe.whitelist()
def log_attach_event(pos_invoice=None, pos_profile=None, item_code=None,
                     attach_type=None, action=None, skip_reason=None,
                     plan_code=None) -> dict:
    """Log an attach offer event (Offered/Accepted/Skipped) to CH Attach Log."""
    if not attach_type or not action:
        frappe.throw(_("attach_type and action are required"), title=_("API Error"))

    log = frappe.new_doc("CH Attach Log")
    log.pos_invoice = pos_invoice or ""
    log.pos_profile = pos_profile or ""
    log.item_code = item_code or ""
    log.attach_type = attach_type
    log.action = action
    log.skip_reason = (str(skip_reason)[:200]) if skip_reason else ""
    log.plan_code = plan_code or ""
    log.offered_by = frappe.session.user
    log.offered_at = now_datetime()
    log.flags.ignore_permissions = True
    log.save()

    return log.name
