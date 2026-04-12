// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Store Wise Business Date Closure"] = {
	filters: [
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
		},
		{
			fieldname: "store",
			label: __("Store"),
			fieldtype: "Link",
			options: "Warehouse",
		}
	],
};
