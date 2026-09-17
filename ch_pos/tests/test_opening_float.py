"""A till may open with an empty drawer; it may not open without a count.

`if not opening_cash` treated 0 as "nothing entered", so a till that
legitimately starts empty — a repair-only counter, a lane opened mid-day, a
store whose float is issued later — could not be opened at all. It blocked a
verification run of the ordinary retail cycle.

The control is that the cashier DECLARES the float, not that it is non-zero.
SAP, Oracle Xstore and Odoo all record a declared opening float and accept
zero; none of them accept a blank. A wrong declaration is caught by the closing
variance, which is what that reconciliation is for.

The check has to run before `flt()`: flt(None) is 0.0, and after that
"nothing entered" and "the drawer is empty" are the same value.
"""

import inspect
import unittest

from ch_pos.api.session_api import open_session


class TestOpeningFloatContract(unittest.TestCase):
    def test_zero_is_not_treated_as_missing(self):
        src = inspect.getsource(open_session)
        self.assertNotIn("if not opening_cash:", src,
                         "0 is falsy — this rejects a legitimately empty drawer")

    def test_a_blank_is_refused(self):
        src = inspect.getsource(open_session)
        self.assertIn('opening_cash is None or str(opening_cash).strip() == ""', src)

    def test_the_check_runs_before_the_coercion(self):
        """flt(None) is 0.0, so after it the two cases are indistinguishable."""
        src = inspect.getsource(open_session)
        self.assertLess(src.index("opening_cash is None"),
                        src.index("opening_cash = flt(opening_cash)"))

    def test_negative_is_refused(self):
        src = inspect.getsource(open_session)
        self.assertIn("Opening cash cannot be negative", src)

    def test_the_prompt_tells_the_cashier_zero_is_allowed(self):
        import pathlib

        import frappe

        js = pathlib.Path(frappe.get_app_path(
            "ch_pos", "public", "js", "pos_app", "shared",
            "session_opening_screen.js")).read_text()
        self.assertEqual(js.count("0 if it starts empty"), 2,
                         "both opening dialogs should say so")
