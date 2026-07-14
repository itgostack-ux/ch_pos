// Copyright (c) 2026, GoStack and contributors

frappe.query_reports["CH Gift Redemption Register"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date (Issued)"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
		},
		{
			fieldname: "to_date",
			label: __("To Date (Issued)"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nIssued\nRevealed\nRedeemed\nExpired\nCancelled",
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "CH Store",
		},
	],
};
