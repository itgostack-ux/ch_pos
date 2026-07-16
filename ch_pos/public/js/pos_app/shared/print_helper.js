/**
 * print_helper.js
 *
 * Print an invoice as a server-rendered PDF (header on every page).
 *
 * Industry-standard pattern: instead of letting the browser paginate HTML
 * (which cannot reliably repeat per-page headers via window.print()), we
 * ask the server to render the invoice into a PDF using wkhtmltopdf
 * (--header-html gives true per-page company header), then load that PDF
 * into a hidden iframe and trigger the browser's PDF print dialog on it.
 *
 * This matches how SAP, Oracle EBS, Tally, Zoho Books and stock ERPNext
 * print invoices. The same PDF is used for download, email, and print —
 * single source of truth.
 *
 * Usage:
 *   import { print_invoice_pdf } from "../shared/print_helper.js";
 *   print_invoice_pdf("SINV-26-00031", "Custom Sales Invoice");
 */

const PDF_ENDPOINT = "/api/method/frappe.utils.print_format.download_pdf";
const DEFAULT_SALES_INVOICE_FORMAT = "Custom Sales Invoice";

async function resolve_sales_invoice_print_settings(invoice_name) {
	try {
		return await frappe.xcall(
			"ch_erp15.ch_erp15.print_helpers.get_sales_invoice_print_settings",
			{ invoice_name }
		);
	} catch (err) {
		console.warn("print_invoice_pdf: could not resolve company print format", err);
		return null;
	}
}

export async function print_invoice_pdf(invoice_name, print_format, opts = {}) {
	if (!invoice_name) return;

	const doctype = opts.doctype || "Sales Invoice";
	let fmt = print_format || DEFAULT_SALES_INVOICE_FORMAT;
	let no_letterhead = opts.no_letterhead ? 1 : 0;

	if (doctype === "Sales Invoice" && !opts.skip_resolve) {
		const settings = await resolve_sales_invoice_print_settings(invoice_name);
		if (settings && settings.print_format) {
			fmt = settings.print_format;
			if (opts.no_letterhead === undefined) {
				no_letterhead = settings.no_letterhead ? 1 : 0;
			}
		}
	}

	const params = new URLSearchParams({
		doctype: doctype,
		name: invoice_name,
		format: fmt,
		no_letterhead: String(no_letterhead),
	});
	const url = `${PDF_ENDPOINT}?${params.toString()}`;

	let blob_url;
	try {
		const resp = await fetch(url, {
			credentials: "include",
			headers: {
				"X-Frappe-CSRF-Token": (typeof frappe !== "undefined" && frappe.csrf_token) || "",
			},
		});
		if (!resp.ok) throw new Error(`PDF HTTP ${resp.status}`);
		const blob = await resp.blob();
		if (!blob || blob.size === 0) throw new Error("Empty PDF blob");
		blob_url = URL.createObjectURL(blob);
	} catch (err) {
		console.error("print_invoice_pdf: PDF fetch failed", err);
		if (typeof frappe !== "undefined" && frappe.show_alert) {
			frappe.show_alert({
				message: __("Could not generate PDF. Opening print preview instead."),
				indicator: "orange",
			});
		}
		// Fallback: open the HTML printview so the user can still print manually
		const fb = `/printview?doctype=${encodeURIComponent(doctype)}&name=${encodeURIComponent(invoice_name)}&format=${encodeURIComponent(fmt)}&no_letterhead=${no_letterhead}&trigger_print=1`;
		window.open(fb, "_blank");
		return;
	}

	// Hidden iframe → load PDF → call iframe.contentWindow.print()
	const iframe = document.createElement("iframe");
	iframe.style.position = "fixed";
	iframe.style.right = "0";
	iframe.style.bottom = "0";
	iframe.style.width = "0";
	iframe.style.height = "0";
	iframe.style.border = "0";
	iframe.setAttribute("aria-hidden", "true");

	const cleanup = () => {
		if (cleanup._done) return;
		cleanup._done = true;
		try { URL.revokeObjectURL(blob_url); } catch (e) {}
		if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
	};

	let printed = false;
	iframe.onload = () => {
		// Give the embedded PDF viewer a moment to initialise before print()
		setTimeout(() => {
			try {
				iframe.contentWindow.focus();
				iframe.contentWindow.print();
				printed = true;
			} catch (e) {
				console.error("print_invoice_pdf: iframe.print() failed; opening in new tab", e);
				window.open(blob_url, "_blank");
			}
			// Clean up after the print dialog has had time to close
			setTimeout(cleanup, 60000);
		}, 300);
	};

	iframe.src = blob_url;
	document.body.appendChild(iframe);

	// Safety net: if onload never fires (PDF plugin blocked / disabled)
	setTimeout(() => { if (!printed) cleanup(); }, 60000);
}
