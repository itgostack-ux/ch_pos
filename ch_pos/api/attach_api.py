# Copyright (c) 2025, GoStack and contributors
# Attach Prompt API — used by the POS attach panel
# Fetches attach rules and logs offers/accepts/skips to CH Attach Log

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate

from ch_pos.api.scope_guard import assert_pos_profile_scope, assert_sales_invoice_scope
from ch_pos.config import get_control_setting


@frappe.whitelist()
def get_attach_offers(item_code, pos_profile=None) -> dict:
    """Return all applicable attach offers (Warranty, VAS, Accessory) for a sold item."""
    frappe.has_permission("Sales Invoice", "create", throw=True)
    assert_pos_profile_scope(pos_profile)
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

    item = frappe.db.get_value("Item", item_code, ["item_group", "brand", "ch_category", "ch_sub_category"], as_dict=True) or {}
    item_group = item_group or item.get("item_group")
    brand = brand or item.get("brand")
    item_category = item.get("ch_category")
    item_sub_category = item.get("ch_sub_category")

    today = nowdate()
    plan_limit = max(1, min(cint(get_control_setting("warranty_plan_result_limit", 200)), 1000))
    conditions = [
        "wp.status = 'Active'",
        "(wp.valid_from IS NULL OR wp.valid_from <= %(today)s)",
        "(wp.valid_to IS NULL OR wp.valid_to >= %(today)s)",
    ]
    params = {"today": today, "plan_limit": plan_limit}
    if brand:
        conditions.append("(IFNULL(wp.brand, '') = '' OR wp.brand = %(brand)s)")
        params["brand"] = brand
    if item_group and frappe.db.table_exists("CH Warranty Plan Item Group"):
        conditions.append(
            "(NOT EXISTS ("
            "SELECT 1 FROM `tabCH Warranty Plan Item Group` all_groups "
            "WHERE all_groups.parent = wp.name AND all_groups.parenttype = 'CH Warranty Plan'"
            ") OR EXISTS ("
            "SELECT 1 FROM `tabCH Warranty Plan Item Group` matching_group "
            "WHERE matching_group.parent = wp.name "
            "AND matching_group.parenttype = 'CH Warranty Plan' "
            "AND matching_group.item_group = %(item_group)s"
            "))"
        )
        params["item_group"] = item_group
    if item_category and frappe.db.table_exists("CH Warranty Plan Category"):
        conditions.append(
            "(NOT EXISTS ("
            "SELECT 1 FROM `tabCH Warranty Plan Category` all_categories "
            "WHERE all_categories.parent = wp.name AND all_categories.parenttype = 'CH Warranty Plan'"
            ") OR EXISTS ("
            "SELECT 1 FROM `tabCH Warranty Plan Category` matching_category "
            "WHERE matching_category.parent = wp.name "
            "AND matching_category.parenttype = 'CH Warranty Plan' "
            "AND matching_category.category = %(item_category)s"
            "))"
        )
        params["item_category"] = item_category
    if item_sub_category and frappe.db.table_exists("CH Warranty Plan Sub Category"):
        conditions.append(
            "(NOT EXISTS ("
            "SELECT 1 FROM `tabCH Warranty Plan Sub Category` all_sub_categories "
            "WHERE all_sub_categories.parent = wp.name AND all_sub_categories.parenttype = 'CH Warranty Plan'"
            ") OR EXISTS ("
            "SELECT 1 FROM `tabCH Warranty Plan Sub Category` matching_sub_category "
            "WHERE matching_sub_category.parent = wp.name "
            "AND matching_sub_category.parenttype = 'CH Warranty Plan' "
            "AND matching_sub_category.sub_category = %(item_sub_category)s"
            "))"
        )
        params["item_sub_category"] = item_sub_category
    plans = frappe.db.sql(
        f"""
        SELECT wp.name, wp.plan_name, wp.plan_type, wp.duration_months, wp.price,
               wp.pricing_mode, wp.percentage_value, wp.service_item, wp.brand,
               wp.valid_from, wp.valid_to
          FROM `tabCH Warranty Plan` wp
         WHERE {' AND '.join(conditions)}
         ORDER BY wp.price ASC, wp.name ASC
         LIMIT %(plan_limit)s
        """,
        params,
        as_dict=True,
    )

    # Only surface plans whose service_item is a Live (Active-lifecycle) Item —
    # an Active plan pointing at a Draft/Blocked service Item is unsellable and
    # would fail at Sales Invoice ("Activate the item first").
    from ch_item_master.ch_item_master.governance import filter_sellable_items
    _live = filter_sellable_items([p.service_item for p in plans])
    plans = [p for p in plans if not p.service_item or p.service_item in _live]

    device_price = 0
    if any(p.pricing_mode == "Percentage of Device Price" for p in plans):
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
        # Resolve percentage pricing to an actual rate. Without this the
        # attach panel adds the plan to the cart at Rs.0 because the plan's
        # ``price`` column stays 0 whenever ``pricing_mode`` is percentage.
        if p.pricing_mode == "Percentage of Device Price":
            p.price = flt(device_price * flt(p.percentage_value) / 100.0, 2)
        matched.append(p)

    return matched[:plan_limit]


@frappe.whitelist(methods=["POST"])
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
    frappe.has_permission("Sales Invoice", "create", throw=True)
    assert_pos_profile_scope(pos_profile)
    if not attach_type or not action:
        frappe.throw(_("attach_type and action are required"), title=_("API Error"))
    if action not in {"Offered", "Accepted", "Skipped"}:
        frappe.throw(_("Invalid attach action."), frappe.ValidationError)
    if pos_invoice:
        invoice = assert_sales_invoice_scope(pos_invoice)
        if invoice.pos_profile != pos_profile:
            frappe.throw(_("Invoice belongs to another POS Profile."), frappe.PermissionError)

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
    log.insert()

    return log.name
