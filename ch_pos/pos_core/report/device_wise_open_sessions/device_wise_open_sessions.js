// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Device Wise Open Sessions"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
		}
	],
};
