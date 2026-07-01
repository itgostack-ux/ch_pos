"""
Walkin Conversion Report  (ch_pos — any company)
-------------------------------------------------
Source: POS Kiosk Token — works for ALL companies (Gogizmo, SARF, Congruence, etc.)

Rows: one per date × POS profile (store)
Scope: Store Managers see only their assigned CH Store(s); System Manager sees all.
Join chain: POS Kiosk Token.store → tabCH Store → cs.zone / cs.city
"""

import frappe
from frappe import _
from frappe.utils import flt, today

from ch_erp15.ch_erp15.report_scope import scope_where_clause


def execute(filters=None):
    filters = filters or {}
    _apply_defaults(filters)
    scope_sql = _get_scope_sql()
    columns = get_columns()
    data = get_data(filters, scope_sql)
    chart = get_chart(data)
    summary = get_summary(data)
    return columns, data, None, chart, summary


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------

def _apply_defaults(filters):
    if not filters.get("from_date"):
        filters["from_date"] = frappe.utils.add_days(today(), -30)
    if not filters.get("to_date"):
        filters["to_date"] = today()


# ---------------------------------------------------------------------------
# permission scope — delegated to the central CH User Scope helper.
#
# Historical implementation read `CH Store User` directly and returned an
# empty fragment (i.e. full visibility) for any user with no scope row.
# That silently broke fail-closed for scoped users who happened to have no
# CH Store User membership. Tier 4 wires this through the central helper
# so it inherits the same fail-closed contract every other report uses.
# ---------------------------------------------------------------------------

def _get_scope_sql():
    clause = scope_where_clause(
        store_field="t.store",
        pos_profile_field="t.pos_profile",
    )
    if clause is None:
        return ""  # bypass caller — no additional filter
    return f" AND {clause}"


# ---------------------------------------------------------------------------
# columns
# ---------------------------------------------------------------------------

def get_columns():
    return [
        {"fieldname": "date",             "label": _("Date"),               "fieldtype": "Date",     "width": 110},
        {"fieldname": "pos_profile",      "label": _("Store (POS Profile)"), "fieldtype": "Link",     "options": "POS Profile", "width": 170},
        {"fieldname": "zone",             "label": _("Zone"),                "fieldtype": "Link",     "options": "CH Store Zone", "width": 135},
        {"fieldname": "city",             "label": _("City"),                "fieldtype": "Link",     "options": "CH City", "width": 115},
        {"fieldname": "total_footfall",   "label": _("Footfall"),            "fieldtype": "Int",      "width": 90},
        {"fieldname": "kiosk",            "label": _("Kiosk"),               "fieldtype": "Int",      "width": 72},
        {"fieldname": "counter",          "label": _("Counter"),             "fieldtype": "Int",      "width": 78},
        {"fieldname": "engaged",          "label": _("Engaged"),             "fieldtype": "Int",      "width": 78},
        {"fieldname": "converted",        "label": _("Converted"),           "fieldtype": "Int",      "width": 88},
        {"fieldname": "dropped",          "label": _("Dropped"),             "fieldtype": "Int",      "width": 78},
        {"fieldname": "expired",          "label": _("No Show"),             "fieldtype": "Int",      "width": 82},
        {"fieldname": "conversion_rate",  "label": _("Conversion %"),        "fieldtype": "Percent",  "width": 108},
        {"fieldname": "engagement_rate",  "label": _("Engagement %"),        "fieldtype": "Percent",  "width": 108},
        {"fieldname": "avg_handling_mins","label": _("Avg Handling (mins)"), "fieldtype": "Float",    "precision": 1, "width": 130},
        {"fieldname": "revenue",          "label": _("Revenue"),             "fieldtype": "Currency", "width": 120},
    ]


# ---------------------------------------------------------------------------
# data query
# ---------------------------------------------------------------------------

def get_data(filters, scope_sql=""):
    conditions = []
    params = {}

    if filters.get("from_date"):
        conditions.append("DATE(t.creation) >= %(from_date)s")
        params["from_date"] = filters["from_date"]
    if filters.get("to_date"):
        conditions.append("DATE(t.creation) <= %(to_date)s")
        params["to_date"] = filters["to_date"]
    if filters.get("company"):
        conditions.append("t.company = %(company)s")
        params["company"] = filters["company"]
    if filters.get("pos_profile"):
        conditions.append("t.pos_profile = %(pos_profile)s")
        params["pos_profile"] = filters["pos_profile"]
    if filters.get("zone"):
        conditions.append("cs.zone = %(zone)s")
        params["zone"] = filters["zone"]
    if filters.get("city"):
        conditions.append("cs.city = %(city)s")
        params["city"] = filters["city"]

    where_base = ("WHERE " + " AND ".join(conditions)) if conditions else "WHERE 1=1"
    where = where_base + scope_sql

    rows = frappe.db.sql("""
        SELECT
            DATE(t.creation)                                                              AS date,
            t.pos_profile,
            IFNULL(cs.zone,  '')                                                          AS zone,
            IFNULL(cs.city,  '')                                                          AS city,
            COUNT(*)                                                                       AS total_footfall,
            SUM(CASE WHEN t.visit_source = 'Kiosk'   THEN 1 ELSE 0 END)                  AS kiosk,
            SUM(CASE WHEN t.visit_source = 'Counter' THEN 1 ELSE 0 END)                  AS counter,
            SUM(CASE WHEN t.status IN ('Engaged','In Progress','Completed','Converted')
                     THEN 1 ELSE 0 END)                                                   AS engaged,
            SUM(CASE WHEN t.status = 'Converted'     THEN 1 ELSE 0 END)                  AS converted,
            SUM(CASE WHEN t.status = 'Dropped'       THEN 1 ELSE 0 END)                  AS dropped,
            SUM(CASE WHEN t.status IN ('Expired','Cancelled') THEN 1 ELSE 0 END)          AS expired,
            AVG(CASE WHEN t.handling_duration > 0 THEN t.handling_duration ELSE NULL END) AS avg_handling_mins,
            COALESCE(SUM(si.grand_total), 0)                                              AS revenue
        FROM `tabPOS Kiosk Token` t
        LEFT JOIN `tabCH Store`      cs ON cs.name = t.store
        LEFT JOIN `tabSales Invoice` si ON si.name = t.converted_invoice AND si.docstatus = 1
        {where}
        GROUP BY DATE(t.creation), t.pos_profile
        ORDER BY DATE(t.creation) DESC, t.pos_profile
    """.format(where=where), params, as_dict=True)  # noqa: UP032

    for r in rows:
        r["conversion_rate"]   = flt(r["converted"]    / r["total_footfall"] * 100, 1) if r["total_footfall"] else 0
        r["engagement_rate"]   = flt(r["engaged"]      / r["total_footfall"] * 100, 1) if r["total_footfall"] else 0
        r["avg_handling_mins"] = flt(r["avg_handling_mins"], 1)

    return rows


# ---------------------------------------------------------------------------
# chart — last 14 dates
# ---------------------------------------------------------------------------

def get_chart(data):
    if not data:
        return None
    dates = sorted({r["date"] for r in data})[-14:]
    ff_map   = {}
    conv_map = {}
    for r in data:
        d = r["date"]
        ff_map[d]   = ff_map.get(d, 0)   + r["total_footfall"]
        conv_map[d] = conv_map.get(d, 0) + r["converted"]

    return {
        "data": {
            "labels": [str(d) for d in dates],
            "datasets": [
                {"name": _("Footfall"),   "values": [ff_map.get(d, 0)   for d in dates]},
                {"name": _("Converted"),  "values": [conv_map.get(d, 0) for d in dates]},
            ],
        },
        "type": "bar",
        "colors": ["#7cd6fd", "#5e64ff"],
    }


# ---------------------------------------------------------------------------
# summary strip
# ---------------------------------------------------------------------------

def get_summary(data):
    if not data:
        return []
    total_ff      = sum(r["total_footfall"] for r in data)
    total_conv    = sum(r["converted"]      for r in data)
    total_dropped = sum(r["dropped"]        for r in data)
    total_revenue = sum(r["revenue"]        for r in data)
    conv_pct      = flt(total_conv / total_ff * 100, 1) if total_ff else 0
    return [
        {"value": total_ff,      "label": _("Total Footfall"),  "datatype": "Int",      "color": "blue"},
        {"value": conv_pct,      "label": _("Conversion %"),    "datatype": "Percent",  "color": "green" if conv_pct >= 35 else "orange"},
        {"value": total_conv,    "label": _("Converted"),       "datatype": "Int",      "color": "green"},
        {"value": total_dropped, "label": _("Dropped"),         "datatype": "Int",      "color": "red"},
        {"value": total_revenue, "label": _("Revenue"),         "datatype": "Currency", "color": "green"},
    ]
