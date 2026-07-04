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
    """Create a POS Sales Invoice with idempotency guard on client_id.

    When the POS queues an invoice offline, it attaches a client_id (UUID-like
    string) to the payload.  If the submit succeeded but the ack was lost (e.g.
    network dropped after server wrote), the client retries and hits this check
    rather than creating a duplicate.

    Falls through to the standard create_pos_invoice for the actual creation.
    """
    client_id = str(kwargs.get("client_id") or "").strip()
    client_request_id = str(kwargs.get("client_request_id") or client_id or "").strip()

    existing = _find_existing_offline_sales_invoice(client_id, client_request_id)
    if existing:
        doc = frappe.get_doc("Sales Invoice", existing)
        return {
            "name": doc.name,
            "grand_total": flt(doc.grand_total),
            "status": "already_exists",
        }

    # Delegate via frappe.call so get_newargs strips Frappe-internal keys
    # (cmd, csrf_token, ignore_permissions, etc.) that arrive in **kwargs
    # because this function uses VAR_KEYWORD and receives the raw form_dict.
    from ch_pos.api.pos_api import create_pos_invoice  # noqa

    _FRAPPE_INTERNAL = {"cmd", "csrf_token", "client_id", "data", "ignore_permissions", "flags"}
    clean_kwargs = {k: v for k, v in kwargs.items() if k not in _FRAPPE_INTERNAL}
    if client_request_id and not clean_kwargs.get("client_request_id"):
        clean_kwargs["client_request_id"] = client_request_id
    result = frappe.call(create_pos_invoice, **clean_kwargs)

    # Stamp the client_id on the created invoice so future retries hit the guard
    if result and result.get("name") and (client_id or client_request_id):
        try:
            updates = {}
            if client_id and frappe.db.has_column("Sales Invoice", "ch_offline_client_id"):
                updates["ch_offline_client_id"] = client_id[:140]
            if client_request_id and frappe.db.has_column("Sales Invoice", "custom_client_request_id"):
                updates["custom_client_request_id"] = client_request_id[:140]
            if updates:
                frappe.db.set_value("Sales Invoice", result["name"], updates, update_modified=False)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Offline POS idempotency stamp failed: {result['name']}")

    return result


def _find_existing_offline_sales_invoice(client_id: str | None, client_request_id: str | None) -> str | None:
    """Return an existing Sales Invoice for a durable offline retry token."""
    clauses = []
    params = {}

    if client_id and frappe.db.has_column("Sales Invoice", "ch_offline_client_id"):
        clauses.append("ch_offline_client_id = %(client_id)s")
        params["client_id"] = client_id[:140]
    if client_request_id and frappe.db.has_column("Sales Invoice", "custom_client_request_id"):
        clauses.append("custom_client_request_id = %(client_request_id)s")
        params["client_request_id"] = client_request_id[:140]

    if not clauses:
        return None

    rows = frappe.db.sql(
        f"""
        SELECT name
        FROM `tabSales Invoice`
        WHERE docstatus != 2
          AND ({' OR '.join(clauses)})
        ORDER BY creation DESC
        LIMIT 1
        """,
        params,
        as_dict=True,
    )
    return rows[0].name if rows else None


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
