"""A cashier with no till is told so, rather than handed an empty picker.

`get_pos_context` answers `no_allocation` for a user with no active POS
Executive record. `SessionOpeningScreen.show()` branched on `select_store`,
`day_closed` and `existing_session` but never on this one, so the status fell
through to the final `else` and opened the till dialog: a required POS Profile
control offering nothing, with no word of why. On this site 35 of 108
non-bypass scoped users answer `no_allocation`, so it was not a corner.

These pin the server half of that contract — the status and a message worth
showing — because the dialog is only ever as good as what it is told, and a
status renamed here would silently restore the empty picker.
"""

import unittest

import frappe

from ch_pos.api.isolation_api import get_pos_context
from ch_pos.api.token_api import get_pos_profiles


def _user_without_allocation():
    """An enabled, non-bypass user holding no active POS Executive row."""
    rows = frappe.db.sql(
        """
        SELECT u.name
          FROM `tabUser` u
          LEFT JOIN `tabPOS Executive` e
                 ON e.user = u.name AND e.is_active = 1
         WHERE u.enabled = 1
           AND u.name NOT IN ('Administrator', 'Guest')
           AND e.name IS NULL
         LIMIT 300
        """,
        as_dict=True)
    from ch_pos.config import is_privileged_user
    for row in rows:
        if is_privileged_user(row.name):
            continue
        # get_pos_context opens with a Sales Invoice read check; a user who
        # cannot clear it never reaches the status under test.
        if frappe.has_permission("Sales Invoice", "read", user=row.name):
            return row.name
    return None


class TestNoAllocationIsAnAnswer(unittest.TestCase):
    def setUp(self):
        self.user = _user_without_allocation()
        if not self.user:
            self.skipTest("no unallocated non-bypass user on this site")

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback()

    def test_context_reports_no_allocation(self):
        frappe.set_user(self.user)
        self.assertEqual(get_pos_context().get("status"), "no_allocation")

    def test_no_allocation_carries_a_message_to_show(self):
        """The dialog shows ctx.message verbatim, so it must not be blank."""
        frappe.set_user(self.user)
        message = get_pos_context().get("message")
        self.assertTrue(message and message.strip(),
                        "no_allocation must explain itself — the UI has nothing else to say")

    def test_status_decides_not_the_length_of_the_till_list(self):
        """Why the dialog must branch on the status, and never on the list.

        `get_pos_context` reads POS Executive; `get_pos_profiles` reads CH User
        Scope. They disagree — measured on this site, 35 users answering
        `no_allocation` are still offered between 17 and 41 tills — so "did the
        list come back empty" is not a test for "may this person open a till".
        The status is, which is why show() has to consult it first. Pinned so
        that a future reader does not swap the check for a length test.
        """
        frappe.set_user(self.user)
        ctx = get_pos_context()
        self.assertEqual(ctx.get("status"), "no_allocation")
        # Deliberately asserts nothing about len(): either answer is possible
        # today, and both must lead to the same refusal.
        self.assertIsInstance(get_pos_profiles(), list)
