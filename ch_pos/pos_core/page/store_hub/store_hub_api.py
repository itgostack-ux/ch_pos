"""Store Hub – Backend API for store operations dashboard."""

import frappe
from frappe.utils import flt, nowdate, get_first_day, cint, getdate


def _build_filters(company=None, store=None, from_date=None, to_date=None):
    prm = {}
    co = ""
    if company:
        co = " AND company = %(company)s"
        prm["company"] = company
    wh = ""
    if store:
        wh = " AND set_warehouse = %(store)s"
        prm["store"] = store
    from_date = str(getdate(from_date)) if from_date else None
    to_date = str(getdate(to_date)) if to_date else None
    if from_date:
        prm["from_date"] = from_date
    if to_date:
        prm["to_date"] = to_date

    def date_col(col):
        if from_date and to_date:
            return f" AND {col} BETWEEN %(from_date)s AND %(to_date)s"
        if from_date:
            return f" AND {col} >= %(from_date)s"
        if to_date:
            return f" AND {col} <= %(to_date)s"
        return ""

    return {"prm": prm, "co": co, "wh": wh, "date_col": date_col}


@frappe.whitelist()
def get_store_hub_data(company=None, store=None, from_date=None, to_date=None):
    """Store operations dashboard: POS Sessions → Daily Sales → Settlements → Cash → Inventory."""
    f = _build_filters(company, store, from_date, to_date)
    prm = f["prm"]
    co = f["co"]
    wh = f["wh"]
    dc = f["date_col"]

    today = nowdate()
    first_day = get_first_day(today)
    prm["today"] = today
    prm["first_day"] = str(first_day)

    # Store filter for session/settlement
    store_flt = ""
    if store:
        store_flt = " AND store = %(store)s"

    # ── Pipeline ──
    # Sessions today
    sessions_today = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabCH POS Session`
            WHERE DATE(shift_start) = %(today)s {store_flt} {co}""", prm
    )[0][0]

    open_sessions = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabCH POS Session`
            WHERE status = 'Open' {store_flt} {co}""", prm
    )[0][0]

    closed_sessions = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabCH POS Session`
            WHERE status = 'Closed' {store_flt} {co}
            {dc('DATE(shift_end)')}""", prm
    )[0][0]

    # POS Invoices today
    pos_today = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabPOS Invoice`
            WHERE docstatus=1 AND is_return=0 AND posting_date=%(today)s
            {co} {wh}""", prm
    )[0][0]

    # Pending settlements
    pending_settlements = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabCH POS Settlement`
            WHERE settlement_status='Pending' {store_flt} {co}""", prm
    )[0][0]

    # Returns today
    returns_today = frappe.db.sql(
        f"""SELECT COUNT(*) FROM `tabPOS Invoice`
            WHERE docstatus=1 AND is_return=1 AND posting_date=%(today)s
            {co} {wh}""", prm
    )[0][0]

    pipeline = [
        {"key": "sessions",     "label": "Open Sessions",   "count": cint(open_sessions),
         "icon": "desktop",      "color": "#0891b2",  "sub": f"Today: {cint(sessions_today)}"},
        {"key": "pos_txn",      "label": "POS Transactions","count": cint(pos_today),
         "icon": "shopping-bag","color": "#3b82f6",  "sub": "Today's invoices"},
        {"key": "closed",       "label": "Closed Sessions", "count": cint(closed_sessions),
         "icon": "lock",        "color": "#059669",  "sub": "Within period"},
        {"key": "settlements",  "label": "Pending Settle",  "count": cint(pending_settlements),
         "icon": "money",       "color": "#f59e0b",  "sub": "Awaiting approval"},
        {"key": "returns",      "label": "Returns Today",   "count": cint(returns_today),
         "icon": "undo",        "color": "#ef4444",  "sub": "Credit notes"},
    ]

    # ── KPIs ──
    today_rev = frappe.db.sql(
        f"""SELECT COALESCE(SUM(grand_total),0) FROM `tabPOS Invoice`
            WHERE docstatus=1 AND is_return=0 AND posting_date=%(today)s
            {co} {wh}""", prm
    )[0][0]

    mtd_rev = frappe.db.sql(
        f"""SELECT COALESCE(SUM(grand_total),0) FROM `tabPOS Invoice`
            WHERE docstatus=1 AND is_return=0
            AND posting_date BETWEEN %(first_day)s AND %(today)s
            {co} {wh}""", prm
    )[0][0]

    avg_ticket = flt(today_rev) / max(cint(pos_today), 1)

    # Payment mode split today
    cash_today = frappe.db.sql(
        f"""SELECT COALESCE(SUM(sip.amount),0)
            FROM `tabSales Invoice Payment` sip
            JOIN `tabPOS Invoice` pi ON pi.name = sip.parent
            WHERE pi.docstatus=1 AND pi.is_return=0 AND pi.posting_date=%(today)s
            AND sip.mode_of_payment = 'Cash'
            {co} {wh.replace('set_warehouse','pi.set_warehouse') if wh else ''}""", prm
    )[0][0]

    digital_today = frappe.db.sql(
        f"""SELECT COALESCE(SUM(sip.amount),0)
            FROM `tabSales Invoice Payment` sip
            JOIN `tabPOS Invoice` pi ON pi.name = sip.parent
            WHERE pi.docstatus=1 AND pi.is_return=0 AND pi.posting_date=%(today)s
            AND sip.mode_of_payment != 'Cash'
            {co} {wh.replace('set_warehouse','pi.set_warehouse') if wh else ''}""", prm
    )[0][0]

    # Variance from settlements
    total_variance = frappe.db.sql(
        f"""SELECT COALESCE(SUM(ABS(variance_amount)),0) FROM `tabCH POS Settlement`
            WHERE 1=1 {store_flt} {co} {dc('business_date')}""", prm
    )[0][0]

    kpis = [
        {"key": "rev_today",    "label": "Today Revenue",     "value": flt(today_rev),         "color": "#0891b2", "fmt": "currency"},
        {"key": "rev_mtd",      "label": "MTD Revenue",       "value": flt(mtd_rev),           "color": "#059669", "fmt": "currency"},
        {"key": "txns",         "label": "POS Txns Today",    "value": cint(pos_today),        "color": "#3b82f6", "fmt": "number"},
        {"key": "avg_ticket",   "label": "Avg Ticket",        "value": avg_ticket,             "color": "#6366f1", "fmt": "currency"},
        {"key": "cash_sales",   "label": "Cash Today",        "value": flt(cash_today),        "color": "#10b981", "fmt": "currency"},
        {"key": "digital_sales","label": "Digital Today",     "value": flt(digital_today),     "color": "#8b5cf6", "fmt": "currency"},
        {"key": "pend_settle",  "label": "Pending Settlements","value": cint(pending_settlements), "color": "#f59e0b", "fmt": "number"},
        {"key": "variance",     "label": "Cash Variance",     "value": flt(total_variance),    "color": "#ef4444", "fmt": "currency"},
    ]

    # ── Detail tables ──
    sessions = frappe.db.sql(
        f"""SELECT s.name, s.store, COALESCE(cs.store_name, s.store) AS store_name,
                   s.shift_start, s.shift_end, s.status,
                   total_sales, total_invoices, cash_variance
            FROM `tabCH POS Session` s
            LEFT JOIN `tabCH Store` cs ON cs.name = s.store
            WHERE 1=1 {store_flt} {co}
            ORDER BY shift_start DESC LIMIT 30""", prm, as_dict=True
    )

    settlements = frappe.db.sql(
        f"""SELECT st.name, st.store, COALESCE(cs.store_name, st.store) AS store_name,
                   st.business_date, st.settlement_status,
                   total_gross_sales, total_sales_cash, total_sales_card,
                   total_sales_upi, total_sales_wallet, variance_amount
            FROM `tabCH POS Settlement` st
            LEFT JOIN `tabCH Store` cs ON cs.name = st.store
            WHERE 1=1 {store_flt} {co} {dc('business_date')}
            ORDER BY business_date DESC LIMIT 30""", prm, as_dict=True
    )

    daily_summary = frappe.db.sql(
        f"""SELECT posting_date, set_warehouse AS warehouse,
                   COALESCE(cs.store_name, set_warehouse) AS warehouse_name,
                   COUNT(*) AS txn_count,
                   SUM(grand_total) AS revenue,
                   AVG(grand_total) AS avg_ticket
            FROM `tabPOS Invoice`
            LEFT JOIN `tabCH Store` cs ON cs.warehouse = set_warehouse
            WHERE docstatus=1 AND is_return=0
            {co} {wh} {dc('posting_date')}
            GROUP BY posting_date, set_warehouse, cs.store_name
            ORDER BY posting_date DESC LIMIT 30""", prm, as_dict=True
    )

    top_items = frappe.db.sql(
        f"""SELECT pii.item_code, pii.item_name,
                   SUM(pii.qty) AS qty,
                   SUM(pii.amount) AS revenue
            FROM `tabPOS Invoice Item` pii
            JOIN `tabPOS Invoice` pi ON pi.name = pii.parent
            WHERE pi.docstatus=1 AND pi.is_return=0
            {co} {wh.replace('set_warehouse','pi.set_warehouse') if wh else ''} {dc('pi.posting_date')}
            GROUP BY pii.item_code, pii.item_name
            ORDER BY revenue DESC LIMIT 20""", prm, as_dict=True
    )

    # Stock alerts — items below reorder level in selected store
    stock_alerts = []
    if store:
        stock_alerts = frappe.db.sql(
            """SELECT b.item_code, i.item_name, b.warehouse,
                      COALESCE(cs.store_name, b.warehouse) AS warehouse_name,
                      b.actual_qty, ir.warehouse_reorder_level AS reorder_level
               FROM `tabBin` b
               JOIN `tabItem` i ON i.name = b.item_code
               LEFT JOIN `tabCH Store` cs ON cs.warehouse = b.warehouse
               LEFT JOIN `tabItem Reorder` ir ON ir.parent = b.item_code AND ir.warehouse = b.warehouse
               WHERE b.warehouse = %(store)s
               AND b.actual_qty <= COALESCE(ir.warehouse_reorder_level, 0)
               AND COALESCE(ir.warehouse_reorder_level, 0) > 0
               ORDER BY b.actual_qty ASC LIMIT 20""", prm, as_dict=True
        )

    # ── Kiosk Queue ──
    kiosk_tokens = frappe.db.sql(
        f"""SELECT name, token_display, store, customer_name, customer_phone,
                   status, visit_purpose, visit_source, expires_at, creation
            FROM `tabPOS Kiosk Token`
            WHERE 1=1 {store_flt} {co} {dc('DATE(creation)')}
            ORDER BY creation DESC LIMIT 30""", prm, as_dict=True
    )

    # ── Cash Drops ──
    cash_drops = frappe.db.sql(
        f"""SELECT cd.name, cd.store, COALESCE(cs.store_name, cd.store) AS store_name,
                   cd.session, cd.business_date, cd.movement_type,
                   amount, status, user, reason, approved_by
            FROM `tabCH Cash Drop` cd
            LEFT JOIN `tabCH Store` cs ON cs.name = cd.store
            WHERE 1=1 {store_flt} {co} {dc('business_date')}
            ORDER BY business_date DESC, creation DESC LIMIT 30""", prm, as_dict=True
    )

    # ── Incentive Tracker ──
    incentives = frappe.db.sql(
        f"""SELECT pos_executive, executive_name, store, posting_date,
                   item_name, brand, qty, billing_amount, incentive_amount,
                   incentive_type, status, payout_month
            FROM `tabPOS Incentive Ledger`
            WHERE 1=1 {store_flt} {co} {dc('posting_date')}
            ORDER BY posting_date DESC LIMIT 30""", prm, as_dict=True
    )

    # ── Audit Log ──
    audit_logs = frappe.db.sql(
        f"""SELECT al.name, al.event_type, al.reference_doctype, al.reference_name,
               al.store, COALESCE(cs.store_name, al.store) AS store_name,
               al.user, al.timestamp, al.remarks
            FROM `tabCH Business Audit Log` al
            LEFT JOIN `tabCH Store` cs ON cs.name = al.store
            WHERE 1=1 {store_flt} {co} {dc('DATE(timestamp)')}
            ORDER BY timestamp DESC LIMIT 30""", prm, as_dict=True
    )

    # ── AI Insights ──
    ai_insights = []
    if cint(open_sessions) > 0 and frappe.utils.now_datetime().hour >= 21:
        ai_insights.append({
            "severity": "High",
            "title": f"{cint(open_sessions)} Sessions Still Open",
            "detail": "It's late and sessions haven't been closed. Ensure end-of-day procedures are completed.",
            "action": "Close all open POS sessions and complete settlements."
        })
    if cint(pending_settlements) > 2:
        ai_insights.append({
            "severity": "High",
            "title": f"{cint(pending_settlements)} Pending Settlements",
            "detail": "Multiple settlements awaiting approval. Cash reconciliation may be delayed.",
            "action": "Review and approve pending settlements."
        })
    if flt(total_variance) > 500:
        ai_insights.append({
            "severity": "Medium",
            "title": f"Cash Variance: ₹{flt(total_variance):,.0f}",
            "detail": "Significant cash variance detected across settlements.",
            "action": "Investigate variance sources and tighten cash handling procedures."
        })
    # Kiosk queue insight
    waiting_tokens = sum(1 for t in kiosk_tokens if t.get("status") in ("Waiting", "In Queue"))
    if waiting_tokens > 5:
        ai_insights.append({
            "severity": "Medium",
            "title": f"{waiting_tokens} Customers Waiting in Kiosk Queue",
            "detail": "High kiosk queue. Consider assigning more staff to reduce wait times.",
            "action": "Review staffing levels and assign executives."
        })
    if not ai_insights:
        ai_insights.append({
            "severity": "Low",
            "title": "Store Operations Running Smoothly",
            "detail": "No significant issues detected. All systems operating normally.",
        })

    financial_control = {
        "today_sales": flt(today_rev),
        "cash_in_hand": flt(cash_today),
        "pending_settlements": cint(pending_settlements),
        "variance": flt(total_variance),
    }

    return {
        "pipeline": pipeline,
        "kpis": kpis,
        "sessions": sessions,
        "settlements": settlements,
        "daily_summary": daily_summary,
        "top_items": top_items,
        "stock_alerts": stock_alerts,
        "kiosk_tokens": kiosk_tokens,
        "cash_drops": cash_drops,
        "incentives": incentives,
        "audit_logs": audit_logs,
        "ai_insights": ai_insights,
        "financial_control": financial_control,
    }
