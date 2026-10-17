import frappe
from frappe import _
from frappe.model.document import Document


class POSPromoter(Document):
    def validate(self):
        self.promoter_name = (self.promoter_name or "").strip()
        self.mobile_no = (self.mobile_no or "").strip() or None
        if self.store:
            store_company = frappe.db.get_value("CH Store", self.store, "company")
            if not store_company:
                frappe.throw(_("Store {0} has no Company set.").format(frappe.bold(self.store)))
            if not self.company:
                self.company = store_company
            elif store_company != self.company:
                frappe.throw(
                    _("Store {0} belongs to {1}, not {2}.").format(
                        frappe.bold(self.store), store_company, self.company),
                    title=_("Store Not in Company"))
