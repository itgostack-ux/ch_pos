// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["POS Tax Summary"] = {
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
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "mode_of_payment",
			label: __("Mode Of Payment"),
			fieldtype: "Data",
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "tax_scheme",
			label: __("Tax Scheme"),
			fieldtype: "Data",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.now_date(),
		}
	],
};
