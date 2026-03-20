/**
 * CH POS — IMEI / Serial Lookup Workspace
 *
 * Scan or enter IMEI/Serial to see full device lifecycle:
 * sales, returns, services, buybacks, warranty status.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class ImeiWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "imei") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#f3e8ff;color:#7c3aed">
							<i class="fa fa-barcode"></i>
						</span>
						${__("IMEI / Serial Lookup")}
					</h4>
					<span class="ch-mode-hint">${__("Scan or enter IMEI/Serial to see full device lifecycle")}</span>
				</div>

				<div style="display:flex;gap:10px;align-items:stretch;margin-bottom:16px;">
					<div class="ch-pos-search-wrap" style="flex:1;max-width:none">
						<i class="fa fa-barcode ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search ch-imei-search"
							placeholder="${__("Scan barcode or type IMEI / Serial...")}">
					</div>
					<button class="btn btn-primary ch-imei-lookup" style="border-radius:var(--pos-radius);font-weight:700;padding:0 20px">
						<i class="fa fa-search"></i>
					</button>
				</div>

				<div class="ch-imei-result">
					<div class="ch-pos-empty-state" style="padding:40px 16px;">
						<div class="empty-icon"><i class="fa fa-barcode"></i></div>
						<div class="empty-title">${__("Scan or enter an IMEI")}</div>
						<div class="empty-subtitle">${__("View complete device history: sales, returns, repairs, buybacks, warranty")}</div>
					</div>
				</div>
			</div>
		`);
		this._bind(panel);
	}

	_bind(panel) {
		const do_search = () => {
			const q = panel.find(".ch-imei-search").val().trim();
			if (!q) {
				frappe.show_alert({ message: __("Enter an IMEI, serial number, or barcode"), indicator: "orange" });
				return;
			}
			panel.find(".ch-imei-result").html(
				`<div style="padding:24px;text-align:center"><i class="fa fa-spinner fa-spin"></i></div>`
			);
			frappe.call({
				method: "ch_pos.api.pos_api.imei_history",
				args: { serial_no: q },
				callback: (r) => {
					const d = r.message;
					if (!d || d.error) {
						panel.find(".ch-imei-result").html(`
							<div class="ch-pos-empty-state" style="padding:30px 16px;">
								<div class="empty-icon"><i class="fa fa-search"></i></div>
								<div class="empty-title">${d?.error || __("No records for")} "${frappe.utils.escape_html(q)}"</div>
							</div>`);
						return;
					}
					this._render_history(panel, d);
				},
			});
		};

		panel.on("click", ".ch-imei-lookup", do_search);
		panel.find(".ch-imei-search").on("keypress", (e) => { if (e.which === 13) do_search(); });
	}

	_render_history(panel, d) {
		const el = panel.find(".ch-imei-result");

		const warranty_badge = (exp) => {
			if (!exp) return `<span class="ch-pos-badge badge-muted">${__("No Warranty Info")}</span>`;
			const exp_date = frappe.datetime.str_to_obj(exp);
			const today = new Date();
			if (exp_date > today) {
				const days = Math.ceil((exp_date - today) / 86400000);
				return `<span class="ch-pos-badge badge-success">${__("Under Warranty")} (${days} ${__("days left")})</span>`;
			}
			return `<span class="ch-pos-badge badge-danger">${__("Warranty Expired")} ${frappe.datetime.str_to_user(exp)}</span>`;
		};

		const section = (title, icon, items, render_fn) => {
			if (!items || !items.length) return "";
			const rows = items.map(render_fn).join("");
			return `<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
				<div class="section-header"><i class="fa fa-${icon}"></i> ${title} (${items.length})</div>
				<div class="section-body"><div class="ch-imei-timeline">${rows}</div></div>
			</div>`;
		};

		let html = `
		<div class="ch-imei-detail">
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
				<div class="section-body">
					<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
						<div>
							<div style="font-weight:700;font-size:var(--pos-fs-lg);color:var(--pos-text);margin-bottom:4px">
								${frappe.utils.escape_html(d.item_name || d.item_code || __("Unknown Device"))}
							</div>
							<span style="font-family:var(--pos-font-mono);font-size:var(--pos-fs-sm);color:var(--pos-text-muted)">${frappe.utils.escape_html(d.serial_no)}</span>
						</div>
						<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
							<span class="ch-pos-badge badge-${d.status === "Active" ? "success" : d.status === "Delivered" ? "info" : "muted"}">${d.status || __("Unknown")}</span>
							${warranty_badge(d.warranty_expiry_date)}
						</div>
					</div>
					<div style="display:flex;gap:var(--pos-space-lg);flex-wrap:wrap;margin-top:12px;font-size:var(--pos-fs-sm)">
						${d.brand ? `<span><b>${__("Brand")}:</b> ${frappe.utils.escape_html(d.brand)}</span>` : ""}
						${d.warehouse ? `<span><b>${__("Location")}:</b> ${frappe.utils.escape_html(d.warehouse)}</span>` : ""}
						${d.customer ? `<span><b>${__("Owner")}:</b> ${frappe.utils.escape_html(d.customer_name || d.customer)}</span>` : ""}
						${d.amc_expiry_date ? `<span><b>${__("AMC Expiry")}:</b> ${frappe.datetime.str_to_user(d.amc_expiry_date)}</span>` : ""}
					</div>
				</div>
			</div>`;

		html += section(__("Sales History"), "shopping-cart", d.sales, (s) => `
			<div class="ch-timeline-item">
				<div class="ch-timeline-dot sale"></div>
				<div class="ch-timeline-content">
					<div class="ch-timeline-top">
						<a class="ch-timeline-link" data-doctype="Sales Invoice" data-name="${s.invoice}">${s.invoice}</a>
						<span class="text-muted">${frappe.datetime.str_to_user(s.date)}</span>
					</div>
					<span>${__("Sold to")} <b>${frappe.utils.escape_html(s.customer || "")}</b> — ₹${format_number(s.rate)}</span>
				</div>
			</div>`);

		html += section(__("Returns"), "undo", d.returns, (r) => `
			<div class="ch-timeline-item">
				<div class="ch-timeline-dot return"></div>
				<div class="ch-timeline-content">
					<div class="ch-timeline-top">
						<a class="ch-timeline-link" data-doctype="Sales Invoice" data-name="${r.invoice}">${r.invoice}</a>
						<span class="text-muted">${frappe.datetime.str_to_user(r.date)}</span>
					</div>
					<span>${__("Returned")} — ₹${format_number(Math.abs(r.rate))}</span>
				</div>
			</div>`);

		html += section(__("Service History"), "wrench", d.services, (s) => `
			<div class="ch-timeline-item">
				<div class="ch-timeline-dot service"></div>
				<div class="ch-timeline-content">
					<div class="ch-timeline-top">
						<a class="ch-timeline-link" data-doctype="Service Request" data-name="${s.name}">${s.name}</a>
						<span class="ch-pos-badge badge-${s.decision === "Completed" ? "success" : s.decision === "In Service" ? "warning" : "info"}">${s.decision}</span>
						<span style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">${frappe.datetime.str_to_user(s.date)}</span>
					</div>
					<span>${frappe.utils.escape_html(s.issue_category || "")} — ${frappe.utils.escape_html(s.issue_description || "")}</span>
				</div>
			</div>`);

		html += section(__("Buyback History"), "exchange", d.buybacks, (b) => `
			<div class="ch-timeline-item">
				<div class="ch-timeline-dot buyback"></div>
				<div class="ch-timeline-content">
					<div class="ch-timeline-top">
						<a class="ch-timeline-link" data-doctype="Buyback Assessment" data-name="${b.name}">${b.name}</a>
						<span class="ch-pos-badge badge-${b.status === "Submitted" ? "success" : "warning"}">${b.status}</span>
						<span style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">${frappe.datetime.str_to_user(b.date)}</span>
					</div>
					<span>${__("Grade")}: ${b.grade || "—"} — ₹${format_number(b.price || 0)}</span>
				</div>
			</div>`);

		html += `</div>`;
		el.html(html);

		el.find(".ch-timeline-link").on("click", function () {
			frappe.set_route("Form", $(this).data("doctype"), $(this).data("name"));
		});
	}
}
