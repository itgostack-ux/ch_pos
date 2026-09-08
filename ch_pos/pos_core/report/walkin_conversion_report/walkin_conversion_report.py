"""
Walkin Conversion Report  (ch_pos — any company)
-------------------------------------------------
The single walk-in report. Three used to exist and disagree:

  * this one            — POS Kiosk Token, per store per day
  * Zone Walk-in Conversion (ch_mg_reports) — the same token data grouped by
    zone, i.e. this report with one filter set differently
  * Walk-in Conversion Report (gofix)       — Service Request instead of token

The first two were the same question asked twice, so the zone view is now a
Group By on this report. The third counted something else entirely and its
numbers could never agree: a walk-in *is* a token, and on this site 54 of 60
Service Requests carried no token at all, so an SR-sourced "walk-in" count both
missed real footfall and counted remote/courier jobs that never walked in.
Counting tokens is the definition that matches the word.

Source: POS Kiosk Token — works for ALL companies (Gogizmo, SARF, Congruence).

Rows: one per date × the chosen Group By (store, zone, city, or date alone)
Scope: Store Managers see only their assigned CH Store(s); System Manager sees all.
Join chain: POS Kiosk Token.pos_profile → CH Store.pos_profile → zone / city.
NOT via token.store — that column holds a Warehouse, so joining it to CH Store
matched nothing and left Zone and City permanently blank.
"""

import frappe
from frappe import _
from frappe.utils import flt, today

from ch_erp15.ch_erp15.report_scope import scope_where_clause


# Group By → (SQL expression, column fieldname, label, fieldtype, link target).
# One place decides how the report is cut, so the columns, the GROUP BY and the
# ORDER BY can never drift apart.
_GROUPINGS = {
    "Store": ("cs.name", "store", "Store", "Link", "CH Store"),
    "Zone": ("cs.zone", "zone", "Zone", "Link", "CH Store Zone"),
    "City": ("cs.city", "city", "City", "Link", "CH City"),
    "Date": (None, None, None, None, None),
    # Not an aggregation: one row per walk-in, showing everything the customer
    # actually entered. The aggregated views answer "how many"; this answers
    # "who came in and what did they say", which previously meant opening
    # tokens one at a time.
    "Walk-in (detail)": (None, None, None, None, None),
}


def execute(filters=None):
    filters = filters or {}
    _apply_defaults(filters)
    scope_sql = _get_scope_sql()
    columns = get_columns(filters)
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
    if filters.get("group_by") not in _GROUPINGS:
        filters["group_by"] = "Store"


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

def _detail_columns():
    return [
        {"fieldname": "token_display", "label": _("Token"), "fieldtype": "Link", "options": "POS Kiosk Token", "width": 150},
        {"fieldname": "creation", "label": _("Checked In"), "fieldtype": "Datetime", "width": 160},
        {"fieldname": "store", "label": _("Store"), "fieldtype": "Link", "options": "CH Store", "width": 150},
        {"fieldname": "store_name", "label": _("Store Name"), "fieldtype": "Data", "width": 130},
        {"fieldname": "customer_name", "label": _("Customer"), "fieldtype": "Data", "width": 140},
        {"fieldname": "customer_phone", "label": _("Phone"), "fieldtype": "Data", "width": 120},
        {"fieldname": "visit_reason", "label": _("Visit Reason"), "fieldtype": "Link", "options": "GoFix Visit Reason", "width": 170},
        {"fieldname": "visit_purpose", "label": _("Purpose"), "fieldtype": "Data", "width": 90},
        {"fieldname": "visit_source", "label": _("Checked In At"), "fieldtype": "Data", "width": 110},
        {"fieldname": "referral_source", "label": _("Heard About Us Via"), "fieldtype": "Link", "options": "GoFix Referral Source", "width": 150},
        {"fieldname": "device_type", "label": _("Device Type"), "fieldtype": "Link", "options": "CH Category", "width": 130},
        {"fieldname": "device_brand", "label": _("Brand"), "fieldtype": "Link", "options": "Brand", "width": 110},
        {"fieldname": "device_model_name", "label": _("Model"), "fieldtype": "Data", "width": 150},
        {"fieldname": "symptoms", "label": _("Symptoms"), "fieldtype": "Data", "width": 260},
        {"fieldname": "issue_category", "label": _("Issue Category"), "fieldtype": "Data", "width": 140},
        {"fieldname": "issue_description", "label": _("Notes"), "fieldtype": "Data", "width": 220},
        {"fieldname": "customer_language", "label": _("Language"), "fieldtype": "Data", "width": 90},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 110},
        {"fieldname": "linked_service_request", "label": _("Service Request"), "fieldtype": "Link", "options": "Service Request", "width": 170},
        {"fieldname": "converted_invoice", "label": _("Invoice"), "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
        {"fieldname": "linked_customer", "label": _("Customer Record"), "fieldtype": "Link", "options": "Customer", "width": 150},
        {"fieldname": "handling_duration", "label": _("Handling (mins)"), "fieldtype": "Int", "width": 120},
    ]


def get_columns(filters=None):
    filters = filters or {}
    group_by = filters.get("group_by") or "Store"
    if group_by == "Walk-in (detail)":
        return _detail_columns()
    _expr, fieldname, label, fieldtype, options = _GROUPINGS.get(group_by, _GROUPINGS["Store"])

    cols = [
        {"fieldname": "date", "label": _("Date"), "fieldtype": "Date", "width": 110},
    ]
    if fieldname:
        col = {"fieldname": fieldname, "label": _(label), "fieldtype": fieldtype, "width": 170}
        if options:
            col["options"] = options
        cols.append(col)
    # Zone and city stay visible when cutting by store, because "which zone is
    # this store in" is the first question anyone asks of a store row.
    if group_by == "Store":
        cols += [
            {"fieldname": "store_name", "label": _("Store Name"), "fieldtype": "Data", "width": 150},
            {"fieldname": "zone", "label": _("Zone"), "fieldtype": "Link", "options": "CH Store Zone", "width": 135},
            {"fieldname": "city", "label": _("City"), "fieldtype": "Link", "options": "CH City", "width": 115},
        ]
    elif group_by == "Zone":
        cols.append({"fieldname": "city", "label": _("City"), "fieldtype": "Link", "options": "CH City", "width": 115})

    cols += [
        {"fieldname": "stores", "label": _("Stores"), "fieldtype": "Int", "width": 78},
        {"fieldname": "total_footfall", "label": _("Footfall"), "fieldtype": "Int", "width": 90},
        {"fieldname": "kiosk", "label": _("Kiosk"), "fieldtype": "Int", "width": 72},
        {"fieldname": "counter", "label": _("Counter"), "fieldtype": "Int", "width": 78},
        {"fieldname": "engaged", "label": _("Engaged"), "fieldtype": "Int", "width": 78},
        {"fieldname": "converted", "label": _("Converted"), "fieldtype": "Int", "width": 88},
        {"fieldname": "dropped", "label": _("Dropped"), "fieldtype": "Int", "width": 78},
        {"fieldname": "expired", "label": _("No Show"), "fieldtype": "Int", "width": 82},
        {"fieldname": "conversion_rate", "label": _("Conversion %"), "fieldtype": "Percent", "width": 108},
        {"fieldname": "engagement_rate", "label": _("Engagement %"), "fieldtype": "Percent", "width": 108},
        # Carried over from the gofix report this replaces, where it was the
        # headline metric under the name "Withdrawn %".
        {"fieldname": "drop_rate", "label": _("Dropped %"), "fieldtype": "Percent", "width": 100},
        {"fieldname": "avg_handling_mins", "label": _("Avg Handling (mins)"), "fieldtype": "Float", "precision": 1, "width": 130},
        {"fieldname": "revenue", "label": _("Revenue"), "fieldtype": "Currency", "width": 120},
    ]
    return cols


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
    if filters.get("store"):
        # CH Store, not the token's warehouse column.
        conditions.append("cs.name = %(store)s")
        params["store"] = filters["store"]
    if filters.get("zone"):
        conditions.append("cs.zone = %(zone)s")
        params["zone"] = filters["zone"]
    if filters.get("city"):
        conditions.append("cs.city = %(city)s")
        params["city"] = filters["city"]
    # Dimensions the tablet and the counter capture. They were on the token all
    # along and no report could cut by them.
    if filters.get("visit_purpose"):
        conditions.append("t.visit_purpose = %(visit_purpose)s")
        params["visit_purpose"] = filters["visit_purpose"]
    if filters.get("visit_source"):
        conditions.append("t.visit_source = %(visit_source)s")
        params["visit_source"] = filters["visit_source"]
    if filters.get("visit_reason"):
        conditions.append("t.visit_reason = %(visit_reason)s")
        params["visit_reason"] = filters["visit_reason"]
    if filters.get("referral_source"):
        conditions.append("t.referral_source = %(referral_source)s")
        params["referral_source"] = filters["referral_source"]
    if filters.get("status"):
        conditions.append("t.status = %(status)s")
        params["status"] = filters["status"]

    where_base = ("WHERE " + " AND ".join(conditions)) if conditions else "WHERE 1=1"
    where = where_base + scope_sql

    group_by = filters.get("group_by") or "Store"

    if group_by == "Walk-in (detail)":
        rows = frappe.db.sql("""
            SELECT
                t.name, t.token_display, t.creation, t.customer_name, t.customer_phone,
                t.visit_reason, t.visit_purpose, t.visit_source, t.referral_source,
                t.device_type, t.device_brand,
                COALESCE(NULLIF(t.device_model_name,''), t.other_device_hint) AS device_model_name,
                t.issue_category, t.issue_description, t.customer_language, t.status,
                t.linked_service_request, t.converted_invoice, t.linked_customer,
                t.handling_duration,
                IFNULL(cs.name, t.pos_profile) AS store,
                IFNULL(cs.store_name, '')      AS store_name
            FROM `tabPOS Kiosk Token` t
            LEFT JOIN `tabCH Store` cs ON cs.pos_profile = t.pos_profile
            {where}
            ORDER BY t.creation DESC
            LIMIT 2000
        """.format(where=where), params, as_dict=True)  # noqa: UP032

        # Symptoms are a child table: one query for the page, not one per row.
        names = [r["name"] for r in rows]
        by_token = {}
        if names:
            for row in frappe.get_all(
                "POS Kiosk Token Symptom",
                filters={"parent": ("in", names)},
                fields=["parent", "symptom_name"],
                order_by="parent asc, idx asc",
                limit_page_length=0,
            ):
                if row.get("symptom_name"):
                    by_token.setdefault(row["parent"], []).append(row["symptom_name"])
        for r in rows:
            r["symptoms"] = ", ".join(by_token.get(r["name"], []))
            # token_display is what a person recognises; the Link needs the
            # docname, so the column points at name and shows the display text.
            r["token_display"] = r.get("token_display") or r["name"]
        return rows
    expr, fieldname, _label, _ft, _opt = _GROUPINGS.get(group_by, _GROUPINGS["Store"])

    # Only the grouped dimension is selected raw; the others are aggregated so
    # a zone row does not silently show one arbitrary store's city.
    if group_by == "Store":
        # Group on the CH Store, not the POS Profile. "POS - STO-GSPL-CHENNA-0008"
        # tells nobody anything; "GF-KELLYS / Kellys" does.
        dim_select = ("IFNULL(cs.name, t.pos_profile) AS store, "
                      "IFNULL(cs.store_name,'') AS store_name, "
                      "t.pos_profile AS pos_profile, "
                      "IFNULL(cs.zone,'') AS zone, IFNULL(cs.city,'') AS city,")
        group_sql = "DATE(t.creation), IFNULL(cs.name, t.pos_profile)"
        order_sql = "DATE(t.creation) DESC, IFNULL(cs.name, t.pos_profile)"
    elif group_by == "Zone":
        dim_select = "IFNULL(cs.zone,'') AS zone, MIN(IFNULL(cs.city,'')) AS city,"
        group_sql = "DATE(t.creation), cs.zone"
        order_sql = "DATE(t.creation) DESC, cs.zone"
    elif group_by == "City":
        dim_select = "IFNULL(cs.city,'') AS city,"
        group_sql = "DATE(t.creation), cs.city"
        order_sql = "DATE(t.creation) DESC, cs.city"
    else:  # Date
        dim_select = ""
        group_sql = "DATE(t.creation)"
        order_sql = "DATE(t.creation) DESC"

    rows = frappe.db.sql("""
        SELECT
            DATE(t.creation)                                                              AS date,
            {dim_select}
            COUNT(DISTINCT t.pos_profile)                                                  AS stores,
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
        -- cs.name = t.store was the join here and it matched 0 of 109 tokens:
        -- POS Kiosk Token.store is a WAREHOUSE ("GF-KELLYS-Sellable - GF"),
        -- while CH Store autonames on the store code ("GF-KELLYS"). Zone and
        -- City were therefore always blank and the zone/city filters always
        -- returned nothing. pos_profile is the link that actually holds:
        -- one CH Store per POS Profile, matching all 109.
        LEFT JOIN `tabCH Store`      cs ON cs.pos_profile = t.pos_profile
        LEFT JOIN `tabSales Invoice` si ON si.name = t.converted_invoice AND si.docstatus = 1
        {where}
        GROUP BY {group_sql}
        ORDER BY {order_sql}
    """.format(dim_select=dim_select, where=where, group_sql=group_sql, order_sql=order_sql),
        params, as_dict=True)  # noqa: UP032

    for r in rows:
        ff = r["total_footfall"]
        r["conversion_rate"] = flt(r["converted"] / ff * 100, 1) if ff else 0
        r["engagement_rate"] = flt(r["engaged"] / ff * 100, 1) if ff else 0
        r["drop_rate"] = flt(r["dropped"] / ff * 100, 1) if ff else 0
        r["avg_handling_mins"] = flt(r["avg_handling_mins"], 1)

    return rows


# ---------------------------------------------------------------------------
# chart — last 14 dates
# ---------------------------------------------------------------------------

def get_chart(data):
    if not data or "total_footfall" not in (data[0] or {}):
        return None   # detail mode has no aggregates to plot
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
    if not data or "total_footfall" not in (data[0] or {}):
        # Detail mode: one row per walk-in, so the count is the headline.
        return [{"value": len(data or []), "label": _("Walk-ins"), "datatype": "Int", "color": "blue"}] if data else []
    total_ff      = sum(r["total_footfall"] for r in data)
    total_conv    = sum(r["converted"]      for r in data)
    total_dropped = sum(r["dropped"]        for r in data)
    total_revenue = sum(r["revenue"]        for r in data)
    conv_pct      = flt(total_conv / total_ff * 100, 1) if total_ff else 0
    drop_pct      = flt(total_dropped / total_ff * 100, 1) if total_ff else 0
    return [
        {"value": total_ff,      "label": _("Total Footfall"),  "datatype": "Int",      "color": "blue"},
        {"value": conv_pct,      "label": _("Conversion %"),    "datatype": "Percent",  "color": "green" if conv_pct >= 35 else "orange"},
        {"value": total_conv,    "label": _("Converted"),       "datatype": "Int",      "color": "green"},
        {"value": total_dropped, "label": _("Dropped"),         "datatype": "Int",      "color": "red"},
        {"value": drop_pct,      "label": _("Dropped %"),       "datatype": "Percent",  "color": "red"},
        {"value": total_revenue, "label": _("Revenue"),         "datatype": "Currency", "color": "green"},
    ]
