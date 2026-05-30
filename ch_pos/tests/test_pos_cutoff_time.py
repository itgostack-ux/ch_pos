"""B2 — POS daily cut-off time guard tests.

    bench --site erpnext.local execute \
        ch_pos.tests.test_pos_cutoff_time.run
"""

from __future__ import annotations

import datetime as _dt

import frappe

from ch_pos.overrides import cutoff_time_guard as guard


class _StubDoc:
	def __init__(self, pos_profile="TEST-PROFILE", posting_date=None, docstatus=0, name=None):
		self.doctype = "Sales Invoice"
		self.pos_profile = pos_profile
		self.posting_date = posting_date or frappe.utils.nowdate()
		self.docstatus = docstatus
		self.name = name

	def is_new(self):
		return self.name is None


def _patch_profile(cutoff, role=None):
	def fake_gv(doctype, name, fields, as_dict=False):
		if doctype == "POS Profile" and as_dict:
			return {"ch_cutoff_time": cutoff, "ch_cutoff_override_role": role}
		return None

	guard.frappe.db.get_value = fake_gv


def _patch_now(hour, minute=0):
	target = _dt.datetime.combine(frappe.utils.getdate(), _dt.time(hour, minute))

	def fake_now():
		return target

	guard.now_datetime = fake_now


def _patch_roles(roles):
	def fake_get_roles(user=None):
		return list(roles)

	guard.frappe.get_roles = fake_get_roles


def _expect_throw(doc, label):
	try:
		guard.validate_pos_cutoff_time(doc)
	except frappe.ValidationError:
		print(f"✅  {label}  →  blocked")
		return True
	print(f"❌  {label}  →  NOT blocked")
	return False


def _expect_pass(doc, label):
	try:
		guard.validate_pos_cutoff_time(doc)
	except frappe.ValidationError as exc:
		print(f"❌  {label}  →  unexpectedly blocked: {exc}")
		return False
	print(f"✅  {label}  →  passed")
	return True


def run():
	frappe.set_user("Administrator")
	orig_gv = frappe.db.get_value
	orig_now = guard.now_datetime
	orig_roles = frappe.get_roles
	results = []
	try:
		# T1: no pos_profile → no-op
		results.append(_expect_pass(_StubDoc(pos_profile=None), "T1: no profile"))

		# T2: cutoff blank → no-op
		_patch_profile(None)
		_patch_now(23, 0)
		_patch_roles(["Sales User"])
		results.append(_expect_pass(_StubDoc(), "T2: cutoff blank"))

		# T3: before cutoff → pass
		_patch_profile("21:00:00")
		_patch_now(20, 0)
		_patch_roles(["Sales User"])
		results.append(_expect_pass(_StubDoc(), "T3: before cutoff"))

		# T4: after cutoff, no override → throw
		_patch_profile("21:00:00")
		_patch_now(22, 30)
		_patch_roles(["Sales User"])
		results.append(_expect_throw(_StubDoc(), "T4: after cutoff no role"))

		# T5: after cutoff, default override role POS Manager → pass
		_patch_profile("21:00:00", role=None)  # default POS Manager
		_patch_now(22, 30)
		_patch_roles(["POS Manager", "Sales User"])
		results.append(_expect_pass(_StubDoc(), "T5: POS Manager bypass"))

		# T6: after cutoff, System Manager always bypass
		_patch_profile("21:00:00")
		_patch_now(23, 59)
		_patch_roles(["System Manager"])
		results.append(_expect_pass(_StubDoc(), "T6: System Manager bypass"))

		# T7: after cutoff, custom override role
		_patch_profile("21:00:00", role="Store Supervisor")
		_patch_now(22, 0)
		_patch_roles(["Store Supervisor"])
		results.append(_expect_pass(_StubDoc(), "T7: custom override role"))

		# T8: back-dated invoice → out of scope
		_patch_profile("21:00:00")
		_patch_now(23, 0)
		_patch_roles(["Sales User"])
		yesterday = frappe.utils.add_days(frappe.utils.nowdate(), -1)
		results.append(_expect_pass(_StubDoc(posting_date=yesterday), "T8: back-dated"))

		# T9: bypass flag honoured
		_patch_profile("21:00:00")
		_patch_now(23, 0)
		_patch_roles(["Sales User"])
		frappe.flags.ignore_pos_cutoff = True
		try:
			results.append(_expect_pass(_StubDoc(), "T9: bypass flag"))
		finally:
			frappe.flags.ignore_pos_cutoff = False

		# T10: timedelta cutoff value (MariaDB returns Time columns as timedelta) → throw
		_patch_profile(_dt.timedelta(hours=21))
		_patch_now(22, 0)
		_patch_roles(["Sales User"])
		results.append(_expect_throw(_StubDoc(), "T10: timedelta cutoff"))

	finally:
		frappe.db.get_value = orig_gv
		guard.frappe.db.get_value = orig_gv
		guard.now_datetime = orig_now
		frappe.get_roles = orig_roles
		guard.frappe.get_roles = orig_roles

	passed = sum(1 for r in results if r)
	total = len(results)
	print(f"\n— POS cut-off guard: {passed}/{total} cases passed —")
	if passed != total:
		raise frappe.ValidationError(f"{total - passed} test case(s) failed")
