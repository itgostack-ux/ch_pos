"""Billed By is mandatory on every money-moving POS document.

A till is shared across a shift, so the browser login names a machine's
session, not a person. `sales_executive` ("Billed By") is what names the human,
and it drives the incentive the sale pays out on — so it cannot be optional,
and it cannot silently fall back to whoever is signed in.

Deliberately not bypassable by a privileged user: the store/company scope
bypass lets an admin work across stores, but nobody at any privilege level gets
to leave a sale unattributed. Odoo enforces the same rule on `employee_id` once
employee mode is on, and there is no admin escape hatch there either.
"""

import ast
import inspect
import textwrap
import unittest
from unittest.mock import patch

import frappe

from ch_pos.api import pos_api, repair
from ch_pos.pos_core.doctype.pos_executive.pos_executive import (
    assert_valid_sales_executive,
)

#: Every server entry point that moves money or takes custody of a device, and
#: therefore has to name the person responsible. Repair billing
#: (`collect_repair_payment`, `close_repair_order`) carried NO attribution field
#: at all before this — worse than the optional-parameter case, because there
#: was nothing for a client to send.
ATTRIBUTED_ENDPOINTS = (
    (pos_api, "create_pos_invoice"),
    (pos_api, "create_pos_return"),
    (pos_api, "collect_repair_payment"),
    (pos_api, "close_repair_order"),
    (repair, "create_service_intake_from_pos"),
)


class TestBilledByGuard(unittest.TestCase):
    """The guard itself."""

    def test_missing_executive_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            assert_valid_sales_executive(None, store="ST-1", company="Co")

    def test_empty_string_is_refused(self):
        with self.assertRaises(frappe.ValidationError):
            assert_valid_sales_executive("", store="ST-1", company="Co")

    def test_custom_message_reaches_the_counter(self):
        """Device intake is not billing — the person reading the error is told
        what they actually left out."""
        with self.assertRaises(frappe.ValidationError):
            assert_valid_sales_executive(None, msg="Say who took the device in")
        self.assertIn(
            "Say who took the device in",
            frappe.get_message_log()[-1].get("message", ""),
        )

    def test_active_executive_at_the_store_passes(self):
        with patch.object(frappe.db, "exists", return_value="PEX-ST-1-0001") as exists:
            assert_valid_sales_executive("PEX-ST-1-0001", store="ST-1", company="Co")

        filters = exists.call_args.args[1]
        self.assertEqual(filters["name"], "PEX-ST-1-0001")
        self.assertEqual(filters["store"], "ST-1")
        self.assertEqual(filters["company"], "Co")
        self.assertEqual(filters["is_active"], 1)

    def test_executive_from_another_store_is_refused(self):
        with (
            patch.object(frappe.db, "exists", return_value=None),
            self.assertRaises(frappe.ValidationError),
        ):
            assert_valid_sales_executive("PEX-OTHER-0001", store="ST-1", company="Co")

    def test_unmapped_profile_still_checks_company_and_active(self):
        """A POS Profile with no CH Store is a master-data gap. It must not take
        the till down — but company + active still has to hold, so the store
        clause is dropped rather than the whole check."""
        with patch.object(frappe.db, "exists", return_value="PEX-ST-1-0001") as exists:
            assert_valid_sales_executive("PEX-ST-1-0001", store=None, company="Co")

        filters = exists.call_args.args[1]
        self.assertNotIn("store", filters)
        self.assertEqual(filters["company"], "Co")
        self.assertEqual(filters["is_active"], 1)


class TestBilledByEnforcedAtEveryMoneyPath(unittest.TestCase):
    """Pins the enforcement, so the optional-parameter pattern cannot creep back."""

    def test_every_money_path_calls_the_guard(self):
        for module, endpoint in ATTRIBUTED_ENDPOINTS:
            source = inspect.getsource(getattr(module, endpoint))
            self.assertIn(
                "assert_valid_sales_executive",
                source,
                f"{endpoint} no longer requires a Billed By executive",
            )

    def test_repair_billing_accepts_an_executive(self):
        """These two built a Sales Invoice with no attribution parameter at all."""
        for endpoint in ("collect_repair_payment", "close_repair_order"):
            signature = inspect.signature(getattr(pos_api, endpoint))
            self.assertIn(
                "sales_executive",
                signature.parameters,
                f"{endpoint} has no sales_executive parameter to attribute with",
            )

    def test_guard_is_not_privilege_bypassable(self):
        """A scope bypass is legitimate; an attribution bypass is not.

        Parsed rather than string-matched, so the docstring is free to name the
        very function it must never call.
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(assert_valid_sales_executive)))
        called = {
            node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        self.assertNotIn(
            "is_privileged_user",
            called,
            "Billed By must be required at every privilege level",
        )
