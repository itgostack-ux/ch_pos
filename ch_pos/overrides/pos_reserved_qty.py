"""
Override for erpnext.accounts.doctype.pos_invoice.pos_invoice.get_stock_availability

Why this override exists:
  The upstream get_pos_reserved_qty_from_table only counts *submitted* POS invoices
  that have not yet been consolidated. This misses:
    1. Draft POS invoices (already in the cashier's cart — stock should appear reserved)
    2. Submitted invoices whose Stock Ledger Entries have already been posted (double-count)

  Rather than patching the upstream file (which would be overwritten on bench update),
  we override the whitelisted get_stock_availability endpoint here and inline the
  corrected reserved-qty logic.
"""

import frappe
from frappe.utils import flt

from erpnext.accounts.doctype.pos_invoice.pos_invoice import (
    get_bin_qty,
    get_bundle_availability,
    get_product_bundle_stock_availability,
    is_negative_stock_allowed,
)


def _get_pos_reserved_qty_from_table(child_table, item_code, warehouse):
    """
    Fixed version of upstream get_pos_reserved_qty_from_table.

    Counts:
      - Submitted POS invoices that have NOT yet posted stock (SLE absent)
      - Draft POS invoices (item already in cashier's cart)

    Excludes consolidated invoices and invoices whose SLEs have already been
    written (stock already deducted — should not be double-counted as reserved).
    """
    qty_column = "qty" if child_table == "Packed Item" else "stock_qty"

    reserved_qty = frappe.db.sql(
        f"""
            SELECT COALESCE(SUM(p_item.`{qty_column}`), 0) AS stock_qty
            FROM `tabPOS Invoice` p_inv
            JOIN `tab{child_table}` p_item ON p_item.parent = p_inv.name
            WHERE IFNULL(p_inv.consolidated_invoice, '') = ''
              AND p_item.item_code = %(item_code)s
              AND p_item.warehouse = %(warehouse)s
              AND (
                    p_inv.docstatus = 0
                    OR (
                        p_inv.docstatus = 1
                        AND NOT EXISTS (
                            SELECT 1
                            FROM `tabStock Ledger Entry` sle
                            WHERE sle.voucher_type = 'POS Invoice'
                              AND sle.voucher_no = p_inv.name
                              AND IFNULL(sle.is_cancelled, 0) = 0
                        )
                    )
              )
        """,
        {"item_code": item_code, "warehouse": warehouse},
        as_dict=True,
    )

    return flt(reserved_qty[0].stock_qty) if reserved_qty else 0


def _get_pos_reserved_qty(item_code, warehouse):
    pinv_item_reserved_qty = _get_pos_reserved_qty_from_table(
        "POS Invoice Item", item_code, warehouse
    )
    packed_item_reserved_qty = _get_pos_reserved_qty_from_table(
        "Packed Item", item_code, warehouse
    )
    return flt(pinv_item_reserved_qty) + flt(packed_item_reserved_qty)


@frappe.whitelist()
def get_stock_availability(item_code, warehouse):
    """
    Overrides erpnext.accounts.doctype.pos_invoice.pos_invoice.get_stock_availability
    using the corrected _get_pos_reserved_qty_from_table above.
    """
    if frappe.db.get_value("Item", item_code, "is_stock_item"):
        bin_qty = get_bin_qty(item_code, warehouse)
        pos_sales_qty = _get_pos_reserved_qty(item_code, warehouse)
        return (
            bin_qty - pos_sales_qty,
            True,
            is_negative_stock_allowed(item_code=item_code),
        )
    else:
        if frappe.db.exists("Product Bundle", {"name": item_code, "disabled": 0}):
            return get_bundle_availability(item_code, warehouse), True, False
        else:
            return 0, False, False
