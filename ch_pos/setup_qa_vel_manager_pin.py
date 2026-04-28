"""Dev helper: ensure QA-VEL has an active manager PIN for opening approvals."""

import frappe


PIN_VALUE = "1234"
STORE = "QA-VEL"
USER = "Administrator"


def run():
    existing = frappe.db.get_value(
        "CH Manager PIN",
        {"user": USER, "store": STORE},
        "name",
    )

    # CH Manager PIN is effectively one-record-per-user in this setup
    # (name is user like "Administrator"). Reuse that record if present.
    if not existing:
        existing = frappe.db.get_value("CH Manager PIN", {"user": USER}, "name")

    if existing:
        doc = frappe.get_doc("CH Manager PIN", existing)
        doc.employee_name = doc.employee_name or "Test Manager"
        # Keep global so same PIN works across QA stores in dev.
        doc.store = ""
        doc.is_active = 1
        doc.can_approve_opening = 1
        doc.can_approve_closing = 1
        doc.can_override_business_date = 1
        doc.pin_hash = PIN_VALUE
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        print(f"Updated CH Manager PIN: {doc.name} (global store PIN)")
        return

    # Create a store-specific manager PIN for QA-VEL
    doc = frappe.get_doc(
        {
            "doctype": "CH Manager PIN",
            "user": USER,
            "employee_name": "Test Manager",
            "store": STORE,
            "is_active": 1,
            "pin_hash": PIN_VALUE,
            "can_approve_opening": 1,
            "can_approve_closing": 1,
            "can_override_business_date": 1,
        }
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    print(f"Created CH Manager PIN: {doc.name} for {STORE}")
