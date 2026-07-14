# Copyright (c) 2026, GoStack and contributors
"""CH Gift Redemption Register — accounts-team view of spin-wheel gifts.

One row per CH Gift Redemption:
  * lifecycle (issued → revealed → redeemed / expired) with dates
  * parent (trigger) invoice and the ₹0 redemption invoice
  * gift cost (COGS) = stock value issued on the redemption invoice, so
    outstanding liability (Revealed, not yet redeemed) and realised cost
    (Redeemed) are both visible.
"""

import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Gift"), "fieldname": "name", "fieldtype": "Link",
			"options": "CH Gift Redemption", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 90},
		{"label": _("Issued At"), "fieldname": "issued_at", "fieldtype": "Datetime", "width": 150},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data", "width": 140},
		{"label": _("Parent Invoice"), "fieldname": "parent_sales_invoice", "fieldtype": "Link",
			"options": "Sales Invoice", "width": 150},
		{"label": _("Parent Amount"), "fieldname": "parent_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Offer"), "fieldname": "offer", "fieldtype": "Link",
			"options": "CH Item Offer", "width": 120},
		{"label": _("Reward Item"), "fieldname": "reward_item_name", "fieldtype": "Data", "width": 170},
		{"label": _("Qty"), "fieldname": "reward_qty", "fieldtype": "Int", "width": 55},
		{"label": _("Revealed At"), "fieldname": "revealed_at", "fieldtype": "Datetime", "width": 150},
		{"label": _("Expires At"), "fieldname": "expires_at", "fieldtype": "Datetime", "width": 150},
		{"label": _("Redeemed At"), "fieldname": "redeemed_at", "fieldtype": "Datetime", "width": 150},
		{"label": _("Redemption Invoice"), "fieldname": "redeemed_invoice", "fieldtype": "Link",
			"options": "Sales Invoice", "width": 150},
		{"label": _("Redeemed Store"), "fieldname": "redeemed_store", "fieldtype": "Link",
			"options": "CH Store", "width": 130},
		{"label": _("Redeemed By"), "fieldname": "redeemed_by", "fieldtype": "Link",
			"options": "User", "width": 130},
		{"label": _("Gift Cost (COGS)"), "fieldname": "gift_cost", "fieldtype": "Currency", "width": 120},
	]


def get_data(filters):
	conditions = ["1=1"]
	values = {}

	if filters.get("company"):
		conditions.append("g.company = %(company)s")
		values["company"] = filters.company
	if filters.get("status"):
		conditions.append("g.status = %(status)s")
		values["status"] = filters.status
	if filters.get("store"):
		conditions.append("(g.store = %(store)s OR g.redeemed_store = %(store)s)")
		values["store"] = filters.store
	if filters.get("from_date"):
		conditions.append("DATE(g.issued_at) >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("DATE(g.issued_at) <= %(to_date)s")
		values["to_date"] = filters.to_date

	return frappe.db.sql(
		f"""
		SELECT
			g.name, g.status, g.issued_at, g.customer_name,
			g.parent_sales_invoice, psi.grand_total AS parent_amount,
			g.offer,
			COALESCE(NULLIF(g.reward_item_name, ''), g.reward_item) AS reward_item_name,
			g.reward_qty,
			g.revealed_at, g.expires_at, g.redeemed_at,
			g.redeemed_invoice, g.redeemed_store, g.redeemed_by,
			COALESCE(sle.cogs, 0) AS gift_cost
		FROM `tabCH Gift Redemption` g
		LEFT JOIN `tabSales Invoice` psi ON psi.name = g.parent_sales_invoice
		LEFT JOIN (
			SELECT voucher_no, item_code, SUM(-stock_value_difference) AS cogs
			FROM `tabStock Ledger Entry`
			WHERE is_cancelled = 0 AND voucher_type = 'Sales Invoice'
			GROUP BY voucher_no, item_code
		) sle ON sle.voucher_no = g.redeemed_invoice AND sle.item_code = g.reward_item
		WHERE {' AND '.join(conditions)}
		ORDER BY g.issued_at DESC
		""",
		values,
		as_dict=True,
	)
