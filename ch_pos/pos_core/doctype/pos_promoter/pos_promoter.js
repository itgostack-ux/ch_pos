// Pick the Company first; the Store list shows only its stores.
frappe.ui.form.on("POS Promoter", {
	setup(frm) {
		frm.set_query("store", () => ({
			filters: frm.doc.company ? { company: frm.doc.company } : {},
		}));
	},
	company(frm) {
		if (frm.doc.store) {
			frappe.db.get_value("CH Store", frm.doc.store, "company").then((r) => {
				if ((r.message || {}).company !== frm.doc.company) {
					frm.set_value("store", null);
				}
			});
		}
	},
});
