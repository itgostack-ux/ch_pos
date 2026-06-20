"""E2E Scenario C — Same customer no-GST → GST → no-GST swing.

Tests whether per-invoice GSTIN override is honoured AND whether next invoice
correctly reverts to no-GST (no carry-forward). Verifies India Compliance
field `billing_address_gstin` is populated (not just our shadow custom field).
"""

import json
import traceback

import frappe


def main():
    results = {"C1": None, "C2": None, "C3": None, "C1_audit": {}, "C2_audit": {}, "C3_audit": {}}
    try:
        from ch_pos.api.pos_api import create_pos_invoice

        # Use VAS-only carts (no IMEI inventory needed). Distinct external
        # IMEI per invoice so Active VAS Plan duplicate-guard does not fire.
        def _vas_only_cart(rate, ext_imei):
            return [{
                "item_code": "GF-VAS-SVC-20260620-01",
                "qty": 1,
                "rate": rate,
                "warranty_plan": "CH-WP-2026-00006",
                "customer_imei": ext_imei,
                "is_vas": 1,
            }]

        # ───────── C1 — no GST (baseline, like BMTNSI26000033) ─────────
        r1 = create_pos_invoice(
            pos_profile="Doveton POS - BMPL",
            customer="Mahalakshmi",
            items=_vas_only_cart(1499, "359000000000010"),
            payments=[{"mode_of_payment": "Cash", "amount": 1499}],
            client_request_id="e2e-scenario-c1-nogst-v2",
        )
        frappe.db.commit()
        results["C1"] = r1["name"]

        # ───────── C2 — same customer, ad-hoc GSTIN at billing ─────────
        # Karnataka GSTIN format: 29 + 10-char PAN + 1 + Z + 1-char checksum
        r2 = create_pos_invoice(
            pos_profile="Doveton POS - BMPL",
            customer="Mahalakshmi",
            items=_vas_only_cart(1499, "359000000000011"),
            payments=[{"mode_of_payment": "Cash", "amount": 1499}],
            customer_gstin="29ABCDE1234F1Z5",
            client_request_id="e2e-scenario-c2-gst-v2",
        )
        frappe.db.commit()
        results["C2"] = r2["name"]

        # ───────── C3 — same customer, back to no GST (no carry-forward) ─────────
        r3 = create_pos_invoice(
            pos_profile="Doveton POS - BMPL",
            customer="Mahalakshmi",
            items=_vas_only_cart(1499, "359000000000012"),
            payments=[{"mode_of_payment": "Cash", "amount": 1499}],
            client_request_id="e2e-scenario-c3-nogst-again-v2",
        )
        frappe.db.commit()
        results["C3"] = r3["name"]

        # Pull the audit fields
        for k, name in [("C1_audit", results["C1"]), ("C2_audit", results["C2"]), ("C3_audit", results["C3"])]:
            row = frappe.db.sql(
                """SELECT name, customer, gst_category, tax_id, billing_address_gstin,
                          custom_customer_gstin, company_gstin, place_of_supply,
                          net_total, total_taxes_and_charges, grand_total
                   FROM `tabSales Invoice` WHERE name=%s""",
                (name,), as_dict=True
            )
            results[k] = row[0] if row else {}

        print("SCENARIO_C_OK", json.dumps(results, default=str, indent=2))
    except Exception as exc:
        frappe.db.rollback()
        print("SCENARIO_C_FAIL", type(exc).__name__, str(exc))
        print("PARTIAL", json.dumps(results, default=str))
        print(traceback.format_exc())
        raise
