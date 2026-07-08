"""
Free-item return guard (POS returns)
====================================

When a POS return (``is_return=1``) is created against an original Sales
Invoice that included Pricing-Rule-driven free items (``is_free_item=1``),
the cashier can forget to bring those free items back onto the return
document. Left unchecked this leaves the free-item stock as sold in the
Serial No / Stock Ledger even though the customer walks out with the
gift returned.

Standard ERPNext / HRMS / India Compliance parity note
-------------------------------------------------------
Upstream ERPNext does NOT auto-inject the linked free rows on the return
side — it copies whatever rows the cashier types in. This guard mirrors
the pattern used by India Compliance's ``e_invoice`` module (a
non-blocking ``frappe.msgprint`` with a link back to the source doc)
rather than throwing, because a customer can legitimately keep the free
item and only return the phone (in which case cashier acknowledges the
prompt and proceeds).

Two touch-points
----------------
1. ``validate_free_item_returns`` — server-side ``Sales Invoice.validate``
   hook. Non-blocking: raises a warning-level ``msgprint`` (yellow)
   listing free items still missing from the return so the cashier
   can't ship without seeing it. Idempotent: only fires when
   ``is_return=1`` and ``return_against`` is set.

2. ``get_missing_free_items`` — whitelisted helper used by the client
   script in ``ch_pos/custom/pos_invoice.js`` to render a proper
   modal with a one-click "Add to Return" primary_action.

Both use the same core function so the two surfaces cannot disagree.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt


# ---------------------------------------------------------------------------
# Core: what's missing?
# ---------------------------------------------------------------------------
def _get_missing_free_items(return_against: str, current_rows: list[dict]) -> list[dict]:
    """Return the list of free-item rows from ``return_against`` that are not
    yet reflected in ``current_rows`` (the in-flight return document).

    A row is considered "reflected" if the same ``item_code`` appears in
    ``current_rows`` with **any** (typically negative) qty — cashier is free
    to key in an alternative qty (e.g. customer keeping some units), we only
    prompt when the row is entirely absent.

    Returns a list of dicts::

        [{"item_code": "EAR-STD", "item_name": "Earphones (Std)",
          "qty": 1.0,  # positive; the return will negate it
          "uom": "Nos",
          "warehouse": "Store 001 - CH",
          "source_row": "SIItem-...."}, ...]

    Empty list when: no return_against, no free items on original, or every
    free item is already on the return.
    """
    if not return_against:
        return []

    # ``return_against`` in POS returns links to the original Sales Invoice.
    original_free_rows = frappe.get_all(
        "Sales Invoice Item",
        filters={"parent": return_against, "is_free_item": 1, "parenttype": "Sales Invoice"},
        fields=["name", "item_code", "item_name", "qty", "uom", "warehouse"],
        limit_page_length=0,
    )
    if not original_free_rows:
        return []

    current_item_codes = {
        (r.get("item_code") or "").strip()
        for r in (current_rows or [])
        if r.get("item_code")
    }

    missing: list[dict] = []
    for row in original_free_rows:
        if row.item_code in current_item_codes:
            continue
        missing.append({
            "item_code": row.item_code,
            "item_name": row.item_name,
            "qty": flt(row.qty),
            "uom": row.uom,
            "warehouse": row.warehouse,
            "source_row": row.name,
        })
    return missing


# ---------------------------------------------------------------------------
# Server-side validate hook — safety net
# ---------------------------------------------------------------------------
def validate_free_item_returns(doc, method=None):
    """Non-blocking msgprint if the return is missing free-item rows.

    Wired via ``ch_pos.hooks.doc_events['Sales Invoice']['validate']``.

    NEVER throws — the customer may legitimately keep the free item.
    This is a UX safety net for the (much more common) case where the
    cashier forgot to add the linked gift to the return.
    """
    if not getattr(doc, "is_return", 0):
        return
    if not getattr(doc, "return_against", None):
        return
    # Skip during Data Import / API bulk flows — the caller has already
    # decided what to include.
    if frappe.flags.get("in_import") or getattr(doc.flags, "ignore_free_item_return_guard", False):
        return

    current_rows = [
        {"item_code": r.item_code} for r in (doc.get("items") or [])
    ]
    missing = _get_missing_free_items(doc.return_against, current_rows)
    if not missing:
        return

    # Format a compact, actionable message. Link the original invoice so
    # the cashier can double-check what was gifted.
    lines = [
        _("Row {0}: <b>{1}</b> — qty {2} {3}").format(
            i + 1,
            m["item_name"] or m["item_code"],
            m["qty"],
            m["uom"] or "",
        )
        for i, m in enumerate(missing)
    ]
    src_link = frappe.utils.get_link_to_form("Sales Invoice", doc.return_against)
    frappe.msgprint(
        msg=_(
            "The original sale {0} included the following free / gift item(s) "
            "that are not on this return:<br><br>{1}<br><br>"
            "If the customer is returning them too, add them to the return "
            "(qty will be negated automatically). If the customer is keeping "
            "them, ignore this message and submit as-is."
        ).format(src_link, "<br>".join(lines)),
        title=_("Free items missing from return"),
        indicator="orange",
    )


# ---------------------------------------------------------------------------
# Whitelisted helper for the client script
# ---------------------------------------------------------------------------
@frappe.whitelist()
def get_missing_free_items(return_against: str, current_items: str | None = None):
    """Whitelisted RPC used by ``custom/pos_invoice.js`` to render a dialog.

    Args:
        return_against: name of the original Sales Invoice.
        current_items: JSON-serialised list of ``{"item_code": ...}`` rows
            already present in the in-flight return cart. Optional — when
            None or empty we treat the cart as empty (i.e. every free item
            on the original is missing).

    Returns:
        List[dict] — see ``_get_missing_free_items``.
    """
    if not return_against:
        return []

    rows: list[dict] = []
    if current_items:
        try:
            parsed = json.loads(current_items) if isinstance(current_items, str) else current_items
            if isinstance(parsed, list):
                rows = [r for r in parsed if isinstance(r, dict)]
        except (ValueError, TypeError):
            # Bad payload from the client — fall back to empty cart,
            # which means we surface every free row on the original.
            rows = []

    # Read-only lookup, no side effects — safe as a whitelisted method.
    return _get_missing_free_items(return_against, rows)
