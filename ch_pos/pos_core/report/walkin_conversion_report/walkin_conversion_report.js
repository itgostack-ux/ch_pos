frappe.query_reports["Walkin Conversion Report"] = {
    filters: [
        {
            fieldname: "from_date",
            label: __("From Date"),
            fieldtype: "Date",
            default: frappe.datetime.add_days(frappe.datetime.nowdate(), -30),
            reqd: 1,
        },
        {
            fieldname: "to_date",
            label: __("To Date"),
            fieldtype: "Date",
            default: frappe.datetime.nowdate(),
            reqd: 1,
        },
        {
            fieldname: "company",
            label: __("Company"),
            fieldtype: "Link",
            options: "Company",
        },
        {
            // Replaces the separate Zone Walk-in Conversion report, which was
            // this same token data cut by zone instead of by store.
            fieldname: "group_by",
            label: __("Group By"),
            fieldtype: "Select",
            options: "Store\nZone\nCity\nDate\nWalk-in (detail)",
            default: "Store",
        },
        {
            fieldname: "zone",
            label: __("Zone"),
            fieldtype: "Link",
            options: "CH Store Zone",
            get_query: () => {
                const company = frappe.query_report.get_filter_value("company");
                return { filters: company ? { company } : {} };
            },
        },
        {
            fieldname: "city",
            label: __("City"),
            fieldtype: "Link",
            options: "CH City",
        },
        {
            fieldname: "pos_profile",
            label: __("Store / POS Profile"),
            fieldtype: "Link",
            options: "POS Profile",
            get_query: () => {
                const company = frappe.query_report.get_filter_value("company");
                return { filters: company ? { company } : {} };
            },
        },
        {
            fieldname: "store",
            label: __("Store"),
            fieldtype: "Link",
            options: "CH Store",
            get_query: () => {
                const company = frappe.query_report.get_filter_value("company");
                return { filters: company ? { company } : {} };
            },
        },
        // ── What the customer told us at check-in ────────────────────────
        // Captured on the token by the tablet and the counter, and until now
        // no report could cut by any of it.
        {
            fieldname: "visit_purpose",
            label: __("Visit Purpose"),
            fieldtype: "Select",
            options: "\nRepair\nSales\nBuyback\nEnquiry\nOther",
        },
        {
            fieldname: "visit_reason",
            label: __("Visit Reason"),
            fieldtype: "Link",
            options: "GoFix Visit Reason",
        },
        {
            fieldname: "referral_source",
            label: __("Heard About Us Via"),
            fieldtype: "Link",
            options: "GoFix Referral Source",
        },
        {
            fieldname: "visit_source",
            label: __("Checked In At"),
            fieldtype: "Select",
            options: "\nKiosk\nCounter\nAppointment\nWeb\nWhatsApp\nOther",
        },
        {
            fieldname: "status",
            label: __("Status"),
            fieldtype: "Select",
            options: "\nWaiting\nHold\nEngaged\nIn Progress\nCompleted\nCancelled\nConverted\nDropped\nExpired",
        },
    ],
};
