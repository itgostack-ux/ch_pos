function ch_no_future_date(fieldname) {
	const report = frappe.query_report;
	const value = report.get_filter_value(fieldname);
	if (value && value > frappe.datetime.get_today()) {
		frappe.show_alert({ message: __("Future dates cannot be selected"), indicator: "orange" });
		report.set_filter_value(fieldname, "");
		return;
	}
	report.refresh();
}

frappe.query_reports["Free Gift Bundle Report"] = {
	// From / To Date have no default (and are cleared on open, see onload):
	// the period is always picked by hand.
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			// Future days are disabled in the calendar; a typed one is reset.
			max_date: frappe.datetime.str_to_obj(frappe.datetime.get_today()),
			on_change: () => ch_no_future_date("from_date"),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			// Future days are disabled in the calendar; a typed one is reset.
			max_date: frappe.datetime.str_to_obj(frappe.datetime.get_today()),
			on_change: () => ch_no_future_date("to_date"),
			reqd: 1,
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Summary", "Invoice Detail"],
			default: "Summary",
			reqd: 1,
		},
		{
			fieldname: "pos_profile",
			label: __("Store (POS Profile)"),
			fieldtype: "Link",
			options: "POS Profile",
			get_query: () => {
				const company = frappe.query_report.get_filter_value("company");
				return { filters: company ? { company } : {} };
			},
		},
		{
			fieldname: "product_bundle",
			label: __("Product Bundle"),
			fieldtype: "Link",
			options: "Product Bundle",
		},
		{
			fieldname: "status",
			label: __("Gift Status"),
			fieldtype: "Select",
			options: ["", "Given", "Partial", "Skipped", "Not Applicable"],
			depends_on: "eval:doc.view == 'Invoice Detail'",
		},
	],
	// Frappe refills filters from the address bar (?from_date=…) on every
	// open or refresh; the period must be picked by hand each time, so clear
	// it here -- this runs after that refill and before the report runs.
	onload(report) {
		["from_date", "to_date"].forEach((f) => {
			const field = report.get_filter(f);
			if (field && field.get_value()) field.set_value("");
		});
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "gift_status" && data) {
			const color = { Given: "green", Partial: "orange", Skipped: "red", "Not Applicable": "gray" }[data.gift_status];
			if (color) value = `<span class="indicator-pill ${color}">${value}</span>`;
		}
		// Summary: the store's bills with no gift due have no bundle to link.
		if (column.fieldname === "product_bundle" && data && !data.product_bundle && data.product_bundle_label) {
			value = `<span class="text-muted">${frappe.utils.escape_html(data.product_bundle_label)}</span>`;
		}
		return value;
	},
};
