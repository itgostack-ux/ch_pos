import frappe
from frappe.model.document import Document


class POSIncentiveSlab(Document):
    def validate(self):
        if self.from_amount and self.to_amount and self.from_amount > self.to_amount:
            frappe.throw("From Amount cannot be greater than To Amount", title=_("Pos Incentive Slab Error"))
