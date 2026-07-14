# Copyright (c) 2026, GoStack and contributors
"""E2E check — FIFO enforcement must agree with sellable-bin gating.

Bug (2026-07-14): a serial parked in the Buyback bin was excluded from the
POS serial picker but still counted as the "oldest" serial by the FIFO
guard, so every legitimate pick failed with "FIFO restricted. Please
select oldest serial: <unpickable serial>". The FIFO baseline now applies
the same four gates as the picker (Active + warehouse, Sellable bin tag,
no open pre-booking reservation, not an exchange new device), and manual
IMEI entry rejects non-Sellable bins outright.

Run inside `bench --site erpnext.local console`:

    from ch_pos._e2e_fifo_bin_gating import run
    run()

Single transaction, rolled back — nothing committed.
"""

import frappe
from frappe.utils import add_days, nowdate


def _tag_bin(serial, item, warehouse, bin_type):
	existing = frappe.db.get_value("CH Stock Bin", {"serial_no": serial}, "name")
	if existing:
		frappe.db.set_value("CH Stock Bin", existing, "bin_type", bin_type)
	else:
		frappe.get_doc({
			"doctype": "CH Stock Bin",
			"serial_no": serial,
			"item_code": item,
			"warehouse": warehouse,
			"bin_type": bin_type,
			"reason": "e2e fifo bin gating",
		}).insert(ignore_permissions=True)


def _make_item(prefix):
	# Borrow governance-mandated master data (HSN, sub-category, group…)
	# from an existing Active serialized item instead of re-deriving the
	# whole item-completeness rulebook here.
	donor_name = frappe.get_all(
		"Item",
		filters={"disabled": 0, "has_serial_no": 1, "has_variants": 0,
			"is_stock_item": 1, "ch_lifecycle_status": "Active"},
		limit=1, pluck="name",
	)[0]
	donor = frappe.get_doc("Item", donor_name)

	item = frappe.get_doc({
		"doctype": "Item",
		"item_code": f"{prefix}-{frappe.generate_hash(length=6).upper()}",
		"item_name": "E2E FIFO Test Phone",
		"item_group": donor.item_group,
		"stock_uom": donor.stock_uom or "Nos",
		"is_stock_item": 1,
		"has_serial_no": 1,
		"is_sales_item": 1,
	})
	for fld in ("ch_lifecycle_status", "ch_serial_kind", "ch_item_mrp",
			"gst_hsn_code", "ch_sub_category", "ch_category", "brand",
			"ch_item_nature", "ch_plm_status"):
		if item.meta.has_field(fld) and donor.get(fld):
			item.set(fld, donor.get(fld))
	if item.meta.has_field("ch_lifecycle_status"):
		item.ch_lifecycle_status = "Active"
	if item.meta.has_field("ch_item_mrp") and not item.get("ch_item_mrp"):
		item.ch_item_mrp = 60000
	if item.meta.has_field("ch_plm_status"):
		item.ch_plm_status = "Active Production"
	item.flags.ignore_permissions = True
	item.insert()
	return item.name


def _receive_serial(item, warehouse, company, serial, days_ago):
	se = frappe.new_doc("Stock Entry")
	se.stock_entry_type = "Material Receipt"
	se.purpose = "Material Receipt"
	se.company = company
	se.set_posting_time = 1
	se.posting_date = add_days(nowdate(), -days_ago)
	se.append("items", {
		"item_code": item,
		"qty": 1,
		"t_warehouse": warehouse,
		"basic_rate": 1000,
		"use_serial_batch_fields": 1,
		"serial_no": serial,
	})
	se.flags.ignore_permissions = True
	se.flags.ignore_procurement_guardrails = True
	se.insert()
	se.submit()
	return se.name


def run():
	from ch_pos.api import pos_api
	from ch_pos.api.search import get_available_serials

	passed, failed = [], []

	def check(label, ok, detail=""):
		(passed if ok else failed).append(label)
		print(f"{'PASS' if ok else 'FAIL'} — {label}{(' :: ' + str(detail)) if detail and not ok else ''}")

	frappe.db.savepoint("e2e_fifo_bin")
	orig_reserved = pos_api._get_open_reserved_sales_order_for_serial
	try:
		profile = frappe.get_all(
			"POS Profile", filters={"disabled": 0},
			fields=["name", "warehouse", "company"], limit=1,
		)[0]
		wh, company = profile.warehouse, profile.company
		item = _make_item("EFIFO")
		s1, s2, s3 = (f"EFIFO-SN{i}-{frappe.generate_hash(length=4).upper()}" for i in (1, 2, 3))
		for serial, age in ((s1, 3), (s2, 2), (s3, 1)):
			_receive_serial(item, wh, company, serial, age)
		print(f"item={item} wh={wh} serials: s1={s1}(D-3) s2={s2}(D-2) s3={s3}(D-1)\n")

		# ── All sellable: baseline behaviour ─────────────────────────────
		leader, _d = pos_api._get_oldest_fifo_serial(item, wh)
		check("P1 all-sellable: FIFO leader is oldest serial s1", leader == s1, leader)
		check("P2 validate oldest s1 → valid",
			pos_api.validate_serial_for_sale(s1, item, wh).get("valid") is True)
		res = pos_api.validate_serial_for_sale(s2, item, wh)
		check("N1 validate newer s2 → FIFO violation citing s1",
			res.get("fifo_violation") and res.get("oldest_serial") == s1, res)
		rows = get_available_serials(item, wh)
		check("P3 picker lists all 3, s1 flagged Sell First",
			[r["serial_no"] for r in rows] == [s1, s2, s3] and rows[0]["is_oldest"] == 1,
			[r["serial_no"] for r in rows])

		# ── s1 bought back → Buyback bin (the reported scenario) ─────────
		_tag_bin(s1, item, wh, "Buyback")
		rows = get_available_serials(item, wh)
		check("P4 picker excludes buyback-binned s1",
			[r["serial_no"] for r in rows] == [s2, s3], [r["serial_no"] for r in rows])
		leader, _d = pos_api._get_oldest_fifo_serial(item, wh)
		check("P5 FIFO leader skips buyback bin → s2 (the bug)", leader == s2, leader)
		check("P6 validate s2 → valid (was: FIFO restricted by s1)",
			pos_api.validate_serial_for_sale(s2, item, wh).get("valid") is True,
			pos_api.validate_serial_for_sale(s2, item, wh))
		res = pos_api.validate_serial_for_sale(s3, item, wh)
		check("N2 validate s3 → violation cites s2, not buyback s1",
			res.get("fifo_violation") and res.get("oldest_serial") == s2, res)
		res = pos_api.validate_serial_for_sale(s1, item, wh)
		check("N3 manual entry of buyback s1 → blocked with bin reason",
			res.get("valid") is False and "Buyback" in (res.get("reason") or ""), res)

		# ── s2 held for exchange → Reserved bin ──────────────────────────
		_tag_bin(s2, item, wh, "Reserved")
		leader, _d = pos_api._get_oldest_fifo_serial(item, wh)
		check("P7 FIFO leader skips Reserved too → s3", leader == s3, leader)
		check("P8 validate s3 → valid",
			pos_api.validate_serial_for_sale(s3, item, wh).get("valid") is True)
		res = pos_api.validate_serial_for_sale(s2, item, wh)
		check("N4 manual entry of Reserved s2 → blocked",
			res.get("valid") is False and "Reserved" in (res.get("reason") or ""), res)

		# ── nothing sellable left ─────────────────────────────────────────
		_tag_bin(s3, item, wh, "Damaged")
		leader, _d = pos_api._get_oldest_fifo_serial(item, wh)
		check("N5 no sellable serials → FIFO leader is None", leader is None, leader)
		check("N5b picker empty", get_available_serials(item, wh) == [])

		# ── buyback unit re-onboarded → sellable again ────────────────────
		for serial in (s1, s2, s3):
			_tag_bin(serial, item, wh, "Sellable")
		leader, _d = pos_api._get_oldest_fifo_serial(item, wh)
		check("P9 re-onboarded: leader back to s1", leader == s1, leader)

		# ── open pre-booking reservation excluded from baseline ──────────
		pos_api._get_open_reserved_sales_order_for_serial = (
			lambda serial_no, warehouse=None: "SO-E2E-HOLD" if serial_no == s1 else None
		)
		leader, _d = pos_api._get_oldest_fifo_serial(item, wh)
		check("P10 prebooked s1 skipped → leader s2", leader == s2, leader)
		res = pos_api.validate_serial_for_sale(s1, item, wh)
		check("N6 validate prebooked s1 → reserved block",
			res.get("reserved") and res.get("reserved_so") == "SO-E2E-HOLD", res)

	finally:
		pos_api._get_open_reserved_sales_order_for_serial = orig_reserved
		frappe.db.rollback(save_point="e2e_fifo_bin")

	print(f"\n{len(passed)} passed, {len(failed)} failed")
	if failed:
		print("FAILED:", failed)
	return not failed
