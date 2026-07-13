# Copyright (c) 2026, GoStack and contributors
"""Backfill trigger-purchase history on existing CH Gift Redemption rows.

trigger_item / trigger_item_name come from the linked CH Item Offer;
trigger_serial_no is recovered from the parent Sales Invoice line(s).
Redemption-side context (redeemed_store/pos_profile/by) cannot be
reconstructed for old rows and is left blank.
"""

import frappe


def execute():
	if not frappe.db.table_exists("CH Gift Redemption"):
		return

	frappe.reload_doc("pos_core", "doctype", "ch_gift_redemption")

	from ch_pos.api.gift_redemption import _collect_trigger_serials

	rows = frappe.get_all(
		"CH Gift Redemption",
		filters={"trigger_item": ("in", ("", None))},
		fields=["name", "offer", "parent_sales_invoice"],
	)

	updated = 0
	for row in rows:
		if not row.offer or not frappe.db.exists("CH Item Offer", row.offer):
			continue
		trigger_item, trigger_item_name = frappe.db.get_value(
			"CH Item Offer", row.offer, ["trigger_item", "trigger_item_name"]
		)
		if not trigger_item:
			continue

		values = {
			"trigger_item": trigger_item,
			"trigger_item_name": trigger_item_name
				or frappe.db.get_value("Item", trigger_item, "item_name"),
		}
		if row.parent_sales_invoice and frappe.db.exists(
			"Sales Invoice", row.parent_sales_invoice
		):
			si = frappe.get_doc("Sales Invoice", row.parent_sales_invoice)
			serials = _collect_trigger_serials(si, trigger_item)
			if serials:
				values["trigger_serial_no"] = serials

		frappe.db.set_value(
			"CH Gift Redemption", row.name, values, update_modified=False
		)
		updated += 1

	if updated:
		print(f"backfill_gift_redemption_history: backfilled {updated} gift(s)")
