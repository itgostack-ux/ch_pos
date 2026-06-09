frappe.ui.form.on("POS Incentive Ledger", {
	refresh(frm) {
		if (frm.is_new()) return;

		const canApprove = frappe.user.has_role("System Manager")
			|| frappe.user.has_role("Accounts Manager")
			|| frappe.user.has_role("POS Manager");
		const canPay = frappe.user.has_role("System Manager")
			|| frappe.user.has_role("Accounts Manager");

		if (frm.doc.status === "Pending" && canApprove) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.call({
					method: "ch_pos.pos_core.doctype.pos_incentive_ledger.pos_incentive_ledger.approve_incentive",
					args: { name: frm.doc.name },
					freeze: true,
					freeze_message: __("Approving incentive..."),
					callback: () => frm.reload_doc(),
				});
			}).addClass("btn-primary");
		}

		if (frm.doc.status === "Approved" && canPay) {
			frm.add_custom_button(__("Mark Paid"), () => {
				frappe.prompt([
					{
						fieldname: "payout_reference",
						label: __("Payout Reference"),
						fieldtype: "Data",
						reqd: 1,
					},
					{
						fieldname: "payout_month",
						label: __("Payout Month"),
						fieldtype: "Data",
						default: frm.doc.payout_month,
						description: __("Format: YYYY-MM"),
					},
				],
				(values) => {
					frappe.call({
						method: "ch_pos.pos_core.doctype.pos_incentive_ledger.pos_incentive_ledger.mark_incentive_paid",
						args: {
							name: frm.doc.name,
							payout_reference: values.payout_reference,
							payout_month: values.payout_month,
						},
						freeze: true,
						freeze_message: __("Marking incentive as paid..."),
						callback: () => frm.reload_doc(),
					});
				},
				__("Mark Incentive Paid"),
				__("Submit")
				);
			}).addClass("btn-primary");
		}

		if ((frm.doc.status === "Pending" || frm.doc.status === "Approved") && canApprove) {
			frm.add_custom_button(__("Cancel"), () => {
				frappe.confirm(
					__("Cancel this incentive entry?"),
					() => {
						frappe.call({
							method: "ch_pos.pos_core.doctype.pos_incentive_ledger.pos_incentive_ledger.cancel_incentive",
							args: { name: frm.doc.name },
							freeze: true,
							freeze_message: __("Cancelling incentive..."),
							callback: () => frm.reload_doc(),
						});
					}
				);
			});
		}
	},
});
