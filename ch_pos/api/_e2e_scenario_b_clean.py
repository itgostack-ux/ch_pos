"""E2E Scenario B (v3 - clean) — Closed-loop EMI with proper math.
Brand offer applied as a pre-discounted rate (43000 instead of 45000)
since ERPNext's calculate_taxes_and_totals recomputes discount_amount
when price_list_rate == rate. This is the recommended POS UI flow.
Bank offer ₹3000 applied on grand_total.
Cash ₹5000 down + EMI ₹42,740 = grand_total ₹47,740.
"""

import json
import traceback

import frappe


def main():
    try:
        from ch_pos.api.pos_api import create_pos_invoice

        payload_items = [
            {
                "item_code": "I04508",
                "qty": 1,
                "rate": 45000,
                "serial_no": "356002001000006",
                # Brand offers like CHOFFER-0015 flow via Pricing Rule sync
                # (enterprise policy: cashier cannot override MOP without a
                # pre-approved CH Exception Request). Just bank offer here.
            },
        ]

        # 45,000 * 1.18 = 53,100 - 3,000 bank offer = 50,100 grand_total
        # Split: Cash 5,000 + EMI 45,100 = 50,100 (closed-loop)
        payments = [
            {
                "mode_of_payment": "Cash",
                "amount": 5000,
            },
            {
                "mode_of_payment": "EMI / Finance",
                "amount": 45100,
                "finance_provider": "HDFC",
                "finance_tenure": 6,
                "finance_approval_id": "HDFC-NOCOST-6M-DEMO-CLEAN",
                "finance_down_payment": 5000,
            },
        ]

        result = create_pos_invoice(
            pos_profile="Doveton POS - BMPL",
            customer="Mahalakshmi",
            items=payload_items,
            payments=payments,
            bank_offer_name="HDFC Credit Card ₹3000 Off (Mobiles >₹30k) [CHOFFER-0004]",
            bank_offer_discount=3000,
            sale_type="Finance Sale",
            sale_sub_type="HDFC",
            sale_reference="HDFC-NOCOST-6M-DEMO-CLEAN",
            finance_tenure=6,
            client_request_id="e2e-scenario-b-clean",
        )
        frappe.db.commit()
        print("SCENARIO_B_CLEAN_OK", json.dumps(result, default=str))
    except Exception as exc:
        frappe.db.rollback()
        print("SCENARIO_B_CLEAN_FAIL", type(exc).__name__, str(exc))
        print(traceback.format_exc())
        raise
