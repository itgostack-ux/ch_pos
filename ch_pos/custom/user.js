// A Sales Person user only appears in POS Sales By and never logs in: no
// welcome email (the server drops it too, overrides/sales_person_user.py) and
// no Roles & Permissions tab -- roles do not apply to them, so it is hidden
// rather than left to be filled in by mistake. Nothing about roles changes.
//
// The tab is hidden by hiding its two sections (Roles / Role Profiles and
// Allow Modules): an empty tab hides itself. Frappe's own User script shows
// those sections again on refresh and on "Enabled", so this runs after it.
const CH_ROLES_SECTIONS = ["sb1", "sb_allow_modules"];

// Same test as Frappe's has_access_to_edit_user() (core user.js, not exported).
function ch_can_edit_user_roles() {
	const roles = frappe.get_meta("User").permissions
		.filter((p) => p.permlevel >= 1 && p.write)
		.map((p) => p.role);
	return has_common(frappe.user_roles, roles.length ? roles : ["System Manager"]);
}

function ch_sales_person_user_view(frm) {
	const is_sp = cint(frm.doc.custom_is_sales_person) === 1;
	frm.set_df_property("send_welcome_email", "read_only", is_sp ? 1 : 0);
	if (is_sp) {
		frm.toggle_display(CH_ROLES_SECTIONS, false);
	} else if (!frm.is_new() && frm.doc.enabled && ch_can_edit_user_roles()) {
		// Unticked: give the sections back on the same terms Frappe shows them.
		frm.toggle_display(CH_ROLES_SECTIONS, true);
	}

	// A link such as /desk/user/x#roles_permissions_tab opens the tab even
	// when it is hidden; move to the first visible tab instead.
	const tabs = (frm.layout && frm.layout.tabs) || [];
	const roles_tab = tabs.find((t) => t.df.fieldname === "roles_permissions_tab");
	if (is_sp && roles_tab && roles_tab.is_active()) {
		const first = tabs.find((t) => !t.is_hidden());
		first && first.set_active();
	}
}

frappe.ui.form.on("User", {
	refresh(frm) {
		ch_sales_person_user_view(frm);
		setTimeout(() => ch_sales_person_user_view(frm), 0);
	},
	enabled(frm) {
		setTimeout(() => ch_sales_person_user_view(frm), 0);
	},
	custom_is_sales_person(frm) {
		if (frm.doc.custom_is_sales_person) {
			frm.set_value("send_welcome_email", 0);
		}
		ch_sales_person_user_view(frm);
	},
});
