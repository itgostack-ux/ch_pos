"""Diagnostic: insert SI but DON'T submit, print GL preview + key fields."""
import json
import traceback
import frappe


def main():
    try:
        from ch_pos.api.pos_api import _enforce_token_linkage
        from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import get_active_session
        from erpnext.accounts.general_ledger import process_gl_map

        pos_profile = "Doveton POS - BMPL"
        active = get_active_session(pos_profile)
        profile = frappe.get_cached_doc("POS Profile", pos_profile)

        inv = frappe.new_doc("Sales Invoice")
        inv.custom_ch_pos_session = active["name"]
        inv.pos_profile = pos_profile
        inv.customer = "Mahalakshmi"
        inv.company = profile.company
        inv.selling_price_list = profile.selling_price_list
        inv.currency = profile.currency or frappe.get_cached_value("Company", profile.company, "default_currency")
        inv.warehouse = profile.warehouse
        inv.posting_date = str(active.get("business_date"))
        inv.is_pos = 1
        inv.update_stock = 1
        inv.due_date = None

        inv.append("items", {
            "item_code": "MB000004-12GB-256GB-150W-WC-AFP",
            "qty": 1, "rate": 37000, "price_list_rate": 37000,
            "uom": "Nos", "warehouse": profile.warehouse,
            "serial_no": "14",
        })
        inv.append("items", {
            "item_code": "VAS-PROTECT-PLUS",
            "qty": 1, "rate": 1999, "price_list_rate": 1999,
            "uom": "Nos", "warehouse": profile.warehouse,
            "custom_warranty_plan": "CH-WP-2026-00001",
        })
        inv.append("items", {
            "item_code": "GF-VAS-SVC-20260620-01",
            "qty": 1, "rate": 1499, "price_list_rate": 1499,
            "uom": "Nos", "warehouse": profile.warehouse,
            "custom_warranty_plan": "CH-WP-2026-00006",
        })
        inv.append("payments", {"mode_of_payment": "Cash", "amount": 47788})
        for tax in (profile.get("taxes") or []):
            inv.append("taxes", {
                "charge_type": tax.charge_type,
                "account_head": tax.account_head,
                "description": tax.account_head,
                "rate": tax.rate,
                "cost_center": tax.cost_center,
            })
        inv.flags.ignore_permissions = True
        inv.flags.ignore_pos_validation = True
        inv.insert(ignore_permissions=True)

        print("INV", inv.name, "net_total", inv.net_total, "grand_total", inv.grand_total, "rounded_total", inv.rounded_total)
        for it in inv.items:
            print("  ITEM", it.item_code, "qty", it.qty, "rate", it.rate, "amount", it.amount,
                  "income_account", it.income_account, "expense_account", it.expense_account,
                  "serial_no", it.serial_no, "warehouse", it.warehouse)
        for p in inv.payments:
            print("  PAY", p.mode_of_payment, "amount", p.amount, "base_amount", p.base_amount, "account", p.account)
        for t in inv.taxes:
            print("  TAX", t.description, t.rate, t.tax_amount, t.account_head)

        gl_map = inv.get_gl_entries()
        print("GL_ENTRIES_COUNT", len(gl_map))
        for g in gl_map:
            print(f"  GL {g.account} DR {g.debit} CR {g.credit} {g.remarks[:60] if g.remarks else ''}")
        # cleanup
        frappe.db.rollback()
    except Exception as exc:
        frappe.db.rollback()
        print("DIAG_FAIL", type(exc).__name__, str(exc))
        traceback.print_exc()
