import frappe


OLD_DOCTYPE = "CH Manager PIN"
NEW_DOCTYPE = "CH POS Password"


def execute():
    if frappe.db.exists("DocType", OLD_DOCTYPE) and not frappe.db.exists("DocType", NEW_DOCTYPE):
        frappe.rename_doc("DocType", OLD_DOCTYPE, NEW_DOCTYPE, force=True)
        frappe.clear_cache(doctype=NEW_DOCTYPE)
        frappe.db.commit()