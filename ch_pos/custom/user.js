// A Sales Person user only appears in POS Billed By and never logs in, so no
// welcome email; the server drops it too (overrides/sales_person_user.py).
frappe.ui.form.on("User", {
	refresh(frm) {
		frm.set_df_property("send_welcome_email", "read_only", frm.doc.custom_is_sales_person ? 1 : 0);
	},
	custom_is_sales_person(frm) {
		if (frm.doc.custom_is_sales_person) {
			frm.set_value("send_welcome_email", 0);
		}
		frm.set_df_property("send_welcome_email", "read_only", frm.doc.custom_is_sales_person ? 1 : 0);
	},
});
