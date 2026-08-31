"""Remove the POS sidebar item left behind by retire_pos_repair_intake.

drop_pos_repair_intake_workspace_refs scrubbed Workspace Shortcut, Workspace
Link and the content blocks, but v16 renders the left navigation from the
separate Workspace Sidebar Item table, which that patch never touched — so
"Repair Intake" stayed clickable and 404'd for every POS workspace user.
"""

import frappe

DOCTYPE = "POS Repair Intake"


def execute():
	if frappe.db.exists("DocType", DOCTYPE):
		# Still a live DocType here — nothing to clean up.
		return

	if not frappe.db.table_exists("Workspace Sidebar Item"):
		return

	stale = frappe.db.get_all(
		"Workspace Sidebar Item", filters={"link_to": DOCTYPE}, pluck="name"
	)
	for name in stale:
		frappe.db.delete("Workspace Sidebar Item", {"name": name})

	if stale:
		frappe.cache.delete_key("workspace_sidebar_items")
