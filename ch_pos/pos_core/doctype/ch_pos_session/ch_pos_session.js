// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

/**
 * CH POS Session form customisations.
 *
 * Administrator-only "Re-open Session" button for Closed sessions.
 * Mirrors the SAP CAR "Cash Desk Re-open" and Oracle Xstore "Force Open Till"
 * supervisor overrides — a deliberate, audited break from the normal lifecycle
 * (Closed is a terminal state in `_VALID_TRANSITIONS`) used when a cashier
 * mistakenly closes a session that still needs to bill.
 */
frappe.ui.form.on("CH POS Session", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.status !== "Closed") return;
		// Hard-gated to Administrator. Backend re-validates.
		if (frappe.session.user !== "Administrator") return;

		frm.add_custom_button(
			__("Re-open Session"),
			() => {
				frappe.prompt(
					[
						{
							fieldname: "reason",
							label: __("Reason"),
							fieldtype: "Small Text",
							reqd: 1,
							description: __(
								"This will void the linked CH POS Settlement and " +
									"flip the POS Opening Entry back to Open. The store " +
									"business date will roll back to {0} if it has been advanced.",
								[frm.doc.business_date],
							),
						},
					],
					(values) => {
						frappe.dom.freeze(__("Re-opening session…"));
						frappe.call({
							method: "ch_pos.api.session_api.admin_reopen_session",
							args: {
								session_name: frm.doc.name,
								reason: values.reason,
							},
							always: () => frappe.dom.unfreeze(),
							callback: (r) => {
								if (!r || !r.message) return;
								frappe.show_alert({
									message: r.message.message || __("Session re-opened."),
									indicator: "orange",
								});
								frm.reload_doc();
							},
						});
					},
					__("Re-open Closed Session"),
					__("Re-open"),
				);
			},
			__("Administrator"),
		);
	},
});
