"""PDF printing endpoints for CH POS.

Frappe's PDF rendering pipeline writes the document Access Log. The additional
client-print endpoint below covers POS output assembled entirely in the browser,
where the Frappe renderer is not involved.
"""

from __future__ import annotations

from typing import Literal

import frappe
from frappe.core.doctype.access_log.access_log import make_access_log

from ch_pos.api.scope_guard import assert_pos_profile_scope


@frappe.whitelist()
def download_pdf(
	doctype: str,
	name: str,
	format=None,
	doc=None,
	no_letterhead=0,
	language=None,
	letterhead=None,
	pdf_generator: Literal["wkhtmltopdf", "chrome"] | None = None,
):
	"""Render a permitted document PDF through Frappe's audited pipeline."""
	from frappe.utils.print_format import download_pdf as frappe_download_pdf

	return frappe_download_pdf(
		doctype=doctype,
		name=name,
		format=format,
		doc=doc,
		no_letterhead=no_letterhead,
		language=language,
		letterhead=letterhead,
		pdf_generator=pdf_generator,
	)


@frappe.whitelist()
def log_client_print(pos_profile: str, report_name: str, filters=None):
	"""Audit a POS print assembled in the browser rather than by Frappe PDF."""
	assert_pos_profile_scope(pos_profile)
	report_name = str(report_name or "POS Browser Print").strip()[:140]
	filter_text = frappe.as_json(filters) if isinstance(filters, dict) else str(filters or "")
	make_access_log(
		method="Print",
		file_type="PDF",
		report_name=report_name,
		filters=filter_text[:10000] or None,
		page="Source: CH POS; Browser Print",
	)
	return {"logged": True}
