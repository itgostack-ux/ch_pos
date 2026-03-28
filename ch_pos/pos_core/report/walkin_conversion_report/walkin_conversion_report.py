import frappe
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
        {"fieldname": "date", "label": "Date", "fieldtype": "Date", "width": 110},
        {"fieldname": "pos_profile", "label": "Store", "fieldtype": "Link", "options": "POS Profile", "width": 160},
        {"fieldname": "total_footfall", "label": "Total Footfall", "fieldtype": "Int", "width": 110},
        {"fieldname": "kiosk", "label": "Kiosk", "fieldtype": "Int", "width": 80},
        {"fieldname": "counter", "label": "Counter", "fieldtype": "Int", "width": 80},
        {"fieldname": "engaged", "label": "Engaged", "fieldtype": "Int", "width": 80},
        {"fieldname": "converted", "label": "Converted", "fieldtype": "Int", "width": 90},
        {"fieldname": "dropped", "label": "Dropped", "fieldtype": "Int", "width": 80},
        {"fieldname": "expired", "label": "Expired/No Show", "fieldtype": "Int", "width": 100},
        {"fieldname": "conversion_rate", "label": "Conversion %", "fieldtype": "Percent", "width": 110},
        {"fieldname": "engagement_rate", "label": "Engagement %", "fieldtype": "Percent", "width": 110},
        {"fieldname": "avg_handling_mins", "label": "Avg Handling (mins)", "fieldtype": "Float", "precision": 1, "width": 130},
        {"fieldname": "revenue", "label": "Revenue", "fieldtype": "Currency", "width": 120},
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

    where = "WHERE " + " AND ".join(conditions) if conditions else ""

    rows = frappe.db.sql(f"""
        SELECT
            DATE(t.creation) AS date,
            t.pos_profile,
            COUNT(*) AS total_footfall,
            SUM(CASE WHEN t.visit_source = 'Kiosk' THEN 1 ELSE 0 END) AS kiosk,
            SUM(CASE WHEN t.visit_source = 'Counter' THEN 1 ELSE 0 END) AS counter,
            SUM(CASE WHEN t.status IN ('Engaged', 'In Progress', 'Completed', 'Converted') THEN 1 ELSE 0 END) AS engaged,
            SUM(CASE WHEN t.status = 'Converted' THEN 1 ELSE 0 END) AS converted,
            SUM(CASE WHEN t.status = 'Dropped' THEN 1 ELSE 0 END) AS dropped,
            SUM(CASE WHEN t.status IN ('Expired', 'Cancelled') THEN 1 ELSE 0 END) AS expired,
            AVG(CASE WHEN t.handling_duration > 0 THEN t.handling_duration ELSE NULL END) AS avg_handling_mins,
            COALESCE(SUM(si.grand_total), 0) AS revenue
        FROM `tabPOS Kiosk Token` t
        LEFT JOIN `tabSales Invoice` si
            ON si.name = t.converted_invoice AND si.docstatus = 1
        {where}
        GROUP BY DATE(t.creation), t.pos_profile
        ORDER BY DATE(t.creation) DESC, t.pos_profile
    """, params, as_dict=True)

    for r in rows:
        r["conversion_rate"] = flt(r["converted"] / r["total_footfall"] * 100, 1) if r["total_footfall"] else 0
        r["engagement_rate"] = flt(r["engaged"] / r["total_footfall"] * 100, 1) if r["total_footfall"] else 0
        r["avg_handling_mins"] = flt(r["avg_handling_mins"], 1)

    return rows


def get_chart(data):
    if not data:
        return None
    dates = sorted(set(r["date"] for r in data))[-14:]  # last 14 days
    footfall_by_date = {}
    converted_by_date = {}
    for r in data:
        d = r["date"]
        footfall_by_date[d] = footfall_by_date.get(d, 0) + r["total_footfall"]
        converted_by_date[d] = converted_by_date.get(d, 0) + r["converted"]

    return {
        "data": {
            "labels": [str(d) for d in dates],
            "datasets": [
                {"name": "Footfall", "values": [footfall_by_date.get(d, 0) for d in dates]},
                {"name": "Converted", "values": [converted_by_date.get(d, 0) for d in dates]},
            ],
        },
        "type": "bar",
        "colors": ["#7cd6fd", "#5e64ff"],
    }


def get_summary(data):
    if not data:
        return []
    total_ff = sum(r["total_footfall"] for r in data)
    total_conv = sum(r["converted"] for r in data)
    total_dropped = sum(r["dropped"] for r in data)
    total_revenue = sum(r["revenue"] for r in data)
    return [
        {"value": total_ff, "label": "Total Footfall", "datatype": "Int"},
        {"value": flt(total_conv / total_ff * 100, 1) if total_ff else 0, "label": "Conversion %", "datatype": "Percent", "indicator": "green" if total_ff and total_conv / total_ff > 0.35 else "orange"},
        {"value": total_dropped, "label": "Dropped", "datatype": "Int", "indicator": "red"},
        {"value": total_revenue, "label": "Revenue", "datatype": "Currency", "indicator": "green"},
    ]
