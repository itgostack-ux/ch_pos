# Copyright (c) 2026, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class CHBusinessAuditLog(Document):
	def validate(self):
		if not self.event_type:
			self.event_type = "Other"
		if not self.user:
			self.user = frappe.session.user
		if not self.timestamp:
			self.timestamp = now_datetime()

	def before_insert(self):
		self.validate()
