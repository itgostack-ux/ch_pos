import frappe
from frappe.utils import flt


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    chart = get_chart(data)
    summary = get_summary(data, filters)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"fieldname": "drop_reason", "label": "Drop Reason", "fieldtype": "Data", "width": 180},
        {"fieldname": "pos_profile", "label": "Store", "fieldtype": "Link", "options": "POS Profile", "width": 150},
        {"fieldname": "count", "label": "Count", "fieldtype": "Int", "width": 80},
        {"fieldname": "pct", "label": "% of Drops", "fieldtype": "Percent", "width": 100},
        {"fieldname": "top_category", "label": "Top Category", "fieldtype": "Data", "width": 130},
        {"fieldname": "top_brand", "label": "Top Brand Interest", "fieldtype": "Data", "width": 140},
        {"fieldname": "top_budget", "label": "Top Budget Range", "fieldtype": "Data", "width": 130},
        {"fieldname": "avg_handling_mins", "label": "Avg Handling (mins)", "fieldtype": "Float", "precision": 1, "width": 130},
    ]


def get_data(filters):
    conditions = ["t.status = 'Dropped'"]
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

    where = "WHERE " + " AND ".join(conditions)

    # Main aggregation by drop_reason and store
    rows = frappe.db.sql("""
        SELECT
            COALESCE(NULLIF(t.drop_reason, ''), 'Not Specified') AS drop_reason,
            t.pos_profile,
            COUNT(*) AS count,
            AVG(CASE WHEN t.handling_duration > 0 THEN t.handling_duration ELSE NULL END) AS avg_handling_mins
        FROM `tabPOS Kiosk Token` t
        {where}
        GROUP BY drop_reason, t.pos_profile
        ORDER BY count DESC
    """.format(where=where), params, as_dict=True)  # noqa: UP032

    # Total drops for percentage
    total_drops = sum(r["count"] for r in rows) or 1

    for r in rows:
        r["pct"] = flt(r["count"] / total_drops * 100, 1)
        r["avg_handling_mins"] = flt(r["avg_handling_mins"], 1)

    # Cross-tab: top category, brand, budget per drop_reason
    cross = frappe.db.sql("""
        SELECT
            COALESCE(NULLIF(t.drop_reason, ''), 'Not Specified') AS drop_reason,
            t.category_interest,
            t.brand_interest,
            t.budget_range,
            COUNT(*) AS cnt
        FROM `tabPOS Kiosk Token` t
        {where}
        GROUP BY drop_reason, t.category_interest, t.brand_interest, t.budget_range
        ORDER BY cnt DESC
    """.format(where=where), params, as_dict=True)  # noqa: UP032

    # Build top-N maps
    cat_map, brand_map, budget_map = {}, {}, {}
    for c in cross:
        reason = c["drop_reason"]
        if reason not in cat_map and c.get("category_interest"):
            cat_map[reason] = c["category_interest"]
        if reason not in brand_map and c.get("brand_interest"):
            brand_map[reason] = c["brand_interest"]
        if reason not in budget_map and c.get("budget_range"):
            budget_map[reason] = c["budget_range"]

    for r in rows:
        r["top_category"] = cat_map.get(r["drop_reason"], "")
        r["top_brand"] = brand_map.get(r["drop_reason"], "")
        r["top_budget"] = budget_map.get(r["drop_reason"], "")

    return rows


def get_chart(data):
    if not data:
        return None

    # Aggregate by reason across stores for pie chart
    reason_totals = {}
    for r in data:
        reason_totals.setdefault(r["drop_reason"], 0)
        reason_totals[r["drop_reason"]] += r["count"]

    sorted_reasons = sorted(reason_totals.items(), key=lambda x: x[1], reverse=True)[:8]

    return {
        "data": {
            "labels": [r[0] for r in sorted_reasons],
            "datasets": [{"name": "Drops", "values": [r[1] for r in sorted_reasons]}],
        },
        "type": "pie",
        "colors": ["#ff5858", "#ff9f43", "#feca57", "#54a0ff", "#5f27cd", "#01a3a4", "#c8d6e5", "#8395a7"],
    }


def get_summary(data, filters):
    if not data:
        return []
    total_drops = sum(r["count"] for r in data)

    # Get total footfall for drop rate
    conditions = []
    params = {}
    if filters.get("from_date"):
        conditions.append("DATE(creation) >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("DATE(creation) <= %(to_date)s")
        params["to_date"] = filters["to_date"]
    if filters.get("pos_profile"):
        conditions.append("pos_profile = %(pos_profile)s")
        params["pos_profile"] = filters["pos_profile"]
    if filters.get("company"):
        conditions.append("company = %(company)s")
        params["company"] = filters["company"]

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    total_tokens = frappe.db.sql(
        "SELECT COUNT(*) FROM `tabPOS Kiosk Token` {where}".format(where=where), params
    )[0][0] or 1

    top_reason = data[0]["drop_reason"] if data else "N/A"

    return [
        {"value": total_drops, "label": "Total Drops", "datatype": "Int", "indicator": "red"},
        {"value": flt(total_drops / total_tokens * 100, 1), "label": "Drop Rate %", "datatype": "Percent"},
        {"value": top_reason, "label": "Top Drop Reason", "datatype": "Data", "indicator": "orange"},
    ]
