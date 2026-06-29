# Copyright (c) 2026, GoStack and contributors
# Phase 2 — One-click multi-channel receipt sharing.
#
# Reuse-first audit findings:
#   - PDF rendering uses frappe.utils.print_format.download_pdf  (no rewrite).
#   - WhatsApp uses ch_item_master.ch_core.whatsapp.send_template_message
#     (Gallabox integration; already deduped + enqueued).
#   - Email uses frappe.sendmail with attached PDF (built-in).
#   - E-invoice IRN generation is owned by India Compliance — we only
#     trigger it best-effort if no IRN is present yet.

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import get_url


_DEFAULT_FORMAT = "Custom Sales Invoice"
_GOFIX_FORMAT = "GoFix Service Invoice"
_VALID_CHANNELS = {"print", "email", "whatsapp", "einvoice"}


def _resolve_print_format(invoice_name: str) -> str:
	"""GoFix invoices use a different print format — mirror payment_dialog logic."""
	try:
		sr = frappe.db.get_value("Sales Invoice", invoice_name, "custom_gofix_service_request")
	except Exception:
		sr = None
	return _GOFIX_FORMAT if sr else _DEFAULT_FORMAT


def _pdf_download_url(invoice_name: str, print_format: str) -> str:
	"""Return a fully-qualified PDF download URL (server-rendered, headered)."""
	return (
		f"/api/method/frappe.utils.print_format.download_pdf"
		f"?doctype=Sales+Invoice"
		f"&name={frappe.utils.escape_html(invoice_name)}"
		f"&format={frappe.utils.escape_html(print_format)}"
		f"&no_letterhead=0"
	)


def _generate_pdf_bytes(invoice_name: str, print_format: str) -> bytes:
	"""Render a Sales Invoice to a PDF byte string using Frappe's print pipeline."""
	pdf_bytes = frappe.get_print(
		doctype="Sales Invoice",
		name=invoice_name,
		print_format=print_format,
		as_pdf=True,
		no_letterhead=False,
	)
	return pdf_bytes


def _share_print(invoice_name: str, fmt: str) -> dict:
	return {
		"success": True,
		"pdf_url": _pdf_download_url(invoice_name, fmt),
		"print_format": fmt,
	}


def _share_email(invoice_name: str, fmt: str, recipient: str | None) -> dict:
	doc = frappe.get_doc("Sales Invoice", invoice_name)
	email = (recipient or "").strip() or (doc.contact_email or "").strip()
	if not email:
		return {"success": False, "message": _("No email on file for this customer.")}

	pdf_bytes = _generate_pdf_bytes(invoice_name, fmt)
	frappe.sendmail(
		recipients=[email],
		subject=_("Your invoice {0}").format(invoice_name),
		message=_("Hello,<br><br>Please find your invoice <b>{0}</b> attached.<br><br>Thank you for shopping with us.").format(invoice_name),
		attachments=[{
			"fname": f"{invoice_name}.pdf",
			"fcontent": pdf_bytes,
		}],
		reference_doctype="Sales Invoice",
		reference_name=invoice_name,
		now=False,
	)
	return {"success": True, "recipient": email}


def _share_whatsapp(invoice_name: str, fmt: str, mobile_no: str | None) -> dict:
	doc = frappe.get_doc("Sales Invoice", invoice_name)
	phone = (mobile_no or "").strip() or (doc.contact_mobile or "").strip()
	if not phone:
		# Fall back to Customer.mobile_no
		if doc.customer:
			phone = (frappe.db.get_value("Customer", doc.customer, "mobile_no") or "").strip()
	if not phone:
		return {"success": False, "message": _("No mobile number on file for this customer.")}

	try:
		from ch_item_master.ch_core.whatsapp import send_template_message
	except Exception:
		return {"success": False, "message": _("WhatsApp helper not installed.")}

	# Resolve the invoice template from the per-company library (event
	# "invoice_receipt"); fall back to the conventional literal name.
	from ch_item_master.ch_core.whatsapp import get_template
	template_name = get_template(doc.company, "invoice_receipt")[0] or "invoice_receipt"

	pdf_url = get_url(_pdf_download_url(invoice_name, fmt))
	body_values = {
		"1": doc.customer_name or doc.customer or _("Customer"),
		"2": invoice_name,
		"3": frappe.format_value(doc.grand_total, {"fieldtype": "Currency"}),
		"4": pdf_url,
	}
	try:
		send_template_message(
			phone=phone,
			event="invoice_receipt",
			body_values=body_values,
			customer_name=doc.customer_name or doc.customer,
			ref_doctype="Sales Invoice",
			ref_name=invoice_name,
			enqueue=True,
			company=doc.company,
		)
		return {"success": True, "recipient": phone, "template": template_name}
	except Exception as exc:
		frappe.log_error(frappe.get_traceback(), "share_invoice: WhatsApp send failed")
		return {"success": False, "message": str(exc)}


def _share_einvoice(invoice_name: str) -> dict:
	"""Best-effort IRN generation via India Compliance, if missing."""
	irn = frappe.db.get_value("Sales Invoice", invoice_name, "irn")
	if irn:
		return {"success": True, "irn": irn, "message": _("Already generated.")}

	try:
		from india_compliance.gst_india.utils.e_invoice import generate_e_invoice
	except Exception:
		return {"success": False, "message": _("India Compliance e-invoice module not available.")}

	try:
		generate_e_invoice(invoice_name)
		irn = frappe.db.get_value("Sales Invoice", invoice_name, "irn")
		return {"success": bool(irn), "irn": irn or ""}
	except Exception as exc:
		frappe.log_error(frappe.get_traceback(), "share_invoice: IRN generation failed")
		return {"success": False, "message": str(exc)}


@frappe.whitelist()
def share_invoice(
	invoice_name: str,
	channels: list | str,
	mobile_no: str | None = None,
	email: str | None = None,
) -> dict:
	"""One-click receipt fanout across print / email / whatsapp / einvoice.

	Returns: {"invoice": str, "results": {channel: {success, ...}}}
	"""
	if not invoice_name or not frappe.db.exists("Sales Invoice", invoice_name):
		frappe.throw(_("Invoice not found."), title=_("Share Invoice"))

	if isinstance(channels, str):
		import json
		try:
			channels = json.loads(channels)
		except Exception:
			channels = [c.strip() for c in channels.split(",") if c.strip()]

	channels = [c for c in (channels or []) if c in _VALID_CHANNELS]
	if not channels:
		frappe.throw(_("At least one valid channel is required."), title=_("Share Invoice"))

	fmt = _resolve_print_format(invoice_name)
	results: dict = {}

	for ch in channels:
		try:
			if ch == "print":
				results[ch] = _share_print(invoice_name, fmt)
			elif ch == "email":
				results[ch] = _share_email(invoice_name, fmt, email)
			elif ch == "whatsapp":
				results[ch] = _share_whatsapp(invoice_name, fmt, mobile_no)
			elif ch == "einvoice":
				results[ch] = _share_einvoice(invoice_name)
		except Exception as exc:
			frappe.log_error(frappe.get_traceback(), f"share_invoice[{ch}] failed")
			results[ch] = {"success": False, "message": str(exc)}

	return {"invoice": invoice_name, "results": results}
