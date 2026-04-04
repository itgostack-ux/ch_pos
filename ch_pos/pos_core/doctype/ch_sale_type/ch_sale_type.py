# Copyright (c) 2026, Congruence Holdings and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class CHSaleType(Document):
    def validate(self):
        self._check_duplicate_sub_types()

    def on_update(self):
        if self._is_finance_type():
            self._sync_finance_partners()

    def _check_duplicate_sub_types(self):
        seen = set()
        for row in self.sub_types:
            key = (row.sale_sub_type or "").strip()
            if not key:
                frappe.throw(f"Row {row.idx}: Sub Type Name is required.")
            if key in seen:
                frappe.throw(f"Row {row.idx}: Duplicate sub type '{key}'.")
            seen.add(key)

    def _is_finance_type(self):
        code = (self.code or "").upper()
        name = (self.sale_type_name or "").lower()
        return code == "FS" or "finance" in name or "emi" in name

    def _sync_finance_partners(self):
        """Auto-create CH Finance Partner records for sub types that don't have one."""
        for row in self.sub_types:
            sub_name = (row.sale_sub_type or "").strip()
            if not sub_name:
                continue
            if not frappe.db.exists("CH Finance Partner", sub_name):
                partner = frappe.new_doc("CH Finance Partner")
                partner.partner_name = sub_name
                partner.enabled = 1
                partner.tenure_options = "3,6,9,12,18,24"
                partner.remarks = f"Auto-created from Finance Sale sub type"
                partner.insert(ignore_permissions=True)
                frappe.msgprint(
                    f"Finance Partner <b>{sub_name}</b> created automatically.",
                    indicator="green",
                    alert=True,
                )
