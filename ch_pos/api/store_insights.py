"""CH POS — Store Insights (pure Python, DB-driven).

Generates the "Insights & Recommendations" cards on the POS Store Dashboard
WITHOUT any external LLM / API call. Every insight is a deterministic,
rule-based reading of this site's own database, so it is fast, free, private
and always available.

Each insight is a plain dict:
    {
        "severity": "Critical|High|Medium|Low|Info",
        "icon":     "fa-...",                 # FontAwesome glyph
        "title":    "short headline",
        "detail":   "one-line explanation",   # never contains raw None
        "metric":   "₹12,500" | "5" | "",     # optional headline figure
        "ref_doctype": "Stock Entry" | None,  # optional deep link
        "ref_name":    "MAT-STE-00007" | None,
        "href":        "/desk/..." | None,     # optional external link
    }
"""

from __future__ import annotations

from urllib.parse import quote

import frappe
from frappe.utils import flt, cint, nowdate, now_datetime, fmt_money

# Severity ordering — lower rank surfaces first.
_SEV_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Info": 4}

# Tunable thresholds (kept conservative so cards stay meaningful).
_LOW_STOCK_QTY = 5
_RETURN_RATE_ALERT = 0.15          # > 15% of bills are returns
_AGING_RATIO_ALERT = 0.20          # > 20% of stock value is aged
_NO_SALES_HOUR = 11                # flag a dry morning only after 11:00
_STALE_HOURS = 24                  # drafts / requests older than this are "stuck"
_MAX_CARDS = 6


def _short_wh(warehouse: str | None) -> str:
    """Warehouse names look like 'Main Store - GG'; show the readable part."""
    if not warehouse:
        return "—"
    return warehouse.rsplit(" - ", 1)[0]


def _inr(value) -> str:
    return "₹" + fmt_money(flt(value), precision=0, currency="INR").replace("₹", "").strip()


@frappe.whitelist()
def store_insights(pos_profile: str) -> dict:
    """Return prioritised, DB-derived store insights for the dashboard panel."""
    profile = frappe.get_cached_doc("POS Profile", pos_profile)
    warehouse = profile.warehouse
    today = nowdate()
    now_dt = now_datetime()

    insights: list[dict] = []

    # ── Today's sales snapshot ──────────────────────────────────────────
    sales = frappe.db.sql(
        """SELECT COUNT(*) AS bills, COALESCE(SUM(grand_total), 0) AS revenue
           FROM `tabSales Invoice`
           WHERE pos_profile = %(pp)s AND posting_date = %(d)s
             AND docstatus = 1 AND is_return = 0""",
        {"pp": pos_profile, "d": today},
        as_dict=True,
    )[0]
    bills = cint(sales.bills)
    revenue = flt(sales.revenue)

    returns = frappe.db.count(
        "Sales Invoice",
        {"pos_profile": pos_profile, "posting_date": today, "docstatus": 1, "is_return": 1},
    )

    # 1) No sales yet (and the day is well underway)
    if bills == 0 and now_dt.hour >= _NO_SALES_HOUR:
        insights.append({
            "severity": "High",
            "icon": "fa-shopping-cart",
            "title": "No sales billed yet today",
            "detail": f"It's {now_dt.strftime('%I:%M %p').lstrip('0')} and no invoice has been "
                      "raised. Confirm a counter session is open and the till is ready.",
            "metric": "0 bills",
        })

    # 2) High return rate
    if bills >= 5 and returns and (returns / bills) > _RETURN_RATE_ALERT:
        pct = round(returns / bills * 100)
        insights.append({
            "severity": "Medium",
            "icon": "fa-undo",
            "title": "Return rate is high today",
            "detail": f"{returns} of {bills} bills were returns. Review return reasons with "
                      "the team to protect today's net sales.",
            "metric": f"{pct}%",
        })

    # 3) Best-selling item today (positive signal)
    if revenue > 0:
        top = frappe.db.sql(
            """SELECT ii.item_name, SUM(ii.qty) AS qty, SUM(ii.amount) AS amt
               FROM `tabSales Invoice Item` ii
               JOIN `tabSales Invoice` si ON si.name = ii.parent
               WHERE si.pos_profile = %(pp)s AND si.posting_date = %(d)s
                 AND si.docstatus = 1 AND si.is_return = 0
               GROUP BY ii.item_code ORDER BY amt DESC LIMIT 1""",
            {"pp": pos_profile, "d": today},
            as_dict=True,
        )
        if top and top[0].item_name:
            t = top[0]
            insights.append({
                "severity": "Info",
                "icon": "fa-trophy",
                "title": f"Top seller: {t.item_name}",
                "detail": f"{cint(t.qty)} units sold today for {_inr(t.amt)} — your strongest "
                          "performer. Keep it stocked and in view.",
                "metric": _inr(t.amt),
            })

    # ── Stock health (warehouse scoped) ─────────────────────────────────
    if warehouse:
        # 4) Fast movers now out of stock (sold in last 30d, qty <= 0 now)
        oos = frappe.db.sql(
            """SELECT sii.item_code, MAX(sii.item_name) AS item_name,
                      SUM(sii.qty) AS sold_qty
               FROM `tabSales Invoice Item` sii
               JOIN `tabSales Invoice` si ON si.name = sii.parent
               LEFT JOIN `tabBin` b
                      ON b.item_code = sii.item_code AND b.warehouse = %(wh)s
               WHERE si.pos_profile = %(pp)s AND si.docstatus = 1 AND si.is_return = 0
                 AND si.posting_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
               GROUP BY sii.item_code
               HAVING COALESCE(MAX(b.actual_qty), 0) <= 0
               ORDER BY sold_qty DESC LIMIT 5""",
            {"wh": warehouse, "pp": pos_profile},
            as_dict=True,
        )
        if oos:
            names = ", ".join(r.item_name for r in oos[:3] if r.item_name)
            more = f" +{len(oos) - 3} more" if len(oos) > 3 else ""
            insights.append({
                "severity": "High",
                "icon": "fa-exclamation-triangle",
                "title": f"{len(oos)} fast-mover{'s' if len(oos) > 1 else ''} out of stock",
                "detail": f"{names}{more} sold recently but {_short_wh(warehouse)} now shows "
                          "zero stock. Raise a transfer to avoid lost sales.",
                "metric": str(len(oos)),
                "ref_doctype": "Item",
                "ref_name": oos[0].item_code,
            })

        # 5) Items running low (1..5 in hand)
        low_count = frappe.db.sql(
            """SELECT COUNT(*) FROM `tabBin` b
               JOIN `tabItem` i ON i.name = b.item_code
               WHERE b.warehouse = %(wh)s AND b.actual_qty > 0
                 AND b.actual_qty <= %(thr)s AND i.disabled = 0
                 AND i.is_stock_item = 1""",
            {"wh": warehouse, "thr": _LOW_STOCK_QTY},
        )[0][0]
        low_count = cint(low_count)
        if low_count:
            insights.append({
                "severity": "Medium",
                "icon": "fa-battery-quarter",
                "title": f"{low_count} item{'s' if low_count > 1 else ''} running low",
                "detail": f"{low_count} line{'s' if low_count > 1 else ''} have "
                          f"{_LOW_STOCK_QTY} or fewer units left at {_short_wh(warehouse)}. "
                          "Replenish before they sell out.",
                "metric": str(low_count),
                "href": "/desk/bin?warehouse=" + quote(warehouse),
            })

        # 6) Aging stock value (no inward movement in 90 days)
        sv = frappe.db.sql(
            """SELECT COALESCE(SUM(b.actual_qty * b.valuation_rate), 0) AS total,
                      COALESCE(SUM(CASE WHEN NOT EXISTS (
                          SELECT 1 FROM `tabStock Ledger Entry` sle
                          WHERE sle.warehouse = b.warehouse
                            AND sle.item_code = b.item_code
                            AND sle.actual_qty > 0 AND sle.is_cancelled = 0
                            AND sle.posting_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
                      ) THEN b.actual_qty * b.valuation_rate ELSE 0 END), 0) AS aged
               FROM `tabBin` b
               WHERE b.warehouse = %(wh)s AND b.actual_qty > 0""",
            {"wh": warehouse},
            as_dict=True,
        )[0]
        stock_total = flt(sv.total)
        aged = flt(sv.aged)
        if stock_total > 0 and aged > 0 and (aged / stock_total) > _AGING_RATIO_ALERT:
            pct = round(aged / stock_total * 100)
            insights.append({
                "severity": "Medium",
                "icon": "fa-hourglass-half",
                "title": f"{_inr(aged)} tied up in aging stock",
                "detail": f"{pct}% of stock value at {_short_wh(warehouse)} hasn't moved in "
                          "90+ days. A clearance push frees up cash and shelf space.",
                "metric": _inr(aged),
            })

        # 7) Transfers stuck in draft > 24h (with REAL warehouse names)
        stale_st = frappe.db.sql(
            """SELECT se.name,
                      COALESCE(NULLIF(se.from_warehouse, ''),
                          (SELECT sed.s_warehouse FROM `tabStock Entry Detail` sed
                           WHERE sed.parent = se.name AND IFNULL(sed.s_warehouse, '') != ''
                           ORDER BY sed.idx LIMIT 1)) AS from_wh,
                      COALESCE(NULLIF(se.to_warehouse, ''),
                          (SELECT sed.t_warehouse FROM `tabStock Entry Detail` sed
                           WHERE sed.parent = se.name AND IFNULL(sed.t_warehouse, '') != ''
                           ORDER BY sed.idx LIMIT 1)) AS to_wh,
                      TIMESTAMPDIFF(HOUR, se.creation, NOW()) AS age_h
               FROM `tabStock Entry` se
               WHERE se.stock_entry_type = 'Material Transfer' AND se.docstatus = 0
                 AND se.creation <= DATE_SUB(NOW(), INTERVAL %(h)s HOUR)
                 AND (se.from_warehouse = %(wh)s OR se.to_warehouse = %(wh)s
                      OR EXISTS (SELECT 1 FROM `tabStock Entry Detail` sed
                                 WHERE sed.parent = se.name
                                   AND (sed.s_warehouse = %(wh)s OR sed.t_warehouse = %(wh)s)))
               ORDER BY se.creation ASC LIMIT 5""",
            {"wh": warehouse, "h": _STALE_HOURS},
            as_dict=True,
        )
        if stale_st:
            first = stale_st[0]
            route = f"{_short_wh(first.from_wh)} → {_short_wh(first.to_wh)}"
            insights.append({
                "severity": "High",
                "icon": "fa-truck",
                "title": f"{len(stale_st)} transfer{'s' if len(stale_st) > 1 else ''} stuck in draft",
                "detail": f"{first.name} ({route}) has sat in draft for {cint(first.age_h)}h. "
                          "Submit or cancel pending transfers to keep stock accurate.",
                "metric": f"{cint(first.age_h)}h",
                "ref_doctype": "Stock Entry",
                "ref_name": first.name,
            })

        # 8) Material requests awaiting fulfilment > 24h
        pending_mr = frappe.db.sql(
            """SELECT mr.name, TIMESTAMPDIFF(HOUR, mr.creation, NOW()) AS age_h
               FROM `tabMaterial Request` mr
               WHERE mr.docstatus = 1 AND mr.status = 'Pending'
                 AND mr.creation <= DATE_SUB(NOW(), INTERVAL %(h)s HOUR)
                 AND EXISTS (SELECT 1 FROM `tabMaterial Request Item` mri
                             WHERE mri.parent = mr.name AND mri.warehouse = %(wh)s)
               ORDER BY mr.creation ASC LIMIT 5""",
            {"wh": warehouse, "h": _STALE_HOURS},
            as_dict=True,
        )
        if pending_mr:
            first = pending_mr[0]
            insights.append({
                "severity": "Medium",
                "icon": "fa-clipboard",
                "title": f"{len(pending_mr)} stock request{'s' if len(pending_mr) > 1 else ''} awaiting action",
                "detail": f"{first.name} has been pending for {cint(first.age_h)}h with no "
                          "fulfilment. Follow up so the floor gets restocked on time.",
                "metric": str(len(pending_mr)),
                "ref_doctype": "Material Request",
                "ref_name": first.name,
            })

        # 9) Reserved pickups awaiting collection (reserve-stock Sales Orders)
        reserved = frappe.db.sql(
            """SELECT COUNT(DISTINCT so.name)
               FROM `tabSales Order` so
               JOIN `tabSales Order Item` soi ON soi.parent = so.name
               WHERE so.docstatus = 1 AND IFNULL(so.reserve_stock, 0) = 1
                 AND so.status NOT IN ('Closed', 'Completed', 'Cancelled')
                 AND soi.warehouse = %(wh)s""",
            {"wh": warehouse},
        )[0][0]
        reserved = cint(reserved)
        if reserved:
            insights.append({
                "severity": "Low",
                "icon": "fa-bookmark",
                "title": f"{reserved} reserved pickup{'s' if reserved > 1 else ''} awaiting collection",
                "detail": f"{reserved} order{'s' if reserved > 1 else ''} hold reserved stock for "
                          "pickup/bill. Nudge customers so the units don't stay locked.",
                "metric": str(reserved),
            })

    # ── Rank & trim ─────────────────────────────────────────────────────
    insights.sort(key=lambda c: _SEV_RANK.get(c.get("severity"), 9))
    healthy = not any(c["severity"] in ("Critical", "High", "Medium") for c in insights)

    return {
        "insights": insights[:_MAX_CARDS],
        "healthy": healthy,
        "generated_on": now_dt.strftime("%I:%M %p").lstrip("0"),
    }
