// Parent Item is editable on a saved bundle. ERPNext locks it (the bundle is
// named after its parent item), so picking a new one here does not save the
// form: after a confirm, ch_pos.overrides.product_bundle.change_bundle_parent
// checks the item and renames the bundle to it, then the form reopens under
// the new name. Cancelled or refused, the form goes back to the saved item.
frappe.ui.form.on("Product Bundle", {
	refresh(frm) {
		if (frm.is_new() || !frm.perm[0] || !frm.perm[0].write) return;
		frm._ch_saved_parent = frm.doc.new_item_code;
		// ERPNext's own refresh disables the field; undo it after that runs.
		setTimeout(() => frm.toggle_enable("new_item_code", true), 0);
	},

	new_item_code(frm) {
		const old_parent = frm._ch_saved_parent;
		const new_parent = frm.doc.new_item_code;
		if (frm.is_new() || !old_parent || !new_parent || new_parent === old_parent) return;

		const revert = () => frm.reload_doc();
		frappe.confirm(
			__("Change Parent Item from {0} to {1}? The bundle and its free items move to {1}. Past invoices are not changed.", [
				frappe.utils.escape_html(old_parent),
				frappe.utils.escape_html(new_parent),
			]),
			() => {
				frappe.call({
					method: "ch_pos.overrides.product_bundle.change_bundle_parent",
					args: { bundle: frm.doc.name, new_parent },
					freeze: true,
					callback: (r) => {
						if (!r.message) return revert();
						frappe.model.remove_from_locals("Product Bundle", old_parent);
						frappe.show_alert({ message: __("Parent Item changed to {0}", [r.message]), indicator: "green" });
						frappe.set_route("Form", "Product Bundle", r.message);
					},
					error: revert,
				});
			},
			revert
		);
	},

	validate(frm) {
		// A plain Save must not carry a changed parent; the rename does that.
		if (!frm.is_new() && frm._ch_saved_parent && frm.doc.new_item_code !== frm._ch_saved_parent) {
			frappe.throw(__("Parent Item is changed through its confirmation, not Save. Pick the item again and confirm."));
		}
	},
});
