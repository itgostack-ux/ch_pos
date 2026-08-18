"""Remove the POS workspace references left behind by retire_pos_repair_intake.

That patch dropped the DocType but not the Workspace Shortcut / Workspace Link
pointing at it, so opening /desk/pos raised "DocType POS Repair Intake not
found". The workspace JSON on disk had already been cleaned by hand, but Frappe
only re-imports a workspace whose `modified` is NEWER than the DB row — and the
two timestamps were identical — so the stale rows could never be synced away.

Deleting the child rows directly is what makes existing sites self-heal; the
JSON fix alone only helps fresh installs.
"""

import frappe

DOCTYPE = "POS Repair Intake"


def execute():
	if frappe.db.exists("DocType", DOCTYPE):
		# Still a live DocType here — nothing to clean up.
		return

	for table, field in (("Workspace Shortcut", "link_to"), ("Workspace Link", "link_to")):
		if not frappe.db.table_exists(table):
			continue
		stale = frappe.db.get_all(
			table, filters={field: DOCTYPE}, fields=["name", "parent"]
		)
		for row in stale:
			frappe.db.delete(table, {"name": row.name})

	# The layout block referencing the shortcut has to go too, or the workspace
	# renders a slot whose shortcut no longer exists.
	for ws in frappe.db.get_all(
		"Workspace", filters={"content": ("like", "%POS Repair Intake%")}, pluck="name"
	) + frappe.db.get_all(
		"Workspace", filters={"content": ("like", "%Repair Intake%")}, pluck="name"
	):
		doc = frappe.get_doc("Workspace", ws)
		try:
			import json

			blocks = json.loads(doc.content or "[]")
		except Exception:
			continue
		kept = [
			b for b in blocks
			if b.get("id") != "sc_repair"
			and (b.get("data") or {}).get("shortcut_name") != "Repair Intake"
		]
		if len(kept) != len(blocks):
			frappe.db.set_value("Workspace", ws, "content", json.dumps(kept), update_modified=False)

	frappe.clear_cache()
