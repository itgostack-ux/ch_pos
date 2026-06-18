from contextlib import contextmanager

import frappe
from frappe.utils import flt, nowdate


COMPANY = "_Test Company"
CUSTOMER = "_Test Customer"
ITEM = "_Test Item"
PRICE_LIST = "_Test Selling Price List"
TAX_TEMPLATE = "_Test Sales Taxes and Charges Template - _TC"
RECEIVABLE = "_Test Receivable - _TC"
INCOME_ACCOUNT = "_Test Account Sales - _TC"
COST_CENTER = "_Test Cost Center - _TC"


def _make_import_invoice(rate=None, amount=None):
    row = {
        "item_code": ITEM,
        "qty": 2,
        "income_account": INCOME_ACCOUNT,
        "cost_center": COST_CENTER,
    }
    if rate is not None:
        row["rate"] = rate
    if amount is not None:
        row["amount"] = amount

    return frappe.get_doc(
        {
            "doctype": "Sales Invoice",
            "naming_series": "ACC-SINV-.YYYY.-",
            "company": COMPANY,
            "customer": CUSTOMER,
            "posting_date": nowdate(),
            "due_date": nowdate(),
            "debit_to": RECEIVABLE,
            "currency": "INR",
            "conversion_rate": 1,
            "selling_price_list": PRICE_LIST,
            "price_list_currency": "INR",
            "plc_conversion_rate": 1,
            "taxes_and_charges": TAX_TEMPLATE,
            "items": [row],
        }
    )


@contextmanager
def _data_import_flags(doc):
    old_global_flag = getattr(frappe.flags, "in_import", False)
    old_doc_flag = getattr(doc.flags, "in_import", False)
    frappe.flags.in_import = True
    doc.flags.in_import = True
    try:
        yield
    finally:
        frappe.flags.in_import = old_global_flag
        doc.flags.in_import = old_doc_flag


def _validate_as_data_import(doc):
    with _data_import_flags(doc):
        doc.run_method("validate")


def test_import_fetches_item_price_amount_and_taxes():
    doc = _make_import_invoice()
    _validate_as_data_import(doc)

    item = doc.items[0]
    assert flt(item.price_list_rate) > 0
    assert flt(item.rate) == flt(item.price_list_rate)
    assert flt(item.amount) == flt(item.rate) * 2
    assert doc.taxes
    assert flt(doc.total_taxes_and_charges) > 0
    assert flt(doc.grand_total) > flt(doc.net_total)


def test_import_preserves_explicit_rate_and_calculates_amount_and_taxes():
    doc = _make_import_invoice(rate=123)
    _validate_as_data_import(doc)

    item = doc.items[0]
    assert flt(item.rate) == 123
    assert flt(item.amount) == 246
    assert doc.taxes
    assert flt(doc.total_taxes_and_charges) > 0
    assert flt(doc.grand_total) > flt(doc.net_total)


def test_import_insert_path_populates_defaults():
    doc = _make_import_invoice()
    try:
        with _data_import_flags(doc):
            doc.insert(ignore_permissions=True)

        item = doc.items[0]
        assert flt(item.rate) > 0
        assert flt(item.amount) > 0
        assert doc.taxes
        assert flt(doc.total_taxes_and_charges) > 0
        assert flt(doc.grand_total) > flt(doc.net_total)
    finally:
        frappe.db.rollback()


def run():
    test_import_fetches_item_price_amount_and_taxes()
    test_import_preserves_explicit_rate_and_calculates_amount_and_taxes()
    test_import_insert_path_populates_defaults()
    return {"status": "ok", "tests": 3}
