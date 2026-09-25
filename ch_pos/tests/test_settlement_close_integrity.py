"""Regression cover for the two defects that stopped stores settling.

Both were silent. Neither raised an error, and the POS reported success while
the day's money went nowhere — which is why they survived in production for
weeks. The tests below assert on *amounts*, never on "it did not throw".

Run:
  bench --site erpnext.local run-tests --app ch_pos \\
    --module ch_pos.tests.test_settlement_close_integrity
"""

import inspect
import unittest

import frappe
from frappe.utils import flt

from ch_pos.pos_core.variance_policy import classify_variance, get_variance_policy


class TestClosingEntryScope(unittest.TestCase):
    """A POS Closing Entry must carry its session's invoices.

    ERPNext scopes the entry by a clock window (``period_start_date .. now``)
    compared against ``Timestamp(posting_date, posting_time)``. POS invoices
    here are stamped ``posting_date = business date`` with ``posting_time =
    server clock``, so once a store's business date fell behind the calendar
    every invoice timestamped before the window opened and the entry swept
    nothing — 42 of 43 submitted closing entries carried grand_total 0.00.

    The window cannot simply be widened: ERPNext's
    ``Sales Invoice.validate_pos_opening_entry`` refuses to create a POS invoice
    unless the open entry starts today, so back-dating period_start_date stops
    the till billing instead. The scope is the session link.
    """

    def setUp(self):
        frappe.db.savepoint("settlement_close_integrity")
        self.addCleanup(frappe.db.rollback, save_point="settlement_close_integrity")

    def test_closing_entry_is_scoped_by_session_not_by_clock(self):
        from ch_pos.pos_core.doctype.ch_pos_session.ch_pos_session import CHPOSSession

        source = inspect.getsource(CHPOSSession._scope_closing_entry_to_session)
        self.assertIn(
            '"custom_ch_pos_session": self.name', source,
            "The closing entry must take its invoices from the session link.")
        self.assertIn(
            '"pos_closing_entry": ("in", ("", None))', source,
            "An invoice already on another closing entry must never be re-claimed.")

    def test_opening_entry_still_starts_today(self):
        """The constraint that makes the clock window unusable, pinned.

        If this ever stops being true, widening period_start_date becomes an
        option again — until then, back-dating it silently stops billing.
        """
        from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice

        source = inspect.getsource(SalesInvoice.validate_pos_opening_entry)
        self.assertIn("frappe.utils.today()", source)
        self.assertIn("Outdated POS Opening Entry", source)

        session_api_source = inspect.getsource(
            frappe.get_module("ch_pos.api.session_api"))
        self.assertIn(
            '"period_start_date": now_datetime()', session_api_source,
            "period_start_date must stay on the wall clock or the till cannot bill.")

    def test_a_sessions_invoices_are_all_claimed(self):
        """Against real data: every unconsolidated invoice on a session is picked up."""
        session_name = frappe.db.get_value(
            "Sales Invoice",
            {"docstatus": 1, "is_pos": 1, "custom_ch_pos_session": ("!=", "")},
            "custom_ch_pos_session")
        if not session_name:
            self.skipTest("no POS Sales Invoice linked to a session on this site")

        invoices = frappe.get_all(
            "Sales Invoice",
            filters={"custom_ch_pos_session": session_name, "docstatus": 1, "is_pos": 1},
            fields=["name", "grand_total"])
        # Free them, as they are at the moment a till closes.
        for row in invoices:
            frappe.db.set_value("Sales Invoice", row.name, "pos_closing_entry", None,
                                update_modified=False)

        session = frappe.get_doc("CH POS Session", session_name)

        class _ClosingStub:
            """Just the interface _scope_closing_entry_to_session writes through.

            Deliberately not a frappe._dict: that inherits dict.update, so a
            lambda assigned to `.update` is never reached and the totals the
            method writes are silently dropped.
            """

            def __init__(self, user):
                self.user = user
                self.captured = {}

            def set(self, field, value):
                self.captured[field] = value

            def update(self, values):
                self.captured.update(values)

        closing = _ClosingStub(
            frappe.db.get_value("Sales Invoice", invoices[0].name, "owner"))
        session._scope_closing_entry_to_session(closing)

        self.assertEqual(
            len(closing.captured.get("sales_invoices") or []), len(invoices),
            "a session invoice was left off its own closing entry")
        self.assertGreater(
            flt(closing.captured.get("grand_total")), 0,
            "a closing entry that holds invoices but totals zero is the original defect")
        self.assertEqual(
            flt(closing.captured["grand_total"]),
            sum(flt(row.grand_total) for row in invoices),
            "the closing entry total must equal what the session actually billed")


class TestVarianceBands(unittest.TestCase):
    """Over/short tolerance has to scale with what the till actually handled."""

    def test_small_till_keeps_an_absolute_floor(self):
        policy = get_variance_policy(1000)
        self.assertGreaterEqual(
            policy["accept_limit"], 100,
            "A percentage alone would give a kiosk a tolerance of a few rupees.",
        )

    def test_large_till_scales_with_turnover(self):
        small = get_variance_policy(1000)["accept_limit"]
        large = get_variance_policy(500000)["accept_limit"]
        self.assertGreater(
            large, small,
            "A till taking five lakh must not be held to a kiosk's rupee tolerance — "
            "that is the flat Rs 100 rule that made every close need a manager.",
        )

    def test_three_bands_not_two(self):
        basis = 100000
        policy = get_variance_policy(basis)
        inside = classify_variance(policy["accept_limit"] - 1, basis)
        middle = classify_variance(policy["accept_limit"] + 1, basis)
        outside = classify_variance(policy["approval_limit"] + 1, basis)

        self.assertFalse(inside["requires_reason"])
        self.assertFalse(inside["requires_approval"])

        self.assertTrue(middle["requires_reason"])
        self.assertFalse(
            middle["requires_approval"],
            "The middle band exists so an explainable difference does not need a manager "
            "physically present before the store can shut.",
        )

        self.assertTrue(outside["requires_reason"])
        self.assertTrue(outside["requires_approval"])

    def test_approval_band_can_never_sit_below_the_accept_band(self):
        """A settings typo must not make a variance both auto-accepted and blocked."""
        policy = get_variance_policy(0)
        self.assertGreaterEqual(policy["approval_limit"], policy["accept_limit"])


class TestExpectedCashHasOneSource(unittest.TestCase):
    """Settlement and session close must never compute different expected cash.

    They used to: settlement scoped by ``pos_profile + posting_date``, the
    session by ``custom_ch_pos_session``. A cashier could pass settlement and
    then be refused at close over a variance they were never shown, with the
    settlement already submitted and no reopen path in the POS UI.
    """

    def setUp(self):
        frappe.db.savepoint("expected_cash_source")
        self.addCleanup(frappe.db.rollback, save_point="expected_cash_source")

    def test_session_close_uses_the_settlement_snapshot(self):
        from ch_pos.pos_core.doctype.ch_pos_settlement.ch_pos_settlement import (
            build_settlement_snapshot,
        )

        name = frappe.db.get_value("CH POS Session", {"docstatus": 1}, "name")
        if not name:
            self.skipTest("no CH POS Session on this site")

        session = frappe.get_doc("CH POS Session", name)
        session.closing_cash_actual = 0
        session._calculate_totals()
        session._calculate_cash_variance()

        self.assertAlmostEqual(
            flt(session.closing_cash_expected),
            flt(build_settlement_snapshot(session)["expected_closing_cash"]),
            places=2,
            msg="The session close and the settlement disagree about expected cash. "
                "That disagreement is what stranded tills between settle and close.",
        )

    def test_snapshot_is_scoped_to_the_session(self):
        from ch_pos.pos_core.doctype.ch_pos_settlement.ch_pos_settlement import (
            build_settlement_snapshot,
        )
        import inspect

        source = inspect.getsource(build_settlement_snapshot)
        self.assertIn(
            "custom_ch_pos_session", source,
            "Expected cash must be scoped per session. Scoping it by pos_profile + "
            "posting_date double-counts every shift handover in a trading day.",
        )
        self.assertNotIn(
            "pi.pos_profile = %(pp)s", source,
            "The profile-and-date scope is back; see this test's docstring.",
        )
