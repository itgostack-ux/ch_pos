"""Re-attach POS invoices to closing entries that were submitted holding nothing.

Every POS Closing Entry created while a store's business date lagged the
calendar swept zero invoices and submitted with a grand_total of 0.00 — see
``session_api._opening_period_start`` for why. On this bench that was 42 of 43
submitted closing entries.

What this does and does not touch:

* It does **not** move money. Each POS Sales Invoice posts its own GL at
  submission, so the takings are already in the ledger. The damage is to
  reconciliation: the closing entry claims the till took nothing, and the
  invoices carry no ``pos_closing_entry``, so they read as never cashed up and
  stay eligible to be swept into some later day's entry.
* It re-attaches each session's invoices to that session's own closing entry,
  and recomputes the entry's totals and payment reconciliation from them.

Idempotent: an invoice already carrying a ``pos_closing_entry`` is skipped, and
a reference row is only inserted when it is missing. Safe to re-run.
"""

import frappe
from frappe.utils import flt

from ch_pos.patches.v1_0.backfill_standard_pos_invoice_closings import _reconcile_closing


def execute():
    for doctype in ("POS Closing Entry", "CH POS Session", "Sales Invoice"):
        if not frappe.db.exists("DocType", doctype):
            return
    if not frappe.db.has_column("Sales Invoice", "custom_ch_pos_session"):
        return

    # A closing entry that reported nothing, whose session in fact billed
    # something. Joining through the session rather than the time window is the
    # point: the window is exactly what was broken.
    rows = frappe.db.sql(
        """
        SELECT pce.name AS closing, si.name AS invoice, si.posting_date, si.customer,
               si.grand_total, si.is_return, si.return_against, si.total_qty,
               si.net_total, si.total_taxes_and_charges
          FROM `tabPOS Closing Entry` pce
          JOIN `tabPOS Opening Entry` poe ON poe.name = pce.pos_opening_entry
          JOIN `tabCH POS Session` s ON s.pos_opening_entry = poe.name
          JOIN `tabSales Invoice` si ON si.custom_ch_pos_session = s.name
         WHERE pce.docstatus = 1
           AND IFNULL(pce.grand_total, 0) = 0
           AND si.docstatus = 1
           AND si.is_pos = 1
           AND IFNULL(si.pos_closing_entry, '') = ''
        """,
        as_dict=True,
    )
    if not rows:
        return

    by_closing = {}
    for row in rows:
        by_closing.setdefault(row.closing, []).append(row)

    repaired = 0
    recovered = 0.0
    for closing_name, invoices in by_closing.items():
        try:
            for row in invoices:
                frappe.db.set_value(
                    "Sales Invoice", row.invoice, "pos_closing_entry", closing_name,
                    update_modified=False,
                )
                if not frappe.db.exists(
                    "Sales Invoice Reference",
                    {"parent": closing_name, "parentfield": "sales_invoices",
                     "sales_invoice": row.invoice},
                ):
                    frappe.get_doc({
                        "doctype": "Sales Invoice Reference",
                        "parent": closing_name,
                        "parenttype": "POS Closing Entry",
                        "parentfield": "sales_invoices",
                        "sales_invoice": row.invoice,
                        "posting_date": row.posting_date,
                        "customer": row.customer,
                        "grand_total": row.grand_total,
                        "is_return": row.is_return,
                        "return_against": row.return_against,
                    }).insert(ignore_permissions=True)

            # _reconcile_closing expects rows keyed by `name`, not `invoice`.
            _reconcile_closing(
                closing_name,
                [frappe._dict({**row, "name": row.invoice}) for row in invoices],
            )
            repaired += 1
            recovered += sum(flt(row.grand_total) for row in invoices)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"POS closing repair failed for {closing_name}")

    frappe.db.commit()
    print(
        f"ch_pos: repaired {repaired} POS Closing Entries that had reported zero; "
        f"re-attached {len(rows)} invoices worth {recovered:,.2f}"
    )
