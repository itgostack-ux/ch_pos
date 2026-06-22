"""E2E smoke for GSTIN submit guard rollout.

Creates sample records (customer) and executes POS invoice creation flows to
verify billing still succeeds for:
1) No GSTIN (B2C)
2) Valid GSTIN (B2B)

Also checks that an invalid GSTIN is rejected server-side (defence-in-depth).
"""

import json
import traceback

import frappe


def _ensure_sample_customer():
    customer_name = "GSTIN Guard E2E 20260620"
    existing = frappe.db.get_value("Customer", {"customer_name": customer_name}, "name")
    if existing:
        return existing

    doc = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": customer_name,
            "customer_type": "Individual",
            "customer_group": "Individual",
            "territory": "All Territories",
            "mobile_no": "9000000020",
            "email_id": "gstin-guard-e2e@example.com",
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def _vas_only_cart(rate, ext_imei):
    return [
        {
            "item_code": "GF-VAS-SVC-20260620-01",
            "qty": 1,
            "rate": rate,
            "warranty_plan": "CH-WP-2026-00006",
            "customer_imei": ext_imei,
            "is_vas": 1,
        }
    ]


def main():
    results = {
        "sample_customer": None,
        "b2c_invoice": None,
        "b2b_invoice": None,
        "invalid_gstin_rejected": False,
        "invalid_gstin_error": "",
    }

    try:
        from ch_pos.api.pos_api import create_pos_invoice

        customer = _ensure_sample_customer()
        results["sample_customer"] = customer

        b2c = create_pos_invoice(
            pos_profile="Doveton POS - BMPL",
            customer=customer,
            items=_vas_only_cart(1499, "359000000001020"),
            payments=[{"mode_of_payment": "Cash", "amount": 1499}],
            client_request_id="e2e-gstin-guard-b2c-20260620",
        )
        frappe.db.commit()
        results["b2c_invoice"] = b2c.get("name")

        b2b = create_pos_invoice(
            pos_profile="Doveton POS - BMPL",
            customer=customer,
            items=_vas_only_cart(1499, "359000000001021"),
            payments=[{"mode_of_payment": "Cash", "amount": 1499}],
            customer_gstin="29ABCDE1234F1Z5",
            client_request_id="e2e-gstin-guard-b2b-20260620",
        )
        frappe.db.commit()
        results["b2b_invoice"] = b2b.get("name")

        try:
            create_pos_invoice(
                pos_profile="Doveton POS - BMPL",
                customer=customer,
                items=_vas_only_cart(1499, "359000000001022"),
                payments=[{"mode_of_payment": "Cash", "amount": 1499}],
                customer_gstin="3335YYI683ZV1Z",
                client_request_id="e2e-gstin-guard-invalid-20260620",
            )
            frappe.db.commit()
        except Exception as exc:
            frappe.db.rollback()
            results["invalid_gstin_rejected"] = True
            results["invalid_gstin_error"] = str(exc)

        if not results["invalid_gstin_rejected"]:
            raise RuntimeError("Expected invalid GSTIN rejection did not occur")

        print("GSTIN_GUARD_E2E_OK", json.dumps(results, default=str, indent=2))
    except Exception as exc:
        frappe.db.rollback()
        print("GSTIN_GUARD_E2E_FAIL", type(exc).__name__, str(exc))
        print("PARTIAL", json.dumps(results, default=str))
        print(traceback.format_exc())
        raise
