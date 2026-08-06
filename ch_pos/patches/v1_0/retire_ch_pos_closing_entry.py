"""Retire the parallel closing DocType and repair historical standard links."""

import frappe


def execute():
	_repair_invalid_opening_links()
	if frappe.db.exists("DocType", "CH POS Closing Entry"):
		frappe.delete_doc("DocType", "CH POS Closing Entry", force=True, ignore_permissions=True)
	if frappe.db.table_exists("CH POS Closing Entry"):
		frappe.db.sql_ddl("DROP TABLE `tabCH POS Closing Entry`")


def _repair_invalid_opening_links():
	if not frappe.db.table_exists("POS Opening Entry"):
		return
	rows = frappe.db.sql(
		"""
		SELECT poe.name, poe.pos_closing_entry
		  FROM `tabPOS Opening Entry` poe
		  LEFT JOIN `tabPOS Closing Entry` pce ON pce.name = poe.pos_closing_entry
		 WHERE IFNULL(poe.pos_closing_entry, '') != ''
		   AND pce.name IS NULL
		""",
		as_dict=True,
	)
	for row in rows:
		if frappe.db.exists("CH POS Session", row.pos_closing_entry):
			session = frappe.get_doc("CH POS Session", row.pos_closing_entry)
			if session.status == "Closed" and session.docstatus == 1:
				session._mirror_close_to_opening_entry()
				continue
		frappe.db.set_value(
			"POS Opening Entry",
			row.name,
			{"pos_closing_entry": None, "status": "Open"},
			update_modified=False,
		)
