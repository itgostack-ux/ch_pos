# Copyright (c) 2025, GoStack and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHAttachRule(Document):
	TRIGGER_DOCTYPE_MAP = {
		"Category": "CH Category",
		"Subcategory": "CH Sub Category",
		"Brand": "Brand",
		"Item": "Item",
	}

	def before_validate(self):
		self._set_trigger_doctype()

	def _validate_links(self):
		"""Override to set trigger_doctype before Frappe validates dynamic links."""
		self._set_trigger_doctype()
		super()._validate_links()

	def validate(self):
		pass

	def _set_trigger_doctype(self):
		"""Map trigger_type select value to the actual DocType for Dynamic Link."""
		if self.trigger_type:
			dt = self.TRIGGER_DOCTYPE_MAP.get(self.trigger_type)
			if not dt:
				frappe.throw(f"Invalid trigger type: {self.trigger_type}")
			self.trigger_doctype = dt


def get_attach_rules_for_item(item_code):
	"""Return all active attach rules applicable to the given item."""
	item = frappe.get_cached_doc("Item", item_code)
	rules = []

	for rule in frappe.get_all("CH Attach Rule",
		filters={"is_active": 1},
		fields=["name", "trigger_type", "trigger_value", "attach_type", "skip_reason_required"]):

		match = False
		if rule.trigger_type == "Item" and rule.trigger_value == item_code:
			match = True
		elif rule.trigger_type == "Category" and rule.trigger_value == getattr(item, "ch_category", None):
			match = True
		elif rule.trigger_type == "Subcategory" and rule.trigger_value == getattr(item, "ch_sub_category", None):
			match = True
		elif rule.trigger_type == "Brand" and rule.trigger_value == getattr(item, "brand", None):
			match = True

		if match:
			rule["attach_items"] = frappe.get_all("CH Attach Rule Item",
				filters={"parent": rule.name},
				fields=["item_code", "item_name", "is_mandatory_offer"])
			rules.append(rule)

	return rules
