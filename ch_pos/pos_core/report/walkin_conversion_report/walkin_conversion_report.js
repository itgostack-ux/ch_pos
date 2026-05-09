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
            fieldname: "zone",
            label: __("Zone"),
            fieldtype: "Link",
            options: "CH Store Zone",
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
        },
    ],
};
