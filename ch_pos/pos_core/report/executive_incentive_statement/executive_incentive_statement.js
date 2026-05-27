// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Executive Incentive Statement"] = {
	filters: [
		{
			fieldname: "brand",
			label: __("Brand"),
			fieldtype: "Link",
			options: "Brand",
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "pos_executive",
			label: __("Pos Executive"),
			fieldtype: "Link",
			options: "POS Executive",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nPending\nPaid\nApproved\nCancelled",
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "CH Store",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: "Summary\nDetail",
			default: "Summary",
		}
	],
};
