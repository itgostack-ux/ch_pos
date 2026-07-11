from contextlib import contextmanager

import frappe
from frappe.utils import cint, flt, nowdate


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


def test_import_derives_rate_from_amount_when_rate_missing():
    doc = _make_import_invoice(rate=None, amount=246)
    from ch_pos.overrides.pos_invoice import _apply_import_item_details

    item = doc.items[0]
    _apply_import_item_details(doc, item, {})

    assert flt(item.rate) == 123
    assert flt(item.amount) == 246


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


def test_import_sets_ignore_pricing_rule():
    """Historic import must reflect the file exactly. A "free item" Pricing Rule
    would otherwise inject an EXTRA line during validate() which, with
    update_stock=1, posts its own Stock Ledger Entry — a single imported line
    then shows as TWO stock movements. The import path forces
    ignore_pricing_rule=1 to prevent that."""
    from ch_pos.overrides.pos_invoice import _hydrate_imported_sales_invoice_defaults

    doc = _make_import_invoice()
    with _data_import_flags(doc):
        try:
            _hydrate_imported_sales_invoice_defaults(doc)
        except Exception:
            # Price/tax hydration can fail on incomplete _Test fixtures; the
            # ignore_pricing_rule flag is set BEFORE that work, which is exactly
            # what this test asserts.
            pass
    assert cint(doc.ignore_pricing_rule) == 1, (
        "data-import Sales Invoice must set ignore_pricing_rule=1"
    )


def test_non_import_does_not_force_ignore_pricing_rule():
    """Live POS / normal sales MUST keep applying Pricing Rules (free bundles,
    offers). The import guard must not touch ignore_pricing_rule outside a
    data-import context."""
    from ch_pos.overrides.pos_invoice import _hydrate_imported_sales_invoice_defaults

    doc = _make_import_invoice()
    doc.ignore_pricing_rule = 0
    # No import flags set → the function must early-return unchanged.
    _hydrate_imported_sales_invoice_defaults(doc)
    assert cint(doc.ignore_pricing_rule) == 0, (
        "non-import path must NOT force ignore_pricing_rule (would break live pricing)"
    )


def _ensure_test_fixtures():
    """Make the suite deterministic on a long-lived dev DB.

    These smoke tests run against the seeded dev site (via `bench execute
    ...run`), where `_Test Item` can be left disabled by earlier activity —
    which fails item-price hydration with "Item _Test Item is disabled". Enable
    it here. Under `bench run-tests` the fixtures are created fresh + enabled,
    so this is a no-op there.
    """
    if frappe.db.exists("Item", ITEM) and cint(frappe.db.get_value("Item", ITEM, "disabled")):
        frappe.db.set_value("Item", ITEM, "disabled", 0)
        frappe.db.commit()

    # _Test Customer can carry a DANGLING loyalty_program (a program that was
    # created by the loyalty backfill and later deleted/reseeded). insert()
    # then fails link validation ("Could not find Loyalty Program ..."). Clear
    # the reference when the program no longer exists so the suite is
    # deterministic. (Real defensive handling of this is discussed separately.)
    lp = frappe.db.get_value("Customer", CUSTOMER, "loyalty_program")
    if lp and not frappe.db.exists("Loyalty Program", lp):
        frappe.db.set_value("Customer", CUSTOMER, "loyalty_program", None)
        frappe.db.commit()


def test_import_missing_price_raises():
    """An import must use the price from the uploaded file — the code never
    fabricates a price from the Item master. A line with neither rate nor amount
    after hydration means the CSV omitted the price for that row; the guard must
    raise (naming the row) instead of silently booking a zero-value, zero-tax
    invoice that posts nothing to the GL (the reported bug)."""
    from ch_pos.overrides.pos_invoice import _guard_imported_line_has_price

    doc = _make_import_invoice()  # no rate, no amount → nothing from the "file"
    doc.items[0].rate = 0
    doc.items[0].amount = 0
    raised = False
    try:
        _guard_imported_line_has_price(doc)
    except frappe.ValidationError:
        raised = True
    assert raised, "import line with no price must raise, not book a ₹0 invoice"


def test_import_priced_line_passes_guard():
    """A line whose price came from the file must pass the guard untouched."""
    from ch_pos.overrides.pos_invoice import _guard_imported_line_has_price

    doc = _make_import_invoice(rate=100)
    _guard_imported_line_has_price(doc)  # must NOT raise
    assert flt(doc.items[0].rate) == 100


def test_import_free_line_skips_price_guard():
    """Free / free-bundle lines legitimately have rate 0 and must be exempt."""
    from ch_pos.overrides.pos_invoice import _guard_imported_line_has_price

    doc = _make_import_invoice()
    doc.items[0].rate = 0
    doc.items[0].amount = 0
    doc.items[0].is_free_item = 1
    _guard_imported_line_has_price(doc)  # must NOT raise


def run():
    _ensure_test_fixtures()
    test_import_fetches_item_price_amount_and_taxes()
    test_import_preserves_explicit_rate_and_calculates_amount_and_taxes()
    test_import_derives_rate_from_amount_when_rate_missing()
    test_import_insert_path_populates_defaults()
    test_import_sets_ignore_pricing_rule()
    test_non_import_does_not_force_ignore_pricing_rule()
    test_import_missing_price_raises()
    test_import_priced_line_passes_guard()
    test_import_free_line_skips_price_guard()
    return {"status": "ok", "tests": 9}
