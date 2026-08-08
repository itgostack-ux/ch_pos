"""Attach historical custom-POS Sales Invoices to standard ERPNext closings."""

import frappe
from frappe.utils import flt


def execute():
	if not frappe.db.has_column("Sales Invoice", "custom_ch_pos_session"):
		return
	frappe.db.sql(
		"""
		UPDATE `tabSales Invoice`
		   SET is_created_using_pos = 1
		 WHERE docstatus = 1 AND is_pos = 1
		   AND IFNULL(custom_ch_pos_session, '') != ''
		"""
	)

	_reconstruct_missing_openings()
	rows = frappe.db.sql(
		"""
		SELECT si.name, si.posting_date, si.customer, si.grand_total, si.is_return,
		       si.return_against, si.total_qty, si.net_total, si.total_taxes_and_charges,
		       s.name AS session, s.pos_opening_entry, poe.pos_closing_entry
		  FROM `tabSales Invoice` si
		  JOIN `tabCH POS Session` s ON s.name = si.custom_ch_pos_session
		  JOIN `tabPOS Opening Entry` poe ON poe.name = s.pos_opening_entry
		  JOIN `tabPOS Closing Entry` pce ON pce.name = poe.pos_closing_entry
		 WHERE si.docstatus = 1 AND si.is_pos = 1 AND si.is_created_using_pos = 1
		""",
		as_dict=True,
	)
	by_closing = {}
	for row in rows:
		by_closing.setdefault(row.pos_closing_entry, []).append(row)
		frappe.db.set_value(
			"Sales Invoice", row.name, "pos_closing_entry", row.pos_closing_entry,
			update_modified=False,
		)
		if not frappe.db.exists(
			"Sales Invoice Reference",
			{"parent": row.pos_closing_entry, "parentfield": "sales_invoices", "sales_invoice": row.name},
		):
			frappe.get_doc({
				"doctype": "Sales Invoice Reference",
				"parent": row.pos_closing_entry,
				"parenttype": "POS Closing Entry",
				"parentfield": "sales_invoices",
				"sales_invoice": row.name,
				"posting_date": row.posting_date,
				"customer": row.customer,
				"grand_total": row.grand_total,
				"is_return": row.is_return,
				"return_against": row.return_against,
			}).insert(ignore_permissions=True)

	for closing_name, invoices in by_closing.items():
		_reconcile_closing(closing_name, invoices)


def _reconstruct_missing_openings():
	sessions = frappe.db.sql(
		"""
		SELECT DISTINCT s.name
		  FROM `tabCH POS Session` s
		  JOIN `tabSales Invoice` si ON si.custom_ch_pos_session = s.name
		 WHERE s.docstatus = 1 AND s.status = 'Closed'
		   AND IFNULL(s.pos_opening_entry, '') = ''
		   AND si.docstatus = 1 AND si.is_pos = 1
		""",
		pluck=True,
	)
	for name in sessions:
		session = frappe.get_doc("CH POS Session", name)
		profile = frappe.get_doc("POS Profile", session.pos_profile)
		opening = frappe.new_doc("POS Opening Entry")
		opening.pos_profile = session.pos_profile
		opening.company = session.company
		opening.user = session.user
		opening.period_start_date = session.shift_start or session.creation
		for payment in profile.payments:
			is_cash = frappe.db.get_value("Mode of Payment", payment.mode_of_payment, "type") == "Cash"
			opening.append("balance_details", {
				"mode_of_payment": payment.mode_of_payment,
				"opening_amount": flt(session.opening_cash) if is_cash else 0,
			})
		opening.flags.ignore_permissions = True
		opening.insert(ignore_permissions=True)
		opening.submit()
		frappe.db.set_value(
			"CH POS Session", session.name, "pos_opening_entry", opening.name,
			update_modified=False,
		)
		session.reload()
		session._mirror_close_to_opening_entry()


def _reconcile_closing(closing_name, invoices):
	closing = frappe.db.get_value(
		"POS Closing Entry", closing_name, ["pos_opening_entry"], as_dict=True
	)
	opening = frappe.get_doc("POS Opening Entry", closing.pos_opening_entry)
	session = frappe.db.get_value(
		"CH POS Session", {"pos_opening_entry": opening.name},
		["name", "closing_cash_actual", "total_cash_drops"], as_dict=True,
	)
	invoice_names = tuple(row.name for row in invoices)
	payment_totals = {
		row.mode_of_payment: flt(row.amount)
		for row in frappe.db.sql(
			"""SELECT mode_of_payment, SUM(amount) amount
			     FROM `tabSales Invoice Payment`
			    WHERE parent IN %(invoices)s GROUP BY mode_of_payment""",
			{"invoices": invoice_names}, as_dict=True,
		)
	}
	opening_totals = {row.mode_of_payment: flt(row.opening_amount) for row in opening.balance_details}
	counted = {}
	if session:
		counted = {
			row.mode_of_payment: flt(row.counted_amount)
			for row in frappe.get_all(
				"POS Closing Payment Detail",
				filters={"parent": session.name, "parentfield": "payment_details"},
				fields=["mode_of_payment", "counted_amount"],
			)
		}
	existing = {
		row.mode_of_payment: row
		for row in frappe.get_all(
			"POS Closing Entry Detail",
			filters={"parent": closing_name, "parentfield": "payment_reconciliation"},
			fields=["name", "mode_of_payment"],
		)
	}
	cash_recorded = False
	for idx, mode in enumerate(sorted(set(opening_totals) | set(payment_totals)), 1):
		expected = opening_totals.get(mode, 0) + payment_totals.get(mode, 0)
		is_cash = frappe.db.get_value("Mode of Payment", mode, "type") == "Cash"
		if is_cash and session and not cash_recorded:
			closing_amount = flt(session.closing_cash_actual) + flt(session.total_cash_drops)
			cash_recorded = True
		else:
			closing_amount = counted.get(mode, expected)
		values = {
			"opening_amount": opening_totals.get(mode, 0),
			"expected_amount": expected,
			"closing_amount": closing_amount,
			"difference": closing_amount - expected,
			"idx": idx,
		}
		if mode in existing:
			frappe.db.set_value(
				"POS Closing Entry Detail", existing[mode].name, values, update_modified=False
			)
		else:
			frappe.get_doc({
				"doctype": "POS Closing Entry Detail",
				"parent": closing_name,
				"parenttype": "POS Closing Entry",
				"parentfield": "payment_reconciliation",
				"mode_of_payment": mode,
				**values,
			}).insert(ignore_permissions=True)

	frappe.db.set_value(
		"POS Closing Entry", closing_name,
		{
			"grand_total": sum(flt(row.grand_total) for row in invoices),
			"net_total": sum(flt(row.net_total) for row in invoices),
			"total_quantity": sum(flt(row.total_qty) for row in invoices),
			"total_taxes_and_charges": sum(flt(row.total_taxes_and_charges) for row in invoices),
		},
		update_modified=False,
	)
