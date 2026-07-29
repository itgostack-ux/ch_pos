import frappe
from frappe import _
from frappe.model.document import Document


class CHExceptionReason(Document):
	def validate(self):
		self.reason_name = (self.reason_name or "").strip()
		if not self.reason_name:
			frappe.throw(_("Reason is required."))

		self.reason_code = (self.reason_code or "").strip().upper().replace(" ", "_")

		# Uniqueness is scoped to the exception type, not global: "Other" is a
		# reasonable reason under FIFO Override and under Discount Override.
		clash = frappe.db.get_value(
			"CH Exception Reason",
			{
				"exception_type": self.exception_type,
				"reason_name": self.reason_name,
				"name": ("!=", self.name),
			},
			"name",
		)
		if clash:
			frappe.throw(
				_("{0} already has a reason named {1}.").format(self.exception_type, self.reason_name),
				title=_("Duplicate Reason"),
			)

		if self.reason_code:
			clash = frappe.db.get_value(
				"CH Exception Reason",
				{
					"exception_type": self.exception_type,
					"reason_code": self.reason_code,
					"name": ("!=", self.name),
				},
				"name",
			)
			if clash:
				frappe.throw(
					_("Reason Code {0} is already used by {1}.").format(self.reason_code, clash),
					title=_("Duplicate Reason Code"),
				)
