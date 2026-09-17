"""CH Business Date — store-level business date control.

The business date does NOT auto-change with the system clock.
Only a manager with override permission can advance the date.
All POS transactions are tagged with this business date.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, now_datetime, nowdate


class CHBusinessDate(Document):
	def validate(self):
		if not self.set_by:
			self.set_by = frappe.session.user
		if not self.set_at:
			self.set_at = now_datetime()


def advance_business_date(store, new_date, reason=None, manager_user=None,
			  authorised=False, allow_rewind=False):
	"""Advance the business date for a store.

	`authorised=True` says the caller has already proved the right to roll the
	day — an emailed code or a manager PIN, plus store scope — and that its
	verification, not a role, is the gate.

	The default stays closed. Without it this falls back to the
	`CH Business Date` write DocPerm, which only System Manager holds, so any
	future caller that forgets to authorise is refused rather than waved
	through.

	Why the flag exists at all: a store executive holds no manager role by
	design, so the DocPerm check refused the two paths built for them — the
	day-roll dialog (after the OTP was accepted) and the automatic advance at
	end of day, which would have thrown inside `close_session` and rolled the
	whole close back.

	`allow_rewind=True` permits moving the date BACKWARDS. It is off by default
	because this is called "advance" for a reason: the store's trading day only
	ever moves forward. Only the future was blocked before, so an operator could
	pick any earlier date in the day-roll dialog and every session opened
	afterwards would be stamped with it. The one legitimate rewind is reopening
	a closed session, which puts the store back on that session's own day.
	"""
	lock_key = f"bd_advance_{frappe.scrub(store)}"
	lock_result = frappe.db.sql("SELECT GET_LOCK(%s, 15)", (lock_key,))[0][0]
	if lock_result != 1:
		frappe.throw(_("Business date for store {0} is being updated by another process. Please retry.").format(store))
	try:
		if not authorised:
			frappe.has_permission("CH Business Date", "write", throw=True)
		new_date = getdate(new_date)
		if new_date > getdate(nowdate()):
			frappe.throw(
				_("Business date cannot be set in the future. Choose today or an earlier operational date."),
				title=_("Invalid Business Date"),
			)

		# The trading day moves forward. Blocking only the future let an
		# operator pick an earlier date and back-date everything opened after
		# it — the server clock is authoritative for when a thing was entered,
		# and the business date must not be usable to contradict it.
		current = frappe.db.get_value("CH Business Date", store, "business_date")
		if current and not allow_rewind and new_date < getdate(current):
			frappe.throw(
				_("Business date is already {0}. It cannot be moved back to {1} — "
				  "the trading day only moves forward. Reopen the session for that "
				  "day if you need to correct it.").format(getdate(current), new_date),
				title=_("Cannot Back-Date"),
			)
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

		from ch_pos.audit import log_business_event

		log_business_event(
			event_type="Business Date Change",
			ref_doctype="CH Business Date",
			ref_name=store,
			before=str(doc.previous_date or ""),
			after=str(new_date),
			remarks=reason or "",
			raise_on_error=True,
		)

		return doc.as_dict()
	finally:
		frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock_key,))
