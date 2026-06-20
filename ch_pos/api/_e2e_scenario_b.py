"""E2E Scenario B — Finance/EMI + brand offer + bank offer.
Cart: Vivo phone IMEI 11 with brand offer ₹2000 (Vivo Exchange Bonus, CHOFFER-0012)
Payment: HDFC EMI No-Cost 6M (CHOFFER-0005) with ₹3000 bank offer (CHOFFER-0004) +
finance/EMI down-payment cash ₹5000, balance EMI / Finance.
Customer: Mahalakshmi.
"""

import json
import traceback

import frappe


def main():
    try:
        from ch_pos.api.pos_api import create_pos_invoice

        # Line offer: Brand bonus ₹2000 (Xiaomi cashback CHOFFER-0015 spirit;
        # use Xiaomi 14 Ultra @ ₹45,000 — qualifies HDFC >₹30k offer)
        # Bank offer: HDFC ₹3000 (>₹30k mobile)
        # Net after brand offer = 45,000 - 2,000 = 43,000
        # Grand = 43,000 * 1.18 = 50,740 — less ₹3,000 bank offer ≈ 47,000
        payload_items = [
            {
                "item_code": "I04508",
                "qty": 1,
                "rate": 45000,
                "serial_no": "356002001000004",
                "discount_amount": 2000,  # Xiaomi brand offer (CHOFFER-0015 spirit)
                "custom_brand_offer_ref": "CHOFFER-0015",
            },
        ]

        # Net 43,000 × 1.18 = 50,740; less ₹3,000 bank-offer on grand_total
        # → grand_total ≈ 47,200 (ERPNext back-solves). Split: Cash 5,000 + EMI 42,200.
        payments = [
            {
                "mode_of_payment": "Cash",
                "amount": 5000,
            },
            {
                "mode_of_payment": "EMI / Finance",
                "amount": 42200,
                "finance_provider": "HDFC",
                "finance_tenure": 6,
                "finance_approval_id": "HDFC-NOCOST-6M-DEMO-002",
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
            sale_reference="HDFC-NOCOST-6M-DEMO-002",
            finance_tenure=6,
            client_request_id="e2e-scenario-b-002",
        )
        frappe.db.commit()
        print("SCENARIO_B_OK", json.dumps(result, default=str))
    except Exception as exc:
        frappe.db.rollback()
        print("SCENARIO_B_FAIL", type(exc).__name__, str(exc))
        print(traceback.format_exc())
        raise
