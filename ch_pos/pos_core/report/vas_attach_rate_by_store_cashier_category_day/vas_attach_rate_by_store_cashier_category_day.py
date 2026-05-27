import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	columns = _get_columns()
	data = _get_data(filters)
	return columns, data


def _get_columns():
	return [
		{"label": _("Date"), "fieldname": "day", "fieldtype": "Date", "width": 110},
		{"label": _("Store"), "fieldname": "store", "fieldtype": "Link", "options": "Warehouse", "width": 170},
		{"label": _("Cashier"), "fieldname": "cashier", "fieldtype": "Link", "options": "User", "width": 170},
		{"label": _("Category"), "fieldname": "category", "fieldtype": "Link", "options": "CH Category", "width": 170},
		{"label": _("Offers"), "fieldname": "offered_count", "fieldtype": "Int", "width": 90},
		{"label": _("Accepts"), "fieldname": "accepted_count", "fieldtype": "Int", "width": 90},
		{"label": _("Attach Rate %"), "fieldname": "attach_rate", "fieldtype": "Percent", "width": 120},
	]


def _get_data(filters):
	conditions = ["l.attach_type = 'VAS'"]
	values = {}

	if filters.get("from_date"):
		conditions.append("DATE(COALESCE(l.offered_at, l.creation)) >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("DATE(COALESCE(l.offered_at, l.creation)) <= %(to_date)s")
		values["to_date"] = filters["to_date"]
	if filters.get("pos_profile"):
		conditions.append("l.pos_profile = %(pos_profile)s")
		values["pos_profile"] = filters["pos_profile"]
	if filters.get("cashier"):
		conditions.append("l.offered_by = %(cashier)s")
		values["cashier"] = filters["cashier"]
	if filters.get("category"):
		conditions.append("i.ch_category = %(category)s")
		values["category"] = filters["category"]
	if filters.get("company"):
		conditions.append("si.company = %(company)s")
		values["company"] = filters["company"]

	where_sql = " AND ".join(conditions) if conditions else "1=1"

	return frappe.db.sql(
		f"""
		SELECT
			DATE(COALESCE(l.offered_at, l.creation)) AS day,
			COALESCE(pp.warehouse, '') AS store,
			l.offered_by AS cashier,
			i.ch_category AS category,
			SUM(CASE WHEN l.action = 'Offered' THEN 1 ELSE 0 END) AS offered_count,
			SUM(CASE WHEN l.action = 'Accepted' THEN 1 ELSE 0 END) AS accepted_count,
			CASE
				WHEN SUM(CASE WHEN l.action = 'Offered' THEN 1 ELSE 0 END) = 0 THEN 0
				ELSE ROUND(
					SUM(CASE WHEN l.action = 'Accepted' THEN 1 ELSE 0 END)
					* 100.0
					/ SUM(CASE WHEN l.action = 'Offered' THEN 1 ELSE 0 END),
					2
				)
			END AS attach_rate
		FROM `tabCH Attach Log` l
		LEFT JOIN `tabItem` i ON i.name = l.item_code
		LEFT JOIN `tabPOS Profile` pp ON pp.name = l.pos_profile
		LEFT JOIN `tabSales Invoice` si ON si.name = l.pos_invoice
		WHERE {where_sql}
		GROUP BY DATE(COALESCE(l.offered_at, l.creation)), COALESCE(pp.warehouse, ''), l.offered_by, i.ch_category
		ORDER BY day DESC, store, cashier
		""",
		values,
		as_dict=True,
	)
