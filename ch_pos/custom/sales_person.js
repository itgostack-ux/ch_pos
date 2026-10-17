// POS Sales By: pick the Company first; the Store (Branch) list shows only its
// stores. The filter is set on every refresh too, so a script that sets a
// Store filter earlier (e.g. a site-wide company filter) cannot win.
function ch_set_store_query(frm) {
	frm.set_query("custom_store", () => ({
		filters: frm.doc.custom_company ? { company: frm.doc.custom_company } : {},
	}));
}

// Sales Person is a tree doctype: its breadcrumb opens the last view used,
// Tree by default. Staff work from the list, so point it there.
function ch_sales_person_list_breadcrumb() {
	try {
		const settings = frappe.model.user_settings["Sales Person"] || {};
		if (settings.last_view !== "List") {
			frappe.model.user_settings.save("Sales Person", "last_view", "List");
		}
	} catch (e) { /* settings are a convenience only */ }
	$('.navbar-breadcrumbs a[href*="/sales-person/view/"]').attr("href", "/desk/sales-person");
}

frappe.ui.form.on("Sales Person", {
	setup: ch_set_store_query,
	onload: ch_set_store_query,
	refresh(frm) {
		ch_set_store_query(frm);
		setTimeout(() => {
			ch_set_store_query(frm);
			ch_sales_person_list_breadcrumb();
		}, 0);
	},
	custom_company(frm) {
		ch_set_store_query(frm);
		// A store of another company no longer fits.
		if (frm.doc.custom_store) {
			frappe.db.get_value("CH Store", frm.doc.custom_store, "company").then((r) => {
				if ((r.message || {}).company !== frm.doc.custom_company) {
					frm.set_value("custom_store", null);
				}
			});
		}
	},
	// Email and Mobile No follow the User picked.
	custom_user(frm) {
		if (!frm.doc.custom_user) return;
		frappe.db.get_value("User", frm.doc.custom_user, ["email", "mobile_no"]).then((r) => {
			const u = r.message || {};
			if (u.email) frm.set_value("custom_email", u.email);
			if (u.mobile_no) frm.set_value("custom_mobile_no", u.mobile_no);
		});
	},
});
