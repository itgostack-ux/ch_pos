# Copyright (c) 2026, GoStack and contributors
"""E2E check — CH Gift Redemption history capture (trigger item/IMEI at
issuance, store/POS profile/cashier at redemption).

Run inside `bench --site erpnext.local console`:

    from ch_pos._e2e_gift_history import run
    run()

Single transaction, rolled back at the end — nothing committed, no
notifications leave the box (enqueue_after_commit never fires).
"""

import frappe
from frappe.utils import add_to_date, now_datetime


def _find_invoice_with_serial():
	"""Prefer a submitted SI whose line carries a serial; fall back to any."""
	row = frappe.db.sql(
		"""
		SELECT si.name
		  FROM `tabSales Invoice` si
		  JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		 WHERE si.docstatus = 1
		   AND IFNULL(sii.serial_no, '') != ''
		 ORDER BY si.creation DESC
		 LIMIT 1
		""",
		pluck=True,
	)
	if row:
		return row[0], True
	row = frappe.db.sql(
		"""
		SELECT si.name
		  FROM `tabSales Invoice` si
		  JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		 WHERE si.docstatus = 1
		 ORDER BY si.creation DESC
		 LIMIT 1
		""",
		pluck=True,
	)
	return (row[0], False) if row else (None, False)


def run():
	from ch_pos.api import gift_redemption as gr

	passed, failed = [], []

	def check(label, ok, detail=""):
		(passed if ok else failed).append(label)
		print(f"{'PASS' if ok else 'FAIL'} — {label}{(' :: ' + str(detail)) if detail and not ok else ''}")

	frappe.db.savepoint("e2e_gift_history")
	orig_create = gr._create_gift_invoice
	try:
		si_name, has_serial = _find_invoice_with_serial()
		if not si_name:
			raise RuntimeError("No submitted Sales Invoice on this site")
		si = frappe.get_doc("Sales Invoice", si_name)
		trigger_row = next(
			(r for r in si.items if has_serial and (r.serial_no or "").strip()),
			si.items[0],
		)
		trigger = trigger_row.item_code
		reward = frappe.get_all(
			"Item",
			filters={"disabled": 0, "has_variants": 0, "name": ("!=", trigger)},
			limit=1, pluck="name",
		)[0]
		print(f"Invoice {si.name} (serialized={has_serial}), trigger={trigger}\n")

		offer = frappe.get_doc({
			"doctype": "CH Item Offer",
			"company": si.company,
			"offer_name": f"E2E GiftHistory {frappe.generate_hash(length=6)}",
			"offer_type": "Freebie",
			"value_type": "Amount",
			"value": 0,
			"gift_delivery": "Spin Wheel",
			"trigger_item": trigger,
			"reward_item": reward,
			"reward_qty": 1,
			"start_date": add_to_date(now_datetime(), days=-1),
			"end_date": add_to_date(now_datetime(), days=7),
		})
		offer.flags.ignore_permissions = True
		offer.insert()
		offer.approve()

		# --- Issuance: trigger item + serial captured ---
		gift_name = gr.issue_gift_for_invoice(si)
		check("gift issued for invoice", bool(gift_name), gift_name)
		gift = frappe.get_doc("CH Gift Redemption", gift_name)
		check("history: parent invoice stored", gift.parent_sales_invoice == si.name)
		check("history: trigger_item captured", gift.trigger_item == trigger, gift.trigger_item)
		check("history: trigger_item_name captured", bool(gift.trigger_item_name))
		expected_serials = "\n".join(
			dict.fromkeys(
				s.strip()
				for r in si.items if r.item_code == trigger
				for s in (r.serial_no or "").replace(",", "\n").split("\n")
				if s.strip()
			)
		)
		check(
			f"history: trigger serial/IMEI captured (serialized={has_serial})",
			(gift.trigger_serial_no or "") == expected_serials,
			f"got={gift.trigger_serial_no!r} want={expected_serials!r}",
		)
		check("history: issued_at + expires_at set", bool(gift.issued_at and gift.expires_at))

		# --- Redemption: store / profile / cashier captured ---
		profile = frappe.get_all(
			"POS Profile", filters={"disabled": 0, "company": si.company},
			limit=1, pluck="name",
		) or frappe.get_all("POS Profile", filters={"disabled": 0}, limit=1, pluck="name")
		if not profile:
			raise RuntimeError("No POS Profile on this site")
		profile = profile[0]

		gift.db_set("status", "Revealed", update_modified=False)
		gr._create_gift_invoice = lambda g, p: "E2E-FAKE-INV"  # skip stock/GL
		result = gr.redeem_gift_code(gift.redemption_code, profile)
		gift.reload()

		check("redeem: status Redeemed + invoice linked",
			gift.status == "Redeemed" and gift.redeemed_invoice == "E2E-FAKE-INV")
		check("redeem: pos profile captured", gift.redeemed_pos_profile == profile,
			gift.redeemed_pos_profile)
		check("redeem: cashier captured", gift.redeemed_by == frappe.session.user,
			gift.redeemed_by)
		ext_store = frappe.db.get_value(
			"POS Profile Extension", {"pos_profile": profile}, "store"
		)
		check("redeem: store captured (when profile has one)",
			(gift.redeemed_store or None) == (ext_store or None),
			f"got={gift.redeemed_store} ext={ext_store}")
		check("redeem: redeemed_at set", bool(gift.redeemed_at))

		# --- Cashier lookup exposes the history ---
		info = gr.lookup_gift_code(gift.redemption_code)
		check("lookup: returns trigger + store + issued_at",
			info.get("trigger_item") == trigger and "store" in info and info.get("issued_at"))

	finally:
		gr._create_gift_invoice = orig_create
		frappe.db.rollback(save_point="e2e_gift_history")

	print(f"\n{len(passed)} passed, {len(failed)} failed")
	if failed:
		print("FAILED:", failed)
	return not failed
