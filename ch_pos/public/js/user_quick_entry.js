// New User quick entry: ticking "Is Sales Person" clears and disables Role
// Profiles -- a Sales Person never logs in, so no role profile applies.
// (The full User form hides its Roles & Permissions tab the same way,
// custom/user.js.) Frappe uses frappe.ui.form.<DocType>QuickEntryForm when
// one exists.
(function () {
	function define() {
		if (!frappe.ui.form || !frappe.ui.form.QuickEntryForm) return false;
		if (frappe.ui.form.UserQuickEntryForm) return true;
		frappe.ui.form.UserQuickEntryForm = class UserQuickEntryForm extends frappe.ui.form.QuickEntryForm {
			render_dialog() {
				super.render_dialog();
				const sync = () => {
					if (!this.fields_dict.role_profiles) return;
					const is_sp = cint(this.get_value("custom_is_sales_person")) === 1;
					if (is_sp && (this.get_value("role_profiles") || []).length) {
						this.set_value("role_profiles", []);
					}
					this.set_df_property("role_profiles", "read_only", is_sp ? 1 : 0);
				};
				const field = this.fields_dict.custom_is_sales_person;
				if (field) {
					field.df.onchange = sync;
					field.$input && field.$input.on("change", sync);
				}
				sync();
			}
		};
		return true;
	}
	if (!define()) {
		$(document).ready(() => { if (!define()) setTimeout(define, 1500); });
	}
})();
