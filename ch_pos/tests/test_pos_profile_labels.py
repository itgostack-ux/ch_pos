"""The till picker names the shop, not the profile code.

`POS - STO-GSPL-CHENNA-0005` tells the person at the counter nothing, and on
this estate the codes are sequential — two adjacent shops differ by one digit,
which is exactly the kind of list people mis-pick from.

`get_pos_profiles` now carries the store and a display label. These tests pin
the label, and pin that adding it did not widen what the list returns: the
scoped set is the security boundary, and an annotation step that quietly
resolved extra rows would be a leak.
"""

import unittest

import frappe

from ch_pos.api.token_api import get_pos_profiles
from ch_pos.config import is_privileged_user


def _plain_executive():
    rows = frappe.db.sql(
        """
        SELECT e.user, e.store
          FROM `tabPOS Executive` e
          JOIN `tabUser` u ON u.name = e.user AND u.enabled = 1
         WHERE e.is_active = 1
         LIMIT 200
        """,
        as_dict=True)
    for row in rows:
        if not is_privileged_user(row.user):
            return row
    return None


class TestProfileLabels(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_every_profile_carries_a_label(self):
        rows = get_pos_profiles()
        self.assertTrue(rows, "Administrator should see profiles")
        for row in rows:
            self.assertIn("label", row)
            self.assertTrue(row["label"], f"{row['name']} has an empty label")

    def test_the_label_names_the_shop_and_keeps_the_code(self):
        mapped = [r for r in get_pos_profiles() if r.get("store")]
        if not mapped:
            self.skipTest("no POS Profile is mapped to a CH Store here")
        row = mapped[0]
        self.assertIn(row["store"], row["label"],
                      "the store code must survive — every other screen shows it")
        self.assertNotEqual(row["label"], row["name"])

    def test_an_unmapped_profile_falls_back_to_its_own_name(self):
        """A till with no CH Store must stay selectable, not vanish."""
        rows = get_pos_profiles()
        for row in rows:
            if not row.get("store"):
                self.assertEqual(row["label"], row["name"])

    def test_labelling_did_not_widen_the_scoped_list(self):
        """The annotation joins CH Store — it must not add rows."""
        subject = _plain_executive()
        if not subject:
            self.skipTest("no non-privileged POS Executive on this site")
        original = frappe.session.user
        try:
            frappe.set_user(subject.user)
            scoped = get_pos_profiles()
        finally:
            frappe.set_user(original)
        everything = get_pos_profiles()  # as Administrator
        self.assertLess(len(scoped), len(everything),
                        "a scoped user must not see the whole estate")
        names = {r["name"] for r in everything}
        self.assertTrue({r["name"] for r in scoped}.issubset(names))
