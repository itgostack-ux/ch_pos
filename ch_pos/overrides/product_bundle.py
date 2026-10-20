"""Change a Product Bundle's Parent Item after it is saved (custom/product_bundle.js).

ERPNext names a Product Bundle after its parent item (autoname =
new_item_code) and locks Parent Item once saved, so moving the free gifts to
another device used to mean Duplicate + disable. This renames the bundle to
the new item and sets Parent Item, after the same checks ERPNext's own save
runs -- all before anything is touched, so a refused change leaves the bundle
exactly as it was.

Past invoices are unaffected: they reference items, not the bundle.
"""

import frappe
from frappe import _


@frappe.whitelist(methods=["POST"])
def change_bundle_parent(bundle: str, new_parent: str) -> str:
    doc = frappe.get_doc("Product Bundle", bundle)
    doc.check_permission("write")

    new_parent = (new_parent or "").strip()
    if not new_parent or new_parent == doc.new_item_code:
        frappe.throw(_("Choose a different Parent Item."))

    item = frappe.db.get_value(
        "Item", new_parent, ["name", "disabled", "is_stock_item", "is_fixed_asset"], as_dict=True
    )
    if not item:
        frappe.throw(_("Item {0} does not exist.").format(new_parent))
    if item.disabled:
        frappe.throw(_("Item {0} is disabled.").format(new_parent))
    if item.is_stock_item:
        frappe.throw(_("Parent Item {0} must not be a Stock Item").format(new_parent))
    if item.is_fixed_asset:
        frappe.throw(_("Parent Item {0} must not be a Fixed Asset").format(new_parent))
    if frappe.db.exists("Product Bundle", new_parent):
        frappe.throw(
            _("{0} already has a Product Bundle. Edit that bundle instead, or disable and delete it first.").format(
                frappe.bold(new_parent)
            ),
            title=_("Bundle Already Exists"),
        )
    if any(row.item_code == new_parent for row in doc.items):
        frappe.throw(_("{0} is one of this bundle's own items; it cannot be the Parent Item.").format(new_parent))

    # The bundle's name IS its parent item, so the change is a rename.
    frappe.rename_doc("Product Bundle", doc.name, new_parent, force=True, show_alert=False)
    renamed = frappe.get_doc("Product Bundle", new_parent)
    renamed.new_item_code = new_parent
    renamed.add_comment(
        "Edit", _("Parent Item changed from {0} to {1}").format(frappe.bold(bundle), frappe.bold(new_parent))
    )
    renamed.save()
    return renamed.name
