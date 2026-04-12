import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    summary = get_summary(data)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"fieldname": "staff", "label": _("Staff"), "fieldtype": "Data", "width": 180},
        {"fieldname": "pos_profile", "label": _("Store"), "fieldtype": "Link", "options": "POS Profile", "width": 150},
        {"fieldname": "total_handled", "label": _("Walk-ins Handled"), "fieldtype": "Int", "width": 120},
        {"fieldname": "engaged", "label": _("Engaged"), "fieldtype": "Int", "width": 90},
        {"fieldname": "converted", "label": _("Converted"), "fieldtype": "Int", "width": 90},
        {"fieldname": "dropped", "label": _("Dropped"), "fieldtype": "Int", "width": 80},
        {"fieldname": "conversion_rate", "label": _("Conversion %"), "fieldtype": "Percent", "width": 110},
        {"fieldname": "avg_handling_mins", "label": _("Avg Handling (mins)"), "fieldtype": "Float", "precision": 1, "width": 130},
        {"fieldname": "revenue", "label": _("Revenue Generated"), "fieldtype": "Currency", "width": 130},
        {"fieldname": "avg_ticket", "label": _("Avg Ticket Size"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "top_drop_reason", "label": _("Top Drop Reason"), "fieldtype": "Data", "width": 150},
    ]


def get_data(filters):
    conditions = []
    params = {}

    if filters.get("from_date"):
        conditions.append("DATE(t.creation) >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("DATE(t.creation) <= %(to_date)s")
        params["to_date"] = filters["to_date"]
    if filters.get("pos_profile"):
        conditions.append("t.pos_profile = %(pos_profile)s")
        params["pos_profile"] = filters["pos_profile"]
    if filters.get("company"):
        conditions.append("t.company = %(company)s")
        params["company"] = filters["company"]

    # Only include tokens that have a staff assignment
    conditions.append("(t.technician IS NOT NULL AND t.technician != '')")

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    rows = frappe.db.sql("""
        SELECT
            COALESCE(u.full_name, t.technician) AS staff,
            t.pos_profile,
            COUNT(*) AS total_handled,
            SUM(CASE WHEN t.status IN ('Engaged', 'In Progress', 'Completed', 'Converted') THEN 1 ELSE 0 END) AS engaged,
            SUM(CASE WHEN t.status = 'Converted' THEN 1 ELSE 0 END) AS converted,
            SUM(CASE WHEN t.status = 'Dropped' THEN 1 ELSE 0 END) AS dropped,
            AVG(CASE WHEN t.handling_duration > 0 THEN t.handling_duration ELSE NULL END) AS avg_handling_mins,
            COALESCE(SUM(si.grand_total), 0) AS revenue
        FROM `tabPOS Kiosk Token` t
        LEFT JOIN `tabUser` u ON u.name = t.technician
        LEFT JOIN `tabSales Invoice` si
            ON si.name = t.converted_invoice AND si.docstatus = 1
        {where}
        GROUP BY t.technician, t.pos_profile
        ORDER BY converted DESC, total_handled DESC
    """.format(where=where), params, as_dict=True)  # noqa: UP032

    for r in rows:
        r["conversion_rate"] = flt(r["converted"] / r["total_handled"] * 100, 1) if r["total_handled"] else 0
        r["avg_ticket"] = flt(r["revenue"] / r["converted"], 2) if r["converted"] else 0
        r["avg_handling_mins"] = flt(r["avg_handling_mins"], 1)

    # Find top drop reason per staff
    if rows:
        staff_list = [r["staff"] for r in rows]
        drop_data = frappe.db.sql("""
            SELECT
                COALESCE(u.full_name, t.technician) AS staff,
                t.drop_reason,
                COUNT(*) AS cnt
            FROM `tabPOS Kiosk Token` t
            LEFT JOIN `tabUser` u ON u.name = t.technician
            {where}
            AND t.status = 'Dropped'
            AND t.drop_reason IS NOT NULL AND t.drop_reason != ''
            GROUP BY t.technician, t.drop_reason
            ORDER BY cnt DESC
        """.format(where=where), params, as_dict=True)  # noqa: UP032

        top_reasons = {}
        for d in drop_data:
            if d["staff"] not in top_reasons:
                top_reasons[d["staff"]] = d["drop_reason"]

        for r in rows:
            r["top_drop_reason"] = top_reasons.get(r["staff"], "")

    return rows


def get_chart(data):
    if not data:
        return None
    top10 = data[:10]
    return {
        "data": {
            "labels": [r["staff"] for r in top10],
            "datasets": [
                {"name": "Converted", "values": [r["converted"] for r in top10]},
                {"name": "Dropped", "values": [r["dropped"] for r in top10]},
            ],
        },
        "type": "bar",
        "colors": ["#5e64ff", "#ff5858"],
    }


def get_summary(data):
    if not data:
        return []
    total_handled = sum(r["total_handled"] for r in data)
    total_conv = sum(r["converted"] for r in data)
    total_revenue = sum(r["revenue"] for r in data)
    best_staff = max(data, key=lambda r: r["conversion_rate"]) if data else {}
    return [
        {"value": len(data), "label": _("Active Staff"), "datatype": "Int"},
        {"value": flt(total_conv / total_handled * 100, 1) if total_handled else 0, "label": _("Team Conversion %"), "datatype": "Percent"},
        {"value": total_revenue, "label": _("Total Revenue"), "datatype": "Currency"},
        {"value": f"{best_staff.get('staff', '')} ({best_staff.get('conversion_rate', 0)}%)", "label": _("Top Performer"), "datatype": "Data", "indicator": "green"},
    ]
