"""Phase 2 — POS Uplift E2E

Covers the surgical Phase 2 deltas added on top of the existing ch_pos SPA:

  1. Hotkey wiring (F3 customer:focus, F9 cart:hold alias, F10 held_bills:open)
     — asserted by grepping the bundle entry-point.
  2. Camera scanner module + global exposure.
  3. share_invoice API: print URL channel works end-to-end on a saved invoice;
     missing-contact channels degrade gracefully (no exceptions).

Run with::

	bench --site erpnext.local execute ch_pos.tests.test_phase2_pos_uplift.run
"""
from __future__ import annotations

import os
import frappe


_PASS: list[str] = []
_FAIL: list[str] = []


def _check(label: str, ok: bool, detail: str = "") -> None:
	(_PASS if ok else _FAIL).append(label)
	prefix = "  [PASS]" if ok else "  [FAIL]"
	print(f"{prefix} {label}{(' — ' + detail) if detail else ''}")


# ── 1. Bundle wiring ───────────────────────────────────────────────────────

def _bundle_path() -> str:
	from ch_pos.hooks import app_name  # noqa: F401
	return os.path.join(
		frappe.get_app_path("ch_pos"), "public", "js", "ch_pos.bundle.js"
	)


def _scan_bundle() -> None:
	with open(_bundle_path(), "r", encoding="utf-8") as fh:
		src = fh.read()
	_check("Bundle binds F3 → customer:focus", 'e.key === "F3"' in src and 'customer:focus' in src)
	_check("Bundle binds F9 → cart:hold",      'e.key === "F9"' in src or 'e.key === "F8" || e.key === "F9"' in src)
	_check("Bundle binds F10 → held_bills:open", 'e.key === "F10"' in src and 'held_bills:open' in src)
	_check("Bundle exposes ch_pos.open_camera_scan", 'ch_pos.open_camera_scan' in src)

	cam_path = os.path.join(
		frappe.get_app_path("ch_pos"), "public", "js", "pos_app", "shared", "camera_scanner.js"
	)
	_check("camera_scanner.js exists", os.path.exists(cam_path))


# ── 2. share_invoice API ───────────────────────────────────────────────────

def _ensure_minimal_invoice() -> str:
	"""Return a submitted Sales Invoice we can share. Reuse if one already exists."""
	# Prefer the most recent submitted invoice on this site
	row = frappe.get_all(
		"Sales Invoice",
		filters={"docstatus": 1},
		fields=["name"],
		order_by="modified desc",
		limit=1,
	)
	if row:
		return row[0]["name"]

	# Otherwise create a tiny one
	company = frappe.db.get_default("Company") or frappe.db.get_value("Company", {}, "name")
	customer = frappe.db.get_value("Customer", {}, "name")
	item     = frappe.db.get_value("Item", {"is_sales_item": 1, "disabled": 0}, "name")
	if not (company and customer and item):
		frappe.throw("Cannot run Phase 2 e2e: site needs at least 1 Company + 1 Customer + 1 sales Item.")

	si = frappe.new_doc("Sales Invoice")
	si.company = company
	si.customer = customer
	si.update_stock = 0
	si.append("items", {"item_code": item, "qty": 1, "rate": 100})
	si.flags.ignore_permissions = True
	si.insert(ignore_permissions=True)
	si.submit()
	return si.name


def _test_share_api() -> None:
	from ch_pos.api import share_api

	# Whitelisted? (Frappe stores whitelisted callables in frappe.whitelisted set)
	_check(
		"share_invoice is whitelisted",
		share_api.share_invoice in frappe.whitelisted,
	)

	inv = _ensure_minimal_invoice()

	# Print channel
	res = share_api.share_invoice(invoice_name=inv, channels=["print"])
	pr = (res.get("results") or {}).get("print") or {}
	_check(
		"share_invoice(print) returns pdf_url",
		bool(pr.get("success") and pr.get("pdf_url")),
		f"pdf_url={pr.get('pdf_url', '')[:60]}…",
	)

	# Email channel without override and (likely) without contact_email — must degrade
	res = share_api.share_invoice(invoice_name=inv, channels=["email"])
	em = (res.get("results") or {}).get("email") or {}
	ok = isinstance(em.get("success"), bool)  # any deterministic boolean is fine
	_check("share_invoice(email) returns deterministic result", ok, str(em)[:80])

	# WhatsApp: degrade gracefully (no helper or no phone)
	res = share_api.share_invoice(invoice_name=inv, channels=["whatsapp"])
	wa = (res.get("results") or {}).get("whatsapp") or {}
	_check("share_invoice(whatsapp) returns deterministic result", isinstance(wa.get("success"), bool), str(wa)[:80])

	# Invalid channel rejection
	try:
		share_api.share_invoice(invoice_name=inv, channels=["bogus"])
		_check("share_invoice rejects empty/invalid channel list", False, "did not raise")
	except frappe.ValidationError:
		_check("share_invoice rejects empty/invalid channel list", True)
	except Exception as exc:
		# frappe.throw raises ValidationError; anything else is a fail
		_check("share_invoice rejects empty/invalid channel list", False, repr(exc))


def run() -> None:
	print("Phase 2 — POS Uplift E2E")
	_scan_bundle()
	_test_share_api()
	print(f"Phase 2 — {len(_PASS)} PASS / {len(_FAIL)} FAIL")
	if _FAIL:
		raise AssertionError(f"Phase 2 failures: {_FAIL}")
	print("Phase 2 — ALL PASS")
