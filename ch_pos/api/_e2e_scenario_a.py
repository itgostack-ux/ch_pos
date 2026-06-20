"""E2E Scenario A — mixed cart:
  1. Mobile phone (Vivo) sold from inventory by IMEI 14
  2. VAS plan for THAT phone (in-store IMEI)
  3. VAS plan for the customer's OWN phone (external IMEI)
Payment: full Cash. Customer: Mahalakshmi (Unregistered, no GST).
"""

import json
import traceback

import frappe


def main():
    try:
        from ch_pos.api.pos_api import create_pos_invoice

        payload_items = [
            {
                "item_code": "MB000004-12GB-256GB-150W-WC-AFP",
                "qty": 1,
                "rate": 37000,
                "serial_no": "14",
            },
            {
                # VAS plan for phone being sold in this bill
                "item_code": "VAS-PROTECT-PLUS",
                "qty": 1,
                "rate": 1999,
                "warranty_plan": "CH-WP-2026-00001",
                "for_item_code": "MB000004-12GB-256GB-150W-WC-AFP",
                "for_serial_no": "14",
                "is_vas": 1,
            },
            {
                # VAS plan for customer-supplied external IMEI
                "item_code": "GF-VAS-SVC-20260620-01",
                "qty": 1,
                "rate": 1499,
                "warranty_plan": "CH-WP-2026-00006",
                "customer_imei": "356789123456790",
                "is_vas": 1,
            },
        ]

        # Pay net total only; pos_api rounding-adjuster inflates to grand_total
        gross = sum(it["qty"] * it["rate"] for it in payload_items)
        payments = [{"mode_of_payment": "Cash", "amount": gross}]

        result = create_pos_invoice(
            pos_profile="Doveton POS - BMPL",
            customer="Mahalakshmi",
            items=payload_items,
            payments=payments,
            client_request_id="e2e-scenario-a-001",
        )
        frappe.db.commit()
        print("SCENARIO_A_OK", json.dumps(result, default=str))
    except Exception as exc:
        frappe.db.rollback()
        print("SCENARIO_A_FAIL", type(exc).__name__, str(exc))
        print(traceback.format_exc())
        raise
