# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt
"""
Offline Sync API — Backend endpoints for the CH POS offline resilience layer.

  create_pos_invoice_offline  — idempotent invoice creation (dedupes by client_id)
  get_full_item_catalog       — paginated full catalog for pre-warming IndexedDB
  get_customer_catalog        — recent customers for offline typeahead cache
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate


# ── Idempotent Invoice Creation ───────────────────────────────────────────────

@frappe.whitelist()
def create_pos_invoice_offline(**kwargs):
    """Create a POS Invoice with idempotency guard on client_id.

    When the POS queues an invoice offline, it attaches a client_id (UUID-like
    string) to the payload.  If the submit succeeded but the ack was lost (e.g.
    network dropped after server wrote), the client retries and hits this check
    rather than creating a duplicate.

    Falls through to the standard create_pos_invoice for the actual creation.
    """
    client_id = kwargs.get("client_id")

    if client_id:
        existing = frappe.db.get_value(
            "POS Invoice",
            {"ch_offline_client_id": client_id, "docstatus": ("!=", 2)},
            "name",
        )
        if existing:
            doc = frappe.get_doc("POS Invoice", existing)
            return {
                "name": doc.name,
                "grand_total": flt(doc.grand_total),
                "status": "already_exists",
            }

    # Delegate to the main POS API (which handles all validation, stock, GL etc.)
    from ch_pos.api.pos_api import create_pos_invoice  # noqa

    result = create_pos_invoice(**{k: v for k, v in kwargs.items() if k != "client_id"})

    # Stamp the client_id on the created invoice so future retries hit the guard
    if result and client_id:
        try:
            frappe.db.set_value("POS Invoice", result["name"], "ch_offline_client_id", client_id)
        except Exception:
            pass  # non-critical — idempotency is best-effort

    return result


# ── Full Item Catalog ─────────────────────────────────────────────────────────

@frappe.whitelist()
def get_full_item_catalog(pos_profile, company=None, page=0, page_size=200):
    """Return a paginated slice of ALL items available at this POS profile.

    Used by SyncService.preload_catalog() to warm the offline IndexedDB store.
    Only returns fields needed for offline item search + cart building.
    """
    page      = cint(page)
    page_size = cint(page_size) or 200
    offset    = page * page_size

    if not pos_profile:
        frappe.throw(_("pos_profile is required"))

    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    warehouse = profile.warehouse

    # Item groups allowed by the profile (empty = all)
    allowed_groups = [r.item_group for r in (profile.item_groups or [])]
    group_clause   = ""
    group_params   = []
    if allowed_groups:
        placeholders = ", ".join(["%s"] * len(allowed_groups))
        group_clause = f"AND i.item_group IN ({placeholders})"
        group_params = allowed_groups

    rows = frappe.db.sql(
        f"""
        SELECT
            i.item_code,
            i.item_name,
            i.item_group,
            i.brand,
            i.description,
            i.stock_uom,
            i.has_serial_no,
            i.has_variants,
            i.image AS thumbnail,
            IFNULL(b.actual_qty, 0) AS actual_qty,
            ip.price_list_rate        AS standard_rate
        FROM `tabItem` i
        LEFT JOIN `tabBin` b
            ON b.item_code = i.item_code
            AND b.warehouse = %s
        LEFT JOIN `tabItem Price` ip
            ON ip.item_code = i.item_code
            AND ip.price_list = %s
            AND ip.selling = 1
        WHERE i.disabled = 0
            AND i.is_sales_item = 1
            AND i.has_variants = 0
            AND IFNULL(i.ch_lifecycle_status, '') IN ('Active', 'Obsolete')
            {group_clause}
        GROUP BY i.item_code
        ORDER BY i.item_name
        LIMIT %s OFFSET %s
        """,
        [warehouse, profile.selling_price_list] + group_params + [page_size + 1, offset],
        as_dict=True,
    )

    has_more = len(rows) > page_size
    items    = rows[:page_size]

    return {"items": items, "has_more": has_more, "page": page}


# ── Customer Catalog ──────────────────────────────────────────────────────────

@frappe.whitelist()
def get_customer_catalog(limit=500):
    """Return recent customers for offline typeahead cache.

    Ordered by last transaction date descending — the most recently-served
    customers are most likely to walk in again.
    """
    limit = cint(limit) or 500

    rows = frappe.db.sql(
        """
        SELECT
            c.name,
            c.customer_name,
            c.customer_group,
            c.mobile_no,
            c.email_id,
            c.customer_type,
            MAX(si.posting_date) AS last_visit
        FROM `tabCustomer` c
        LEFT JOIN `tabSales Invoice` si
            ON si.customer = c.name
            AND si.docstatus = 1
        WHERE c.disabled = 0
        GROUP BY c.name
        ORDER BY last_visit DESC, c.customer_name
        LIMIT %s
        """,
        [limit],
        as_dict=True,
    )

    return {"customers": rows, "count": len(rows)}
