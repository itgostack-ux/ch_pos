# Copyright (c) 2026, GoStack and contributors
"""POS → Store Stock Report.

Surfaces on-hand stock for the POS profile's warehouse, including the
market-standard "last verified" (date last cycle-counted) and which items are
due for a count. Backed by the shared core in ch_erp15 CH Cycle Count.
"""

import frappe
from frappe import _
from frappe.utils import flt


def _resolve_warehouse(pos_profile):
    warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
    if not warehouse:
        frappe.throw(_("POS Profile {0} has no warehouse configured.").format(pos_profile))
    return warehouse


@frappe.whitelist()
def get_store_stock_report(pos_profile, only_due=0, class_filter=None) -> dict:
    """Return the store stock + cycle-count report for a POS profile's warehouse."""
    if not pos_profile:
        frappe.throw(_("POS Profile is required."))
    warehouse = _resolve_warehouse(pos_profile)

    from ch_erp15.ch_erp15.doctype.ch_cycle_count.ch_cycle_count import get_store_stock
    rows = get_store_stock(warehouse, only_due=only_due, class_filter=class_filter)

    due_count = sum(1 for r in rows if r.get("due"))
    total_value = sum(flt(r.get("stock_value")) for r in rows)
    return {
        "warehouse": warehouse,
        "rows": rows,
        "summary": {
            "items": len(rows),
            "due_for_count": due_count,
            "total_stock_value": total_value,
        },
    }


@frappe.whitelist()
def start_store_cycle_count(pos_profile, class_filter=None, only_due=1) -> dict:
    """Create a Draft CH Cycle Count for the store, pre-loaded with on-hand items.

    Lets a store kick off a count straight from POS; the count is then completed
    and submitted (counters enter quantities / scan IMEIs) from the desk form.
    """
    if not pos_profile:
        frappe.throw(_("POS Profile is required."))
    warehouse = _resolve_warehouse(pos_profile)
    company = frappe.db.get_value("POS Profile", pos_profile, "company")
    frappe.has_permission("CH Cycle Count", "create", throw=True)

    from ch_erp15.ch_erp15.doctype.ch_cycle_count.ch_cycle_count import get_count_lines

    cc = frappe.new_doc("CH Cycle Count")
    cc.warehouse = warehouse
    cc.company = company
    cc.count_date = frappe.utils.today()
    cc.counted_by = frappe.session.user
    cc.count_class_filter = class_filter or None
    cc.status = "Counting"
    for line in get_count_lines(warehouse, class_filter=class_filter, only_due=only_due):
        cc.append("items", line)
    cc.insert(ignore_permissions=True)
    return {"cycle_count": cc.name, "items": len(cc.items), "warehouse": warehouse}
