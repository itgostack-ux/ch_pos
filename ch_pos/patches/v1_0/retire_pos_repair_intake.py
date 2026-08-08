"""Retire the duplicate POS Repair Intake wrapper.

POS now creates Service Request directly. Existing intake rows are only removed
after proving they have a valid canonical Service Request link.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import delete_custom_fields


def execute():
	_validate_and_relink_history()
	delete_custom_fields({"Sales Invoice": ["custom_repair_intake"]})
	if frappe.db.exists("DocType", "POS Repair Intake"):
		frappe.delete_doc("DocType", "POS Repair Intake", force=True, ignore_permissions=True)
	if frappe.db.table_exists("POS Repair Intake"):
		frappe.db.sql_ddl("DROP TABLE `tabPOS Repair Intake`")
	frappe.clear_cache(doctype="Sales Invoice")


def _validate_and_relink_history():
	if not frappe.db.table_exists("POS Repair Intake"):
		return
	invalid = frappe.db.sql(
		"""
		SELECT pri.name
		  FROM `tabPOS Repair Intake` pri
		  LEFT JOIN `tabService Request` sr ON sr.name = pri.service_request
		 WHERE sr.name IS NULL
		""",
		pluck=True,
	)
	if invalid:
		frappe.throw(
			"Cannot retire POS Repair Intake: these records have no canonical "
			f"Service Request: {', '.join(invalid[:20])}"
		)
	if frappe.db.has_column("Sales Invoice", "custom_repair_intake"):
		frappe.db.sql(
			"""
			UPDATE `tabSales Invoice` si
			JOIN `tabPOS Repair Intake` pri ON pri.name = si.custom_repair_intake
			   SET si.custom_gofix_service_request = COALESCE(
			       NULLIF(si.custom_gofix_service_request, ''), pri.service_request)
			 WHERE IFNULL(si.custom_repair_intake, '') != ''
			"""
		)
