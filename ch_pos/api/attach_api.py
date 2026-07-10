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

    # 2. Attach rules (VAS + Accessories) via CH Attach Rule.
    #
    # Historical note: a mirror `VAS Attach Rule` doctype existed in
    # ch_item_master and its offers were merged in here. That doctype
    # has been folded back into CH Attach Rule (see
    # ch_item_master.patches.v31_merge_vas_plan_into_ch_warranty_plan) so
    # there is only one attach-rule surface now.
    from ch_pos.pos_core.doctype.ch_attach_rule.ch_attach_rule import (
        get_attach_rules_for_item,
    )
    rules = get_attach_rules_for_item(item_code)

    return {
        "warranty_plans": warranty_plans,
        "attach_rules": rules,
    }


def _get_warranty_plans(item_code, item_group=None, brand=None):
    """Retrieve warranty plans applicable to this item.

    NOTE: `CH Warranty Plan` uses a `status` field (Draft/Active/Retired), NOT
    `is_active`; brand is a top-level Link (`brand`), and item-group
    applicability lives in the `applicable_item_groups` child table — the
    legacy `applicable_item_group` / `applicable_brand` single-value fields
    no longer exist. The previous filter shape referenced fields that
    haven't been on the plan for two schema revisions, which effectively
    hid every plan from the attach panel.

    Pricing modes:
      * ``Fixed`` — return the plan's ``price`` (Standard Price) as-is.
      * ``Percentage of Device Price`` — plan.price on the doc is 0; we
        compute the effective price as ``device_price * percentage_value /
        100`` using the active POS CH Item Price for ``item_code``. This
        mirrors the computation in ``pos_api.get_warranty_plans`` so the
        cashier-facing attach panel and the standalone plan resolver
        agree on the rendered rate.
    """
    # NOTE: `frappe.db.table_exists` accepts the DocType name and prepends
    # `tab` internally — passing `tabCH Warranty Plan` here looks up
    # `tabtabCH Warranty Plan` and always returns False, which silently
    # emptied the attach panel's warranty section on every request.
    if not frappe.db.table_exists("CH Warranty Plan"):
        return []

    today = nowdate()

    # NOTE on brand filtering: `brand` on CH Warranty Plan is a nullable Link,
    # and a NULL brand means "applies to every brand" (catch-all). SQL `IN`
    # does not match NULLs, so we can't push the OR-with-NULL down as a
    # ``["in", [brand, "", None]]`` filter — that quietly drops every
    # catch-all plan. Fetch the whole Active set and filter brand in Python.
    plans = frappe.get_all(
        "CH Warranty Plan",
        filters={"status": "Active"},
        fields=[
            "name", "plan_name", "plan_type", "duration_months", "price",
            "pricing_mode", "percentage_value",
            "service_item", "brand", "valid_from", "valid_to",
        ],
        order_by="price asc",
    )

    # Pre-load item-group applicability rows for the fetched plan set in a
    # single query so we do not re-hit the DB per plan.
    plan_names = [p.name for p in plans]
    ig_map: dict[str, set[str]] = {}
    if plan_names and frappe.db.table_exists("CH Warranty Plan Item Group"):
        for row in frappe.get_all(
            "CH Warranty Plan Item Group",
            filters={"parent": ["in", plan_names], "parenttype": "CH Warranty Plan"},
            fields=["parent", "item_group"],
            limit_page_length=0,
        ):
            if row.item_group:
                ig_map.setdefault(row.parent, set()).add(row.item_group)

    # Look up the device selling price once — needed only if we hit a
    # percentage-priced plan, but a single get_value is cheaper than the
    # branch overhead per plan.
    device_price = flt(frappe.db.get_value(
        "CH Item Price",
        {"item_code": item_code, "channel": "POS", "status": "Active"},
        "selling_price",
    ))

    matched = []
    for p in plans:
        # Brand match: catch-all when the plan has no brand set.
        if p.brand and brand and p.brand != brand:
            continue
        # Validity window (either bound optional).
        if p.valid_from and str(p.valid_from) > today:
            continue
        if p.valid_to and str(p.valid_to) < today:
            continue
        # Item-group applicability: catch-all when the plan has no rows.
        applicable_groups = ig_map.get(p.name)
        if applicable_groups and item_group and item_group not in applicable_groups:
            continue
        # Resolve percentage pricing to an actual rate. Without this the
        # attach panel adds the plan to the cart at Rs.0 because the plan's
        # ``price`` column stays 0 whenever ``pricing_mode`` is percentage.
        if p.pricing_mode == "Percentage of Device Price":
            p.price = flt(device_price * flt(p.percentage_value) / 100.0, 2)
        matched.append(p)

    return matched


@frappe.whitelist()
def log_attach_event(pos_invoice=None, pos_profile=None, item_code=None,
                     attach_type=None, action=None, skip_reason=None,
                     plan_code=None, serial_no=None) -> dict:
    """Log an attach offer event (Offered/Accepted/Skipped) to CH Attach Log.

    Timing model (SAP CRM upsell events / Oracle Retail POS attach
    telemetry parity):
      * "Offered"  — logged when the attach panel opens for a device.
                     No POS Invoice exists yet. ``pos_invoice`` stays
                     blank; it is back-filled by ``create_pos_invoice``
                     when the sale is booked.
      * "Accepted" — logged when the cashier clicks Add on a suggestion.
      * "Skipped"  — logged when the cashier dismisses a suggestion;
                     ``skip_reason`` becomes mandatory via
                     ``mandatory_depends_on`` when the rule has
                     ``skip_reason_required=1``.

    ``serial_no`` captures the covered device's IMEI (in-store serial
    for cart devices, customer-provided IMEI for external-device VAS)
    so the log answers "what plan attached to what IMEI".

    Empty strings on Link fields are coerced to ``None`` — passing
    ``""`` on a Link would trip Frappe's mandatory check as if the
    field were unset.
    """
    if not attach_type or not action:
        frappe.throw(_("attach_type and action are required"), title=_("API Error"))

    def _link(v):
        v = (v or "").strip() if isinstance(v, str) else v
        return v or None

    log = frappe.new_doc("CH Attach Log")
    log.pos_invoice = _link(pos_invoice)
    log.pos_profile = _link(pos_profile)
    log.item_code = _link(item_code)
    log.attach_type = attach_type
    log.action = action
    log.skip_reason = (str(skip_reason)[:200]) if skip_reason else ""
    log.plan_code = _link(plan_code)
    log.serial_no = (str(serial_no).strip()[:140]) if serial_no else ""
    log.offered_by = frappe.session.user
    log.offered_at = now_datetime()
    log.flags.ignore_permissions = True
    log.save()

    return log.name
