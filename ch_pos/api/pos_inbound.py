"""
CH POS — Inbound Receive APIs
================================

Whitelisted server surface that lets a store operator run the same
Purchase Receipt "Generate + Submit" pipeline used by the stock team on the
Purchase Receipt desk form — but scoped to the store's warehouse and
gated by role.

Design rules (per ERPNext v16 / ch_erp15 conventions)
-----------------------------------------------------

1. Reuse-first — every mutation delegates into the existing pipeline:
     * ``ch_erp15.ch_erp15.custom.purchase_receipt.generate_barcode_serials``
       for barcode-type auto-generation (single source of truth for the
       ``<COMPANY_ABBR><YYYYMMDD><6-digit>`` series).
     * ``CustomPurchaseReceipt.validate`` runs on every ``pr.save()``:
       enforces per-row ``custom_serial_generated``, serial count == qty,
       cross-row uniqueness, no-collision with existing Serial No master.
     * ``pr.submit()`` triggers the standard GRN, India Compliance GST
       validators, and the 3-way match (Import-only exemption still
       preserved).
     * ``ch_erp15.ch_erp15.custom.purchase_order.make_purchase_receipt``
       for materialising a Draft PR from a Purchase Order.

2. Store scoping — a POS session is bound to a single POS Profile whose
   ``warehouse`` is the store bin. Any PR / PO surfaced through this API
   must have ``set_warehouse == profile.warehouse`` **or** carry at least
   one item row with ``warehouse == profile.warehouse``. There is no
   ``custom_target_store`` field on PR today — warehouse identity is the
   authoritative store scope.

3. Role gate — the stock-team desk pages require ``Stock Manager`` or
   ``System Manager``. We accept the same set plus the store-side custom
   role ``Store Manager`` so a store can run the flow without desk access.
   The gate is applied inside every mutating call; read-only listings
   only require an active POS session.

4. No new custom fields — POS surfaces existing PR data as-is.

Public API
----------

Read:
    * :func:`list_open_purchase_receipts` — Draft PRs targeting this store.
    * :func:`list_pending_purchase_orders` — Submitted POs (not fully
      received) whose ``set_warehouse`` is this store.
    * :func:`get_pr_detail` — full PR payload + per-row Generate status.

Mutate (gated):
    * :func:`create_pr_from_po` — materialise a Draft PR from a PO.
    * :func:`pos_pr_set_imei_serials` — save an IMEI list for a row.
    * :func:`pos_pr_generate_barcode_serials` — auto-fill Barcode row.
    * :func:`pos_pr_submit` — final validate + submit.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt


# ── Role gate ───────────────────────────────────────────────────────────
# Same set as the PR desk form + POS's own store-side role. System Manager
# always allowed as the escape hatch used elsewhere in ch_pos.
_INBOUND_ALLOWED_ROLES = frozenset({
    "System Manager",
    "Stock Manager",
    "Stock User",
    "Store Manager",
    "POS Manager",
})


def _require_inbound_role() -> None:
    """Raise PermissionError if the caller cannot run Inbound Receive.

    Applied at the top of every mutating whitelisted call. Read-only calls
    intentionally skip this so the store can browse pending GRNs without
    needing GRN-issuing rights.
    """
    roles = set(frappe.get_roles(frappe.session.user))
    if not (roles & _INBOUND_ALLOWED_ROLES):
        frappe.throw(
            _(
                "You do not have permission to run Inbound Receive. "
                "Required roles: {0}."
            ).format(", ".join(sorted(_INBOUND_ALLOWED_ROLES))),
            frappe.PermissionError,
        )


def _resolve_store_warehouse(pos_profile: str) -> str:
    """Return the warehouse bound to this POS Profile (mandatory)."""
    if not pos_profile:
        frappe.throw(_("POS Profile is required."))
    warehouse = frappe.db.get_value("POS Profile", pos_profile, "warehouse")
    if not warehouse:
        frappe.throw(
            _("POS Profile <b>{0}</b> is not linked to a warehouse.").format(pos_profile)
        )
    return warehouse


def _resolve_store_company(pos_profile: str) -> str:
    """Return the company bound to this POS Profile."""
    company = frappe.db.get_value("POS Profile", pos_profile, "company")
    if not company:
        frappe.throw(
            _("POS Profile <b>{0}</b> is not linked to a company.").format(pos_profile)
        )
    return company


def _row_generate_state(item) -> dict:
    """Compute per-row Generate progress for the client card.

    ``custom_serial_generated`` is the flag CustomPurchaseReceipt.validate
    checks — set to 1 when the row has scanned/generated enough serials to
    cover ``qty``. We surface both the flag and the raw counts so the UI
    can render a "3 / 10 scanned" progress chip even mid-flow.
    """
    kind = (item.get("custom_type") or "").strip()
    serials = [
        s.strip() for s in (item.get("serial_no") or "").split("\n") if s.strip()
    ]
    qty_int = int(flt(item.get("qty") or 0))
    needs_generate = kind in ("IMEI", "Barcode")
    return {
        "custom_type": kind or None,
        "needs_generate": needs_generate,
        "serials": serials,
        "scanned_count": len(serials),
        "remaining": max(qty_int - len(serials), 0) if needs_generate else 0,
        "custom_serial_generated": cint(item.get("custom_serial_generated")),
        "complete": (not needs_generate) or (
            cint(item.get("custom_serial_generated")) == 1
            and len(serials) == qty_int
        ),
    }


# ── Read APIs ───────────────────────────────────────────────────────────

@frappe.whitelist()
def list_open_purchase_receipts(pos_profile: str, limit: int = 25) -> list[dict]:
    """Draft Purchase Receipts targeting this store's warehouse.

    Scope = any PR where ``set_warehouse`` == store warehouse or any item
    row's ``warehouse`` == store warehouse. Only ``docstatus = 0`` is
    returned; submitted PRs are historical and not editable here.
    """
    warehouse = _resolve_store_warehouse(pos_profile)
    limit = max(1, min(int(cint(limit)) or 25, 100))

    rows = frappe.db.sql(
        """
        SELECT pr.name,
               pr.posting_date,
               pr.supplier,
               pr.supplier_name,
               pr.set_warehouse,
               pr.custom_purchase_type,
               pr.total_qty,
               pr.grand_total,
               pr.currency,
               (SELECT COUNT(*) FROM `tabPurchase Receipt Item` pri
                    WHERE pri.parent = pr.name) AS item_count,
               (SELECT COUNT(*) FROM `tabPurchase Receipt Item` pri
                    WHERE pri.parent = pr.name
                      AND pri.custom_type IN ('IMEI','Barcode')
                      AND IFNULL(pri.custom_serial_generated, 0) = 0) AS pending_generate_rows
        FROM `tabPurchase Receipt` pr
        WHERE pr.docstatus = 0
          AND (
            pr.set_warehouse = %(wh)s
            OR EXISTS (
                SELECT 1 FROM `tabPurchase Receipt Item` pri
                 WHERE pri.parent = pr.name AND pri.warehouse = %(wh)s
            )
          )
        ORDER BY pr.modified DESC
        LIMIT %(limit)s
        """,
        {"wh": warehouse, "limit": limit},
        as_dict=True,
    )
    return rows or []


@frappe.whitelist()
def list_pending_purchase_orders(pos_profile: str, limit: int = 25) -> list[dict]:
    """Submitted Purchase Orders whose destination is this store and which
    still have qty left to receive.

    Uses the standard ``per_received < 100`` gate that ERPNext maintains on
    Purchase Order via ``update_qty()``. Drop-ship POs (``custom_is_drop_ship``
    = 1) with warehouse == this store are the primary use case.
    """
    warehouse = _resolve_store_warehouse(pos_profile)
    limit = max(1, min(int(cint(limit)) or 25, 100))

    rows = frappe.db.sql(
        """
        SELECT po.name,
               po.transaction_date,
               po.schedule_date,
               po.supplier,
               po.supplier_name,
               po.set_warehouse,
               po.custom_purchase_type,
               IFNULL(po.custom_is_drop_ship, 0) AS custom_is_drop_ship,
               po.per_received,
               po.status,
               po.total_qty,
               po.grand_total,
               po.currency,
               (SELECT COUNT(*) FROM `tabPurchase Order Item` poi
                    WHERE poi.parent = po.name) AS item_count
        FROM `tabPurchase Order` po
        WHERE po.docstatus = 1
          AND IFNULL(po.status, '') NOT IN ('Closed', 'Delivered', 'Completed')
          AND IFNULL(po.per_received, 0) < 100
          AND (
            po.set_warehouse = %(wh)s
            OR EXISTS (
                SELECT 1 FROM `tabPurchase Order Item` poi
                 WHERE poi.parent = po.name AND poi.warehouse = %(wh)s
            )
          )
        ORDER BY po.transaction_date DESC, po.modified DESC
        LIMIT %(limit)s
        """,
        {"wh": warehouse, "limit": limit},
        as_dict=True,
    )
    return rows or []


@frappe.whitelist()
def get_pr_detail(pr_name: str, pos_profile: str) -> dict:
    """Return a Draft PR with per-row Generate state for the workspace.

    Store-scoped: the PR must overlap this store's warehouse or the call
    raises. Read-only — no role gate.
    """
    if not pr_name:
        frappe.throw(_("Purchase Receipt name is required."))
    warehouse = _resolve_store_warehouse(pos_profile)

    pr = frappe.get_doc("Purchase Receipt", pr_name)

    if pr.docstatus != 0:
        frappe.throw(
            _("Purchase Receipt {0} is not a Draft (status: {1}).").format(
                pr_name, pr.docstatus
            )
        )

    row_warehouses = {(row.get("warehouse") or "") for row in pr.items}
    if pr.set_warehouse != warehouse and warehouse not in row_warehouses:
        frappe.throw(
            _(
                "Purchase Receipt {0} does not target this store's warehouse "
                "({1})."
            ).format(pr_name, warehouse),
            frappe.PermissionError,
        )

    items = []
    for row in pr.items:
        state = _row_generate_state(row)
        items.append({
            "name": row.name,
            "idx": row.idx,
            "item_code": row.item_code,
            "item_name": row.item_name,
            "warehouse": row.warehouse,
            "uom": row.uom,
            "qty": flt(row.qty),
            "stock_qty": flt(row.stock_qty),
            "rate": flt(row.rate),
            "amount": flt(row.amount),
            **state,
        })

    return {
        "name": pr.name,
        "docstatus": pr.docstatus,
        "posting_date": str(pr.posting_date) if pr.posting_date else None,
        "supplier": pr.supplier,
        "supplier_name": pr.supplier_name,
        "company": pr.company,
        "set_warehouse": pr.set_warehouse,
        "currency": pr.currency,
        "total_qty": flt(pr.total_qty),
        "grand_total": flt(pr.grand_total),
        "items": items,
        "all_rows_complete": all(item["complete"] for item in items),
    }


# ── Mutate APIs (role-gated) ────────────────────────────────────────────

@frappe.whitelist()
def create_pr_from_po(po_name: str, pos_profile: str) -> dict:
    """Materialise a Draft Purchase Receipt from a Submitted PO.

    Delegates to ``ch_erp15.ch_erp15.custom.purchase_order.make_purchase_receipt``
    (the CH override that preserves ``custom_purchase_type``,
    ``custom_unit_taxable_value``, tax templates, etc). The mapped PR is
    saved as Draft — the store then finishes Generate + Submit here.
    """
    _require_inbound_role()
    if not po_name:
        frappe.throw(_("Purchase Order name is required."))

    warehouse = _resolve_store_warehouse(pos_profile)

    po = frappe.get_doc("Purchase Order", po_name)
    if po.docstatus != 1:
        frappe.throw(_("Purchase Order {0} is not Submitted.").format(po_name))

    row_warehouses = {(row.get("warehouse") or "") for row in po.items}
    if po.set_warehouse != warehouse and warehouse not in row_warehouses:
        frappe.throw(
            _(
                "Purchase Order {0} does not target this store's warehouse "
                "({1})."
            ).format(po_name, warehouse),
            frappe.PermissionError,
        )

    from ch_erp15.ch_erp15.custom.purchase_order import (
        make_purchase_receipt as _make_pr,
    )

    pr = _make_pr(po_name)

    # Ensure the PR is anchored to the store warehouse — the vanilla mapper
    # copies whatever the PO had; we force alignment with the current store
    # so the reflected GRN posts to the correct bin.
    pr.set_warehouse = warehouse
    for row in pr.items or []:
        row.warehouse = warehouse

    pr.flags.ignore_permissions = True
    pr.insert()

    return {"pr_name": pr.name}


@frappe.whitelist()
def pos_pr_set_imei_serials(
    pr_name: str,
    row_name: str,
    serials: str | list,
    pos_profile: str,
) -> dict:
    """Write scanned IMEIs to a PR row and flag it as Generated.

    Client hands a list (or newline-joined string) of serial numbers.
    Mirrors what ``open_imei_dialog`` in purchase_receipt.js does on the
    desk form, then triggers ``pr.save()`` which runs the full
    CustomPurchaseReceipt.validate pipeline (duplicate detection, existing
    Serial No master collision check, cross-row uniqueness). If validate
    throws, no state is persisted.
    """
    _require_inbound_role()
    warehouse = _resolve_store_warehouse(pos_profile)

    if isinstance(serials, str):
        try:
            parsed = json.loads(serials)
        except (json.JSONDecodeError, ValueError):
            parsed = [s for s in serials.replace(",", "\n").split("\n")]
        serials = parsed if isinstance(parsed, list) else []

    cleaned = [
        s.strip() for s in (serials or []) if isinstance(s, str) and s.strip()
    ]

    pr = frappe.get_doc("Purchase Receipt", pr_name)
    if pr.docstatus != 0:
        frappe.throw(_("Purchase Receipt {0} is not a Draft.").format(pr_name))

    row_warehouses = {(row.get("warehouse") or "") for row in pr.items}
    if pr.set_warehouse != warehouse and warehouse not in row_warehouses:
        frappe.throw(
            _("Purchase Receipt does not target this store's warehouse."),
            frappe.PermissionError,
        )

    row = next((r for r in pr.items if r.name == row_name), None)
    if not row:
        frappe.throw(_("Row {0} not found on {1}.").format(row_name, pr_name))

    if (row.get("custom_type") or "") not in ("IMEI", "Barcode"):
        frappe.throw(
            _("Row {0} does not accept serials (type: {1}).").format(
                row.idx, row.get("custom_type") or _("None")
            )
        )

    row.serial_no = "\n".join(cleaned)
    row.custom_serial_generated = 1 if len(cleaned) == int(flt(row.qty)) else 0

    pr.flags.ignore_permissions = True
    pr.save()

    return {
        "pr_name": pr.name,
        "row_name": row.name,
        "state": _row_generate_state(row),
    }


@frappe.whitelist()
def pos_pr_generate_barcode_serials(
    pr_name: str,
    row_name: str,
    pos_profile: str,
) -> dict:
    """Auto-generate barcode serials for a Barcode-type row.

    Reuses the single-source-of-truth generator
    ``ch_erp15.ch_erp15.custom.purchase_receipt.generate_barcode_serials``
    (row-locked ``tabSeries`` via ``getseries``), then writes the numbers
    back into ``row.serial_no`` and saves. Never mints serials when the row
    is not of ``custom_type == 'Barcode'``.
    """
    _require_inbound_role()
    warehouse = _resolve_store_warehouse(pos_profile)

    pr = frappe.get_doc("Purchase Receipt", pr_name)
    if pr.docstatus != 0:
        frappe.throw(_("Purchase Receipt {0} is not a Draft.").format(pr_name))

    row_warehouses = {(row.get("warehouse") or "") for row in pr.items}
    if pr.set_warehouse != warehouse and warehouse not in row_warehouses:
        frappe.throw(
            _("Purchase Receipt does not target this store's warehouse."),
            frappe.PermissionError,
        )

    row = next((r for r in pr.items if r.name == row_name), None)
    if not row:
        frappe.throw(_("Row {0} not found on {1}.").format(row_name, pr_name))

    if (row.get("custom_type") or "") != "Barcode":
        frappe.throw(
            _("Row {0}: Auto-generate is only for Barcode-type items.").format(row.idx)
        )

    from ch_erp15.ch_erp15.custom.purchase_receipt import generate_barcode_serials

    minted = generate_barcode_serials(
        company=pr.company,
        posting_date=pr.posting_date,
        qty=int(flt(row.qty)),
    ) or {}
    serials = minted.get("serials") or []

    row.serial_no = "\n".join(serials)
    row.custom_serial_generated = 1 if serials else 0

    pr.flags.ignore_permissions = True
    pr.save()

    return {
        "pr_name": pr.name,
        "row_name": row.name,
        "prefix": minted.get("prefix"),
        "state": _row_generate_state(row),
    }


@frappe.whitelist()
def pos_pr_submit(pr_name: str, pos_profile: str) -> dict:
    """Submit a Draft PR after Generate is complete for all rows.

    ``CustomPurchaseReceipt.validate`` re-runs during submit and enforces
    every guard the desk form does. We do not skip any validator here —
    ``ignore_permissions`` is used only because the store operator may not
    hold ``write`` on Purchase Receipt via the standard role matrix, even
    though the ``_require_inbound_role`` gate above authorises them for
    this specific POS pipeline.
    """
    _require_inbound_role()
    warehouse = _resolve_store_warehouse(pos_profile)

    pr = frappe.get_doc("Purchase Receipt", pr_name)
    if pr.docstatus != 0:
        frappe.throw(_("Purchase Receipt {0} is not a Draft.").format(pr_name))

    row_warehouses = {(row.get("warehouse") or "") for row in pr.items}
    if pr.set_warehouse != warehouse and warehouse not in row_warehouses:
        frappe.throw(
            _("Purchase Receipt does not target this store's warehouse."),
            frappe.PermissionError,
        )

    incomplete = [
        row.idx
        for row in pr.items
        if (row.get("custom_type") in ("IMEI", "Barcode"))
        and cint(row.get("custom_serial_generated")) != 1
    ]
    if incomplete:
        frappe.throw(
            _("Rows {0}: Generate is not complete. Scan or auto-generate serials first.").format(
                ", ".join(str(i) for i in incomplete)
            )
        )

    pr.flags.ignore_permissions = True
    pr.submit()

    return {"pr_name": pr.name, "docstatus": pr.docstatus}
