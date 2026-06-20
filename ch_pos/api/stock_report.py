# Copyright (c) 2026, GoStack and contributors
"""POS → Store Stock Report.

Surfaces on-hand stock for the POS profile's warehouse, including the
market-standard "last verified" (date last cycle-counted) and which items are
due for a count. Backed by the shared core in ch_erp15 CH Cycle Count.
"""

import json

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

    return {
        "cycle_count": cc.name,
        "items": len(cc.items),
        "warehouse": warehouse,
        "blind_count": int(cc.blind_count or 0),
        "lines": [
            {
                "item_code": d.item_code,
                "item_name": d.item_name,
                "is_serialized": int(d.is_serialized or 0),
                "system_qty": flt(d.system_qty),
            }
            for d in cc.items
        ],
    }


@frappe.whitelist()
def submit_pos_count(cycle_count, counts) -> dict:
    """Apply counts entered in POS to a Draft/Counting CH Cycle Count and submit.

    `counts` = [{item_code, counted_qty, scanned_serials}]. Submitting runs the
    full variance → verify / matrix-approval flow in the controller.
    """
    if isinstance(counts, str):
        counts = json.loads(counts)
    cc = frappe.get_doc("CH Cycle Count", cycle_count)
    cc.check_permission("submit")
    if cc.docstatus != 0:
        frappe.throw(_("Cycle count {0} is already submitted.").format(cc.name))

    by_item = {c.get("item_code"): c for c in (counts or [])}
    for row in cc.items:
        c = by_item.get(row.item_code)
        if not c:
            continue
        if row.is_serialized:
            row.scanned_serials = c.get("scanned_serials") or ""
        else:
            row.counted_qty = flt(c.get("counted_qty"))
    cc.save(ignore_permissions=True)
    cc.submit()
    cc.reload()
    return {
        "name": cc.name,
        "status": cc.status,
        "total_variance_value": flt(cc.total_variance_value),
        "variance_exception": cc.variance_exception,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stock Audit history — backs the "Count History" and "Variance Requests"
# tabs in the POS Stock Audit workspace.
# ─────────────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def list_cycle_counts(pos_profile, limit=25) -> list:
    """Return recent CH Cycle Count rows for this store's warehouse.

    Used by the POS Stock Audit "Count History" tab so a store executive can
    see the last counts performed at their warehouse, by whom, with the final
    status (Verified / Variance / Pending Approval) and the resulting variance.
    """
    if not pos_profile:
        frappe.throw(_("POS Profile is required."))
    warehouse = _resolve_warehouse(pos_profile)
    try:
        limit = int(limit or 25)
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(limit, 200))

    rows = frappe.get_all(
        "CH Cycle Count",
        filters={"warehouse": warehouse, "docstatus": ("!=", 2)},
        fields=[
            "name", "count_date", "status", "counted_by",
            "total_variance_qty", "total_variance_value",
            "variance_exception", "stock_reconciliation",
            "count_class_filter", "blind_count",
            "creation", "modified",
        ],
        order_by="count_date desc, creation desc",
        limit_page_length=limit,
    )
    return {
        "warehouse": warehouse,
        "rows": rows,
    }


@frappe.whitelist()
def list_variance_requests(pos_profile, limit=25) -> dict:
    """Return Stock Count Variance CH Exception Request rows for this store.

    Mirrors the cashier `get_pending_exceptions` shape but for the cycle-count
    family: includes Approved / Rejected / Expired / Auto-Approved so the
    Stock Audit operator can audit the full variance approval history,
    not just the open ones.
    """
    if not pos_profile:
        frappe.throw(_("POS Profile is required."))
    warehouse = _resolve_warehouse(pos_profile)
    try:
        limit = int(limit or 25)
    except (TypeError, ValueError):
        limit = 25
    limit = max(1, min(limit, 200))

    rows = frappe.get_all(
        "CH Exception Request",
        filters={
            "exception_type": "Stock Count Variance",
            "store_warehouse": warehouse,
            "docstatus": ("!=", 2),
        },
        fields=[
            "name", "exception_type", "status",
            "requested_by", "requested_by_name", "requested_reason",
            "requested_value", "resolution_value",
            "reference_doctype", "reference_name",
            "raised_at", "resolved_at",
        ],
        order_by="raised_at desc",
        limit_page_length=limit,
    )
    return {
        "warehouse": warehouse,
        "rows": rows,
    }
