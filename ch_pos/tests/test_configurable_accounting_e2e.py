from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, now_datetime, nowdate


STORE_CODE = "TEST-POS-CONFIG"
PROFILE_NAME = "_Test CH POS Configurable Accounting"
COMPANY_ADDRESS_TITLE = "Test CH POS Company"


def _delete_if_exists(doctype, name):
    if frappe.db.exists(doctype, name):
        frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def _cleanup(invoice_name=None):
    if invoice_name and frappe.db.exists("Sales Invoice", invoice_name):
        invoice = frappe.get_doc("Sales Invoice", invoice_name)
        if invoice.docstatus == 1:
            invoice.flags.ignore_permissions = True
            invoice.flags.ignore_validate = True
            invoice.cancel()
        frappe.delete_doc(
            "Sales Invoice",
            invoice_name,
            force=True,
            ignore_permissions=True,
            delete_permanently=True,
        )

    for session_name in frappe.get_all(
        "CH POS Session", filters={"pos_profile": PROFILE_NAME}, pluck="name"
    ):
        frappe.db.delete("CH POS Session", {"name": session_name})
    frappe.db.delete("CH Business Date", {"store": STORE_CODE})
    frappe.db.delete("POS Executive", {"store": STORE_CODE})
    for address_name in frappe.get_all(
        "Address", filters={"address_title": COMPANY_ADDRESS_TITLE}, pluck="name"
    ):
        _delete_if_exists("Address", address_name)
    _delete_if_exists("POS Profile Extension", PROFILE_NAME)
    _delete_if_exists("POS Profile", PROFILE_NAME)
    _delete_if_exists("CH Store", STORE_CODE)
    frappe.db.commit()


def _make_profile():
    profile = frappe.get_doc(
        {
            "doctype": "POS Profile",
            "name": PROFILE_NAME,
            "company": "_Test Company",
            "warehouse": "_Test Warehouse - _TC",
            "currency": "INR",
            "cost_center": "Main - _TC",
            "income_account": "Sales - _TC",
            "expense_account": "Cost of Goods Sold - _TC",
            "write_off_account": "_Test Write Off - _TC",
            "write_off_cost_center": "_Test Write Off Cost Center - _TC",
            "selling_price_list": "_Test Price List",
            "territory": "_Test Territory",
            "customer_group": "_Test Customer Group",
            "disabled": 0,
            "payments": [{"mode_of_payment": "Cash", "default": 1}],
        }
    )
    profile.insert(ignore_permissions=True)
    return profile


def _make_store_and_session():
    frappe.get_doc(
        {
            "doctype": "Address",
            "address_title": COMPANY_ADDRESS_TITLE,
            "address_type": "Office",
            "address_line1": "Test Company Address",
            "city": "Mumbai",
            "state": "Maharashtra",
            "country": "India",
            "pincode": "400001",
            "gstin": "27AAPFU0939F1ZV",
            "gst_state": "Maharashtra",
            "gst_state_number": "27",
            "is_your_company_address": 1,
            "links": [{"link_doctype": "Company", "link_name": "_Test Company"}],
        }
    ).insert(ignore_permissions=True)
    store = frappe.get_doc(
        {
            "doctype": "CH Store",
            "store_code": STORE_CODE,
            "store_name": "Test POS Configurable Accounting",
            "company": "_Test Company",
            "disabled": 0,
            "store_status": "Active",
            "is_retail_enabled": 1,
        }
    )
    store.insert(ignore_permissions=True)
    frappe.db.set_value(
        "CH Store",
        store.name,
        {"warehouse": "_Test Warehouse - _TC", "pos_profile": PROFILE_NAME},
        update_modified=False,
    )
    frappe.get_doc(
        {
            "doctype": "POS Profile Extension",
            "pos_profile": PROFILE_NAME,
            "store": store.name,
            "disabled": 0,
        }
    ).insert(ignore_permissions=True)
    frappe.get_doc(
        {
            "doctype": "CH Business Date",
            "store": store.name,
            "business_date": nowdate(),
            "status": "Open",
            "opened_on": now_datetime(),
        }
    ).insert(ignore_permissions=True)
    frappe.get_doc(
        {
            "doctype": "POS Executive",
            "executive_name": "Test POS Configurable Accounting",
            "user": frappe.session.user,
            "store": store.name,
            "company": "_Test Company",
            "role": "Manager",
            "is_active": 1,
        }
    ).insert(ignore_permissions=True)
    session = frappe.get_doc(
        {
            "doctype": "CH POS Session",
            "company": "_Test Company",
            "pos_profile": PROFILE_NAME,
            "store": store.name,
            "user": frappe.session.user,
            "business_date": nowdate(),
            "shift_start": now_datetime(),
            "opening_cash": 1000,
            "status": "Open",
        }
    )
    session.insert(ignore_permissions=True)
    frappe.db.set_value("CH POS Session", session.name, "docstatus", 1)
    frappe.db.commit()


def run():
    frappe.set_user("Administrator")
    invoice_name = None
    original_item_values = frappe.db.get_value(
        "Item", "_Test Item 2", ["ch_plm_status", "gst_hsn_code"], as_dict=True
    )
    _cleanup()
    try:
        frappe.db.set_value("Item", "_Test Item 2", "ch_plm_status", "Approved")
        frappe.db.set_value("Item", "_Test Item 2", "gst_hsn_code", "851713")
        _make_profile()
        _make_store_and_session()

        from ch_pos.api.pos_api import create_pos_invoice

        result = create_pos_invoice(
            pos_profile=PROFILE_NAME,
            customer="_Test Customer",
            items=[{"item_code": "_Test Item 2", "qty": 1, "rate": 118}],
            mode_of_payment="Cash",
            amount_paid=118,
        )
        invoice_name = result["name"]
        invoice = frappe.get_doc("Sales Invoice", invoice_name)
        assert invoice.docstatus == 1
        assert abs(
            flt(invoice.grand_total)
            - flt(invoice.net_total)
            - flt(invoice.total_taxes_and_charges)
        ) <= 0.02

        entries = frappe.get_all(
            "GL Entry",
            filters={
                "voucher_type": "Sales Invoice",
                "voucher_no": invoice.name,
                "is_cancelled": 0,
            },
            fields=["debit", "credit"],
        )
        assert entries
        debit = flt(sum(flt(entry.debit) for entry in entries), 2)
        credit = flt(sum(flt(entry.credit) for entry in entries), 2)
        assert abs(debit - credit) <= 0.02
        assert frappe.db.exists(
            "Payment Ledger Entry",
            {"voucher_type": "Sales Invoice", "voucher_no": invoice.name},
        )

        print(
            {
                "invoice": invoice.name,
                "grand_total": invoice.grand_total,
                "net_total": invoice.net_total,
                "tax": invoice.total_taxes_and_charges,
                "gl_debit": debit,
                "gl_credit": credit,
            }
        )
        return invoice.name
    finally:
        _cleanup(invoice_name)
        frappe.db.set_value(
            "Item",
            "_Test Item 2",
            {
                "ch_plm_status": original_item_values.ch_plm_status or "NPI",
                "gst_hsn_code": original_item_values.gst_hsn_code,
            },
        )
        frappe.db.commit()


class TestConfigurableAccountingE2E(IntegrationTestCase):
    def test_session_invoice_tax_and_ledger(self):
        with patch(
            "frappe.workflow.doctype.workflow_action.workflow_action.send_email_alert",
            return_value=False,
        ):
            self.assertTrue(run())
