"""A till with no card terminal must still take a card.

get_payment_machines returned manual_only ONLY for the shadow-live pilot. A
store that simply had no terminal got empty lists and no manual_only at all,
so the payment dialog rendered an empty "Select Machine" dropdown and Pay Now
refused -- the card could not be taken by any route. There are zero
CH Payment Machine rows on this bench, so that was every till.

A shop without a terminal has always taken cards: the cashier keys the RRN off
the bank's own slip. That path already existed for the pilot; it just was not
reachable.
"""

import unittest

import frappe

from ch_pos.api import payment_gateway_api as pg


class TestPaymentMachineFallback(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Administrator")
        self.sp = "payment_machine_fallback"
        frappe.db.savepoint(self.sp)
        self.company = frappe.db.get_value("Company", {"name": ("not like", "ZZZ %")}, "name")
        if not self.company:
            raise unittest.SkipTest("no company on this site")
        self.store = frappe.db.get_value("CH Store", {"company": self.company}, "name")
        if not self.store:
            raise unittest.SkipTest("no CH Store on this site")

    def tearDown(self):
        frappe.db.rollback(save_point=self.sp)

    def _machine(self, store=None, modes="CARD,UPI", enabled=1):
        doc = frappe.new_doc("CH Payment Machine")
        doc.update({
            "machine_name": "ZZ Fallback Test Terminal",
            "machine_id": "ZZ-FB-1",
            "provider": "Other",
            "company": self.company,
            "store": store or self.store,
            "enabled": enabled,
            "supported_payment_modes": modes,
            "terminal_id": "T-FB-0001",
        })
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.insert(ignore_permissions=True)
        return doc

    def test_the_answer_always_says_which_way_to_take_the_payment(self):
        """manual_only must never be absent: the client reads it as falsy."""
        res = pg.get_payment_machines(company=self.company, store=self.store)
        self.assertIn("manual_only", res)

    def test_no_terminal_means_key_the_reference(self):
        frappe.db.sql("DELETE FROM `tabCH Payment Machine`")
        res = pg.get_payment_machines(company=self.company, store=self.store)
        self.assertEqual(res["machines"], [])
        self.assertTrue(res["manual_only"], "a till with no terminal could not take a card")

    def test_a_terminal_at_this_store_is_used(self):
        frappe.db.sql("DELETE FROM `tabCH Payment Machine`")
        self._machine()
        res = pg.get_payment_machines(company=self.company, store=self.store)
        self.assertEqual(len(res["machines"]), 1)
        self.assertFalse(res["manual_only"], "a configured terminal must be used, not bypassed")

    def test_a_terminal_at_another_store_does_not_count(self):
        """It is no more use to this counter than no terminal at all."""
        other = frappe.db.get_value(
            "CH Store", {"company": self.company, "name": ("!=", self.store)}, "name")
        if not other:
            raise unittest.SkipTest("only one store on this company")
        frappe.db.sql("DELETE FROM `tabCH Payment Machine`")
        self._machine(store=other)
        res = pg.get_payment_machines(company=self.company, store=self.store)
        self.assertEqual(res["machines"], [])
        self.assertTrue(res["manual_only"])

    def test_a_disabled_terminal_does_not_count(self):
        """Unplugged is the same as absent, and the till must not stall on it."""
        frappe.db.sql("DELETE FROM `tabCH Payment Machine`")
        self._machine(enabled=0)
        res = pg.get_payment_machines(company=self.company, store=self.store)
        self.assertEqual(res["machines"], [])
        self.assertTrue(res["manual_only"])

    def test_the_pilot_flag_still_forces_manual(self):
        """Shadow-live unplugs the terminals deliberately; that must still win."""
        frappe.db.sql("DELETE FROM `tabCH Payment Machine`")
        self._machine()
        import ch_item_master.ch_core.shadow_live as shadow
        original = shadow.manual_payment_entry
        pg_shadow = getattr(pg, "manual_payment_entry", None)
        try:
            shadow.manual_payment_entry = lambda: True
            res = pg.get_payment_machines(company=self.company, store=self.store)
            self.assertTrue(res["manual_only"])
        finally:
            shadow.manual_payment_entry = original
            if pg_shadow is not None:
                pg.manual_payment_entry = pg_shadow
