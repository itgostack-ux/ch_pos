"""CH Business Date — store-level business date control.

The business date does NOT auto-change with the system clock.
Only a manager with override permission can advance the date.
All POS transactions are tagged with this business date.
"""

import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now_datetime


class CHBusinessDate(Document):
	def validate(self):
		if not self.set_by:
			self.set_by = frappe.session.user
		if not self.set_at:
			self.set_at = now_datetime()


def advance_business_date(store, new_date, reason=None, manager_user=None):
	"""Advance the business date for a store. Requires manager override."""
	frappe.has_permission("CH Business Date", "write", throw=True)
	new_date = getdate(new_date)
	timestamp = now_datetime()
	acting_user = manager_user or frappe.session.user

	if frappe.db.exists("CH Business Date", store):
		doc = frappe.get_doc("CH Business Date", store)
		doc.previous_date = doc.business_date
		doc.business_date = new_date
		doc.override_reason = reason or ""
		doc.set_by = acting_user
		doc.set_at = timestamp
		doc.status = "Open"
		doc.opened_on = timestamp
		doc.opened_by = acting_user
		doc.closed_on = None
		doc.closed_by = None
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({
			"doctype": "CH Business Date",
			"store": store,
			"business_date": new_date,
			"status": "Open",
			"is_active": 1,
			"set_by": acting_user,
			"set_at": timestamp,
			"override_reason": reason or "Initial setup",
		})
		doc.insert(ignore_permissions=True)

	frappe.db.commit()

	try:
		from ch_pos.audit import log_business_event

		log_business_event(
			event_type="Business Date Change",
			ref_doctype="CH Business Date",
			ref_name=store,
			before=str(doc.previous_date or ""),
			after=str(new_date),
			remarks=reason or "",
		)
	except Exception:
		frappe.log_error("Business Date audit log failed")

	return doc.as_dict()
