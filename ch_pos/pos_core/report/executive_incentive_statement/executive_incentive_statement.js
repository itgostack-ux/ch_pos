// Copyright (c) 2026, GoStack and contributors
// For license information, please see license.txt

frappe.query_reports["Executive Incentive Statement"] = {
	onload(report) {
		// Apply default preset once when the report opens.
		const preset = report.get_filter_value("period_preset") || "MTD";
		_apply_period_preset(report, preset);
	},

	filters: [
		{
			fieldname: "period_preset",
			label: __("Period Preset"),
			fieldtype: "Select",
			options: "Custom\nMTD\nLast Month\nThis Quarter",
			default: "MTD",
			on_change: function (query_report) {
				const preset = query_report.get_filter_value("period_preset") || "Custom";
				_apply_period_preset(query_report, preset);
			}
		},
		{
			fieldname: "brand",
			label: __("Brand"),
			fieldtype: "Link",
			options: "Brand",
		},
		{
			fieldname: "item_group",
			label: __("Category"),
			fieldtype: "Link",
			options: "Item Group",
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
			fieldname: "city",
			label: __("City"),
			fieldtype: "Link",
			options: "CH City",
		},
		{
			fieldname: "zone",
			label: __("Zone"),
			fieldtype: "Link",
			options: "CH Store Zone",
		},
		{
			fieldname: "transaction_type",
			label: __("Transaction Type"),
			fieldtype: "Select",
			options: "\nSale\nService\nReturn\nExchange\nSwap\nWarranty\nVAS\nAccessory\nAttach Rate",
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


function _apply_period_preset(report, preset) {
	if (preset === "Custom") return;

	const today = frappe.datetime.now_date();
	let fromDate = report.get_filter_value("from_date");
	let toDate = report.get_filter_value("to_date");

	if (preset === "MTD") {
		fromDate = frappe.datetime.month_start(today);
		toDate = today;
	}

	if (preset === "Last Month") {
		const prevMonthDate = frappe.datetime.add_months(today, -1);
		fromDate = frappe.datetime.month_start(prevMonthDate);
		toDate = frappe.datetime.month_end(prevMonthDate);
	}

	if (preset === "This Quarter") {
		const [y, m] = today.split("-").map((v) => Number(v));
		const startMonth = (Math.floor((m - 1) / 3) * 3) + 1;
		fromDate = `${y}-${String(startMonth).padStart(2, "0")}-01`;
		toDate = today;
	}

	report.set_filter_value("from_date", fromDate);
	report.set_filter_value("to_date", toDate);
}
