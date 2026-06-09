"""
Executive Incentive Statement
==============================
Shows incentive earnings per sales executive for a selected period.

Modes
-----
- Manager / Accounts role  → sees ALL executives in selected store/company
- POS User (cashier)       → sees ONLY their own incentive (filtered to
  the POS Executive linked to frappe.session.user)

Sections
--------
1. Summary table  — one row per executive: billings count, billing amount,
                    total earned, paid out, pending
2. Detail table   — every billing row with item, amount, slab, incentive
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, flt, get_first_day, get_last_day, getdate, today
from erpnext.accounts.doctype.monthly_distribution.monthly_distribution import (
    get_periodwise_distribution_data,
)
from erpnext.accounts.report.financial_statements import get_period_list


_PRIVILEGED_ROLES = {
    "System Manager",
    "Accounts Manager",
    "Accounts User",
    "POS Manager",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_manager():
    roles = frappe.get_roles(frappe.session.user)
    return any(r in roles for r in _PRIVILEGED_ROLES)


def _scope_get_user_scope(user=None):
    from ch_erp15.ch_erp15.scope import get_user_scope
    return get_user_scope(user)


def _scope_intersect_filters(company=None, city=None, zone=None, store=None, user=None):
    from ch_erp15.ch_erp15.scope import intersect_filters
    return intersect_filters(company=company, city=city, zone=zone, store=store, user=user)


def _current_user_executive():
    """Return the POS Executive linked to the logged-in user, or None."""
    return frappe.db.get_value("POS Executive", {"user": frappe.session.user}, "name")


# ── Frappe Report API ─────────────────────────────────────────────────────────

def execute(filters=None):
    filters = frappe._dict(filters or {})
    _apply_period_preset(filters)
    columns = get_columns(filters)
    data = get_data(filters)
    summary = get_report_summary(filters)
    return columns, data, None, None, summary


def _apply_period_preset(filters):
    preset = (filters.get("period_preset") or "Custom").strip()
    if preset == "Custom":
        return

    current = getdate(today())
    if preset == "MTD":
        filters["from_date"] = str(get_first_day(current))
        filters["to_date"] = str(current)
        return

    if preset == "Last Month":
        last_month_day = getdate(add_to_date(get_first_day(current), days=-1))
        filters["from_date"] = str(get_first_day(last_month_day))
        filters["to_date"] = str(get_last_day(last_month_day))
        return

    if preset == "This Quarter":
        q_start_month = ((current.month - 1) // 3) * 3 + 1
        quarter_start = getdate(f"{current.year}-{q_start_month:02d}-01")
        filters["from_date"] = str(quarter_start)
        filters["to_date"] = str(current)
        return


def get_columns(filters):
    view = filters.get("view", "Summary")
    if view == "Detail":
        return [
            {"label": _("Date"),            "fieldname": "posting_date",    "fieldtype": "Date",     "width": 100},
            {"label": _("Executive"),       "fieldname": "executive_name",  "fieldtype": "Data",     "width": 160},
            {"label": _("Invoice"),         "fieldname": "invoice",         "fieldtype": "Link",
             "options": "Sales Invoice",    "width": 160},
            {"label": _("Item"),            "fieldname": "item_name",       "fieldtype": "Data",     "width": 200},
            {"label": _("Brand"),           "fieldname": "brand",           "fieldtype": "Data",     "width": 100},
            {"label": _("Billing Amt"),     "fieldname": "billing_amount",  "fieldtype": "Currency", "width": 120},
            {"label": _("Slab"),            "fieldname": "incentive_slab",  "fieldtype": "Data",     "width": 140},
            {"label": _("Type"),            "fieldname": "incentive_type",  "fieldtype": "Data",     "width": 90},
            {"label": _("Value"),           "fieldname": "incentive_value", "fieldtype": "Float",    "width": 70},
            {"label": _("Incentive (₹)"),   "fieldname": "incentive_amount","fieldtype": "Currency", "width": 120},
            {"label": _("Status"),          "fieldname": "status",          "fieldtype": "Data",     "width": 90},
            {"label": _("Payout Month"),    "fieldname": "payout_month",    "fieldtype": "Data",     "width": 100},
        ]
    else:
        # Summary view
        return [
            {"label": _("Executive"),       "fieldname": "executive_name",  "fieldtype": "Data",     "width": 180},
            {"label": _("Store"),           "fieldname": "store",           "fieldtype": "Link",
             "options": "CH Store",         "width": 160},
            {"label": _("Billings"),        "fieldname": "billings",        "fieldtype": "Int",      "width": 80},
            {"label": _("Billing Amt (₹)"), "fieldname": "billing_amount",  "fieldtype": "Currency", "width": 140},
            {"label": _("Total Earned (₹)"),"fieldname": "total_incentive", "fieldtype": "Currency", "width": 140},
            {"label": _("Paid (₹)"),        "fieldname": "paid",            "fieldtype": "Currency", "width": 120},
            {"label": _("Pending (₹)"),     "fieldname": "pending",         "fieldtype": "Currency", "width": 120},
        ]


def get_data(filters):
    conditions, values = _build_conditions(filters)
    view = filters.get("view", "Summary")

    if view == "Detail":
        rows = frappe.db.sql("""
            SELECT
                il.posting_date,
                COALESCE(pe.executive_name, il.pos_executive) AS executive_name,
                il.invoice,
                il.item_name,
                il.brand,
                il.billing_amount,
                il.incentive_slab,
                il.incentive_type,
                il.incentive_value,
                il.incentive_amount,
                il.status,
                il.payout_month
            FROM `tabPOS Incentive Ledger` il
            LEFT JOIN `tabPOS Executive` pe ON pe.name = il.pos_executive
            WHERE {conditions}
            ORDER BY il.posting_date DESC, il.pos_executive
        """.format(conditions=conditions), values, as_dict=True)  # noqa: UP032
        return rows

    else:
        # Summary: group by executive
        rows = frappe.db.sql("""
            SELECT
                COALESCE(pe.executive_name, il.pos_executive) AS executive_name,
                il.store,
                COUNT(DISTINCT il.invoice)          AS billings,
                SUM(il.billing_amount)              AS billing_amount,
                SUM(il.incentive_amount)            AS total_incentive,
                SUM(CASE WHEN il.status = 'Paid'    THEN il.incentive_amount ELSE 0 END) AS paid,
                SUM(CASE WHEN il.status = 'Pending' THEN il.incentive_amount ELSE 0 END) AS pending
            FROM `tabPOS Incentive Ledger` il
            LEFT JOIN `tabPOS Executive` pe ON pe.name = il.pos_executive
            WHERE {conditions}
            GROUP BY il.pos_executive
            ORDER BY total_incentive DESC
        """.format(conditions=conditions), values, as_dict=True)  # noqa: UP032
        return rows


def get_report_summary(filters):
    conditions, values = _build_conditions(filters)
    totals = frappe.db.sql("""
        SELECT
            COUNT(DISTINCT il.invoice)                                          AS billings,
            SUM(il.billing_amount)                                              AS billing_amount,
            SUM(il.incentive_amount)                                            AS total_incentive,
            SUM(CASE WHEN il.transaction_type = 'Sale'    THEN il.incentive_amount ELSE 0 END) AS sales_incentive,
            SUM(CASE WHEN il.transaction_type = 'VAS'     THEN il.incentive_amount ELSE 0 END) AS vas_incentive,
            SUM(CASE WHEN il.transaction_type = 'Service' THEN il.incentive_amount ELSE 0 END) AS service_incentive,
            SUM(CASE WHEN il.status = 'Paid'    THEN il.incentive_amount ELSE 0 END) AS paid,
            SUM(CASE WHEN il.status = 'Pending' THEN il.incentive_amount ELSE 0 END) AS pending
        FROM `tabPOS Incentive Ledger` il
        LEFT JOIN `tabPOS Executive` pe ON pe.name = il.pos_executive
        WHERE {conditions}
    """.format(conditions=conditions), values, as_dict=True)  # noqa: UP032

    if not totals:
        return []

    t = totals[0]
    summary = [
        {"label": _("Total Billings"),    "value": int(t.billings or 0),            "datatype": "Int",      "indicator": "blue"},
        {"label": _("Billing Amount"),    "value": flt(t.billing_amount or 0),      "datatype": "Currency", "indicator": "blue"},
        {"label": _("Total Earned"),      "value": flt(t.total_incentive or 0),     "datatype": "Currency", "indicator": "green"},
        {"label": _("Sales Incentive"),   "value": flt(t.sales_incentive or 0),     "datatype": "Currency", "indicator": "blue"},
        {"label": _("VAS Incentive"),     "value": flt(t.vas_incentive or 0),       "datatype": "Currency", "indicator": "green"},
        {"label": _("Service Incentive"), "value": flt(t.service_incentive or 0),   "datatype": "Currency", "indicator": "purple"},
        {"label": _("Paid Out"),          "value": flt(t.paid or 0),                "datatype": "Currency", "indicator": "green"},
        {"label": _("Pending Payout"),    "value": flt(t.pending or 0),             "datatype": "Currency", "indicator": "orange"},
    ]

    summary.extend(_get_target_achievement_cards(filters, conditions, values))
    return summary


def _get_target_achievement_cards(filters, conditions, values):
    rows = frappe.db.sql(
        """
        SELECT il.pos_executive, il.store, SUM(il.billing_amount) AS achieved
        FROM `tabPOS Incentive Ledger` il
        WHERE {conditions}
        GROUP BY il.pos_executive, il.store
        """.format(conditions=conditions),
        values,
        as_dict=True,
    )

    achieved_by_store = {}
    executive_ids = set()
    for r in rows:
        if r.store:
            achieved_by_store[r.store] = achieved_by_store.get(r.store, 0.0) + flt(r.achieved)
        if r.pos_executive:
            executive_ids.add(r.pos_executive)

    exec_map = {}
    if executive_ids:
        for pe in frappe.get_all(
            "POS Executive",
            filters={"name": ("in", list(executive_ids))},
            fields=["name", "store", "sales_person"],
        ):
            exec_map[pe.name] = pe

    exec_sales_persons = {
        exec_map[eid].sales_person
        for eid in executive_ids
        if exec_map.get(eid) and exec_map[eid].sales_person
    }
    achieved_exec = sum(flt(r.achieved) for r in rows)
    target_exec = _compute_sales_target(
        sales_persons=exec_sales_persons,
        from_date=filters.get("from_date"),
        to_date=filters.get("to_date"),
        item_group=filters.get("item_group"),
        company=filters.get("company"),
    )

    asm_stores = _get_scope_store_union("Area Sales Manager (ASM)")
    zsm_stores = _get_scope_store_union("Zonal Sales Manager (ZSM)")

    achieved_asm = sum(v for s, v in achieved_by_store.items() if s in asm_stores)
    achieved_zsm = sum(v for s, v in achieved_by_store.items() if s in zsm_stores)

    asm_sales_persons = {
        pe.sales_person
        for pe in exec_map.values()
        if pe.sales_person and pe.store in asm_stores
    }
    zsm_sales_persons = {
        pe.sales_person
        for pe in exec_map.values()
        if pe.sales_person and pe.store in zsm_stores
    }

    target_asm = _compute_sales_target(
        sales_persons=asm_sales_persons,
        from_date=filters.get("from_date"),
        to_date=filters.get("to_date"),
        item_group=filters.get("item_group"),
        company=filters.get("company"),
    )
    target_zsm = _compute_sales_target(
        sales_persons=zsm_sales_persons,
        from_date=filters.get("from_date"),
        to_date=filters.get("to_date"),
        item_group=filters.get("item_group"),
        company=filters.get("company"),
    )

    def _percent(achieved, target):
        if flt(target) <= 0:
            return 0.0
        return flt((flt(achieved) / flt(target)) * 100, 2)

    return [
        {"label": _("Executive Target"), "value": flt(target_exec), "datatype": "Currency", "indicator": "blue"},
        {"label": _("Executive Achieved"), "value": flt(achieved_exec), "datatype": "Currency", "indicator": "green"},
        {"label": _("Executive Achievement %"), "value": _percent(achieved_exec, target_exec), "datatype": "Percent", "indicator": "green" if target_exec and achieved_exec >= target_exec else "orange"},
        {"label": _("ASM Target"), "value": flt(target_asm), "datatype": "Currency", "indicator": "blue"},
        {"label": _("ASM Achieved"), "value": flt(achieved_asm), "datatype": "Currency", "indicator": "green"},
        {"label": _("ASM Achievement %"), "value": _percent(achieved_asm, target_asm), "datatype": "Percent", "indicator": "green" if target_asm and achieved_asm >= target_asm else "orange"},
        {"label": _("ZSM Target"), "value": flt(target_zsm), "datatype": "Currency", "indicator": "blue"},
        {"label": _("ZSM Achieved"), "value": flt(achieved_zsm), "datatype": "Currency", "indicator": "green"},
        {"label": _("ZSM Achievement %"), "value": _percent(achieved_zsm, target_zsm), "datatype": "Percent", "indicator": "green" if target_zsm and achieved_zsm >= target_zsm else "orange"},
    ]


def _get_scope_store_union(scope_role):
    stores = set()
    try:
        users = frappe.get_all(
            "CH User Scope",
            filters={"enabled": 1, "scope_role": scope_role},
            pluck="user",
        )
    except Exception:
        return stores

    for user in users:
        try:
            scope = _scope_get_user_scope(user)
            stores.update(scope.get("stores") or set())
        except Exception:
            continue
    return stores


def _compute_sales_target(sales_persons, from_date, to_date, item_group=None, company=None):
    if not sales_persons:
        return 0.0

    from_dt = getdate(from_date)
    to_dt = getdate(to_date)
    target_rows = frappe.get_all(
        "Target Detail",
        filters={"parenttype": "Sales Person", "parent": ("in", list(sales_persons))},
        fields=["parent", "fiscal_year", "distribution_id", "target_amount", "item_group"],
    )
    if item_group:
        target_rows = [r for r in target_rows if (r.item_group or "") == item_group]

    period_cache = {}
    dist_cache = {}
    total = 0.0

    for row in target_rows:
        if not row.fiscal_year or not row.distribution_id:
            continue

        fy = frappe.db.get_value(
            "Fiscal Year",
            row.fiscal_year,
            ["year_start_date", "year_end_date"],
            as_dict=True,
        )
        if not fy:
            continue
        if getdate(fy.year_end_date) < from_dt or getdate(fy.year_start_date) > to_dt:
            continue

        period_key = (row.fiscal_year, company)
        if period_key not in period_cache:
            period_cache[period_key] = get_period_list(
                row.fiscal_year,
                row.fiscal_year,
                "",
                "",
                "Fiscal Year",
                "Monthly",
                company=company,
            )
        period_list = period_cache[period_key]

        dist_key = (row.distribution_id, row.fiscal_year, company)
        if dist_key not in dist_cache:
            dist_cache[dist_key] = get_periodwise_distribution_data(
                row.distribution_id,
                period_list,
                "Monthly",
            )
        dist_map = dist_cache[dist_key]

        for period in period_list:
            p_from = getdate(period.from_date)
            p_to = getdate(period.to_date)
            if p_to < from_dt or p_from > to_dt:
                continue

            month_target = flt(row.target_amount) * flt(dist_map.get(period.key, 0)) / 100
            overlap_from = max(p_from, from_dt)
            overlap_to = min(p_to, to_dt)
            overlap_days = (overlap_to - overlap_from).days + 1
            period_days = (p_to - p_from).days + 1
            total += month_target * (flt(overlap_days) / flt(period_days))

    return flt(total, 2)


def _build_conditions(filters):
    """Build SQL WHERE clause with hierarchical CH User Scope enforcement.

    Access model:
      - Privileged roles (Accounts/POS/System managers): full visibility + filters.
      - Scoped users (ASM/ZSM/store-scope personas): rows narrowed to allowed stores.
      - POS associates without scope: restricted to own POS Executive.
    """
    conditions = ["il.docstatus != 2"]  # exclude cancelled; includes draft (0) and submitted (1)
    values = {}

    # Date range — default to current calendar month
    from_date = filters.get("from_date") or frappe.utils.get_first_day(today())
    to_date = filters.get("to_date") or frappe.utils.get_last_day(today())
    conditions.append("il.posting_date BETWEEN %(from_date)s AND %(to_date)s")
    values["from_date"] = from_date
    values["to_date"] = to_date

    # Common optional filters for privileged users and scope-aware users.
    if filters.get("pos_executive"):
        conditions.append("il.pos_executive = %(pos_executive)s")
        values["pos_executive"] = filters["pos_executive"]

    if filters.get("company"):
        conditions.append("il.company = %(company)s")
        values["company"] = filters["company"]

    if filters.get("store"):
        conditions.append("il.store = %(store)s")
        values["store"] = filters["store"]

    if filters.get("city"):
        conditions.append(
            "EXISTS (SELECT 1 FROM `tabCH Store` st WHERE st.name = il.store AND st.city = %(city)s)"
        )
        values["city"] = filters["city"]

    if filters.get("zone"):
        conditions.append(
            "EXISTS (SELECT 1 FROM `tabCH Store` st WHERE st.name = il.store AND st.zone = %(zone)s)"
        )
        values["zone"] = filters["zone"]

    # Hierarchical scope restriction for non-privileged users.
    if not _is_manager():
        scoped_applied = False
        try:
            scope = _scope_get_user_scope(frappe.session.user)
            if scope and not scope.get("bypass"):
                effective = _scope_intersect_filters(
                    company=filters.get("company"),
                    city=filters.get("city"),
                    zone=filters.get("zone"),
                    store=filters.get("store"),
                    user=frappe.session.user,
                )
                allowed_stores = effective.get("allowed_stores") or []
                if not allowed_stores:
                    conditions.append("1 = 0")
                else:
                    conditions.append("il.store IN %(allowed_stores)s")
                    values["allowed_stores"] = tuple(allowed_stores)
                scoped_applied = True
        except Exception:
            # Scope API unavailable on sites without ch_erp15, fall back to own exec mode.
            scoped_applied = False

        if not scoped_applied:
            own_exec = _current_user_executive()
            if not own_exec:
                conditions.append("1 = 0")
            else:
                conditions.append("il.pos_executive = %(own_exec)s")
                values["own_exec"] = own_exec

    if filters.get("status"):
        conditions.append("il.status = %(status)s")
        values["status"] = filters["status"]

    if filters.get("brand"):
        conditions.append("il.brand = %(brand)s")
        values["brand"] = filters["brand"]

    if filters.get("transaction_type"):
        conditions.append("il.transaction_type = %(transaction_type)s")
        values["transaction_type"] = filters["transaction_type"]

    if filters.get("item_group"):
        conditions.append("il.item_group = %(item_group)s")
        values["item_group"] = filters["item_group"]

    return " AND ".join(conditions), values


# ── Filter definitions (shown in report UI) ───────────────────────────────────

def get_filters():
    is_mgr = _is_manager()
    filters = [
        {
            "fieldname": "period_preset",
            "label": _("Period Preset"),
            "fieldtype": "Select",
            "options": "Custom\nMTD\nLast Month\nThis Quarter",
            "default": "MTD",
        },
        {
            "fieldname": "from_date",
            "label": _("From Date"),
            "fieldtype": "Date",
            "default": frappe.utils.get_first_day(today()),
            "reqd": 1,
        },
        {
            "fieldname": "to_date",
            "label": _("To Date"),
            "fieldtype": "Date",
            "default": frappe.utils.get_last_day(today()),
            "reqd": 1,
        },
        {
            "fieldname": "view",
            "label": _("View"),
            "fieldtype": "Select",
            "options": "Summary\nDetail",
            "default": "Summary",
        },
        {
            "fieldname": "status",
            "label": _("Status"),
            "fieldtype": "Select",
            "options": "\nPending\nApproved\nPaid\nCancelled",
        },
    ]

    if is_mgr:
        filters += [
            {
                "fieldname": "pos_executive",
                "label": _("Executive"),
                "fieldtype": "Link",
                "options": "POS Executive",
            },
            {
                "fieldname": "store",
                "label": _("Store"),
                "fieldtype": "Link",
                "options": "CH Store",
            },
            {
                "fieldname": "city",
                "label": _("City"),
                "fieldtype": "Link",
                "options": "CH City",
            },
            {
                "fieldname": "zone",
                "label": _("Zone"),
                "fieldtype": "Link",
                "options": "CH Store Zone",
            },
            {
                "fieldname": "company",
                "label": _("Company"),
                "fieldtype": "Link",
                "options": "Company",
            },
            {
                "fieldname": "brand",
                "label": _("Brand"),
                "fieldtype": "Link",
                "options": "Brand",
            },
            {
                "fieldname": "item_group",
                "label": _("Category"),
                "fieldtype": "Link",
                "options": "Item Group",
            },
            {
                "fieldname": "transaction_type",
                "label": _("Transaction Type"),
                "fieldtype": "Select",
                "options": "\nSale\nService\nReturn\nExchange\nSwap\nWarranty\nVAS\nAccessory\nAttach Rate",
            },
        ]
    else:
        filters += [
            {
                "fieldname": "item_group",
                "label": _("Category"),
                "fieldtype": "Link",
                "options": "Item Group",
            },
            {
                "fieldname": "transaction_type",
                "label": _("Transaction Type"),
                "fieldtype": "Select",
                "options": "\nSale\nService\nReturn\nExchange\nSwap\nWarranty\nVAS\nAccessory\nAttach Rate",
            },
        ]

    return filters
