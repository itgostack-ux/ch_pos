/**
 * CH POS — Pre-Book / Proforma Workspace
 *
 * Lets the cashier convert the current Sell cart (or a fresh entry) into:
 *   1. A **Proforma Invoice** — submits a Quotation and opens the
 *      "Proforma Invoice" print format (Phase H deliverable, ch_erp15).
 *   2. A **Pre-Booking** — submits a Sales Order with stock reservation +
 *      advance amount tracking (Phase H, ch_payments/advance_payments).
 *
 * Cashier visibility (parity with SAP Retail / Oracle Xstore / GoFrugal POS):
 *   - Tabbed UI: "Create" + "My Proformas" + "My Pre-Bookings"
 *   - KPI strip: today/open/converted/expired counts, reserved IMEI count
 *
 * Reuse-first: backend wrappers `create_pos_quotation`, `create_pre_booking`,
 * `list_my_proformas`, `list_pickup_prebookings`, `get_prebook_pickup_kpis`
 * already exist in ch_pos/api/pos_api.py; this workspace only renders UI.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class PrebookWorkspace {
	constructor() {
		this._panel = null;
		this._active_tab = "create"; // create | proformas | prebookings
		this._tab_filter = { status: "All", days: 30, only_mine: 1, search: "" };
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "prebook") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this._panel = panel;
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#e0f2fe;color:#0369a1">
							<i class="fa fa-bookmark"></i>
						</span>
						${__("Pre-Book / Proforma")}
					</h4>
					<span class="ch-mode-hint">${__("Issue a Proforma Invoice (Quotation) or reserve stock as a Pre-Booking (Sales Order).")}</span>
				</div>

				<div class="ch-pb-kpi-strip" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:var(--pos-space-md);"></div>

				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);">
					<div class="section-body" style="padding:0;">
						<div class="ch-pb-tabs" style="display:flex;border-bottom:1px solid var(--pos-border);">
							<div class="ch-pb-tab" data-tab="create" style="padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;">
								<i class="fa fa-plus-circle"></i> ${__("Create New")}
							</div>
							<div class="ch-pb-tab" data-tab="proformas" style="padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;">
								<i class="fa fa-file-text-o"></i> ${__("My Proformas")}
							</div>
							<div class="ch-pb-tab" data-tab="prebookings" style="padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;">
								<i class="fa fa-bookmark"></i> ${__("My Pre-Bookings")}
							</div>
						</div>
					</div>
				</div>

				<div class="ch-pb-tab-body"></div>
			</div>
		`);

		this._bind(panel);
		this._refresh_kpis();
		this._switch_tab(this._active_tab);
	}

	_bind(panel) {
		panel.on("click", ".ch-pb-tab", (e) => {
			const tab = $(e.currentTarget).data("tab");
			if (tab) this._switch_tab(tab);
		});
		// "Create" subview events
		panel.on("click", ".ch-prebook-proforma", () => {
			const ctx = this._read_cart_ctx();
			this._proforma_flow(ctx.cart, ctx.customer);
		});
		panel.on("click", ".ch-prebook-reserve", () => {
			const ctx = this._read_cart_ctx();
			this._prebook_flow(ctx.cart, ctx.customer, ctx.total);
		});
		// List filter events
		panel.on("input", ".ch-pb-search", (e) => {
			this._tab_filter.search = e.target.value.trim();
			clearTimeout(this._debounce);
			this._debounce = setTimeout(() => this._reload_list(), 300);
		});
		panel.on("change", ".ch-pb-status", (e) => {
			this._tab_filter.status = e.target.value || "All";
			this._reload_list();
		});
		panel.on("change", ".ch-pb-days", (e) => {
			this._tab_filter.days = parseInt(e.target.value, 10) || 30;
			this._reload_list();
		});
		panel.on("change", ".ch-pb-mine", (e) => {
			this._tab_filter.only_mine = e.target.checked ? 1 : 0;
			this._reload_list();
		});
		panel.on("click", ".ch-pb-refresh", () => {
			this._refresh_kpis();
			this._reload_list();
		});
		panel.on("click", ".ch-pb-print", (e) => {
			const url = $(e.currentTarget).data("url");
			if (url) window.open(url, "_blank");
		});
		panel.on("click", ".ch-pb-open", (e) => {
			const doctype = $(e.currentTarget).data("doctype");
			const name = $(e.currentTarget).data("name");
			if (doctype && name) {
				window.open(`/app/${doctype.toLowerCase().replace(/ /g, "-")}/${encodeURIComponent(name)}`, "_blank");
			}
		});
	}

	_read_cart_ctx() {
		const cart = PosState.cart || [];
		const customer = PosState.customer || PosState.default_customer || "";
		const total = cart.reduce((s, it) => s + flt(it.qty || 1) * flt(it.rate || 0), 0);
		return { cart, customer, total };
	}

	_switch_tab(tab) {
		this._active_tab = tab;
		const $tabs = this._panel.find(".ch-pb-tab");
		$tabs.each((_, el) => {
			const $el = $(el);
			const active = $el.data("tab") === tab;
			$el.css({
				"border-bottom-color": active ? "var(--pos-primary, #0369a1)" : "transparent",
				"color": active ? "var(--pos-primary, #0369a1)" : "inherit",
				"font-weight": active ? "600" : "400",
			});
		});
		const $body = this._panel.find(".ch-pb-tab-body");
		if (tab === "create") {
			$body.html(this._render_create());
		} else {
			$body.html(this._render_list_shell(tab));
			this._reload_list();
		}
	}

	_refresh_kpis() {
		if (!PosState.pos_profile) {
			this._panel.find(".ch-pb-kpi-strip").html("");
			return;
		}
		frappe.call({
			method: "ch_pos.api.pos_api.get_prebook_pickup_kpis",
			args: { pos_profile: PosState.pos_profile, days: 30 },
			callback: (r) => {
				if (!r.message) return;
				const k = r.message;
				const card = (label, value, sub, color) => `
					<div style="flex:1;min-width:140px;background:#fff;border:1px solid var(--pos-border);
						border-left:3px solid ${color};border-radius:var(--pos-radius);padding:10px 12px;">
						<div class="text-muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.3px;">${label}</div>
						<div style="font-size:20px;font-weight:600;margin-top:2px;">${value}</div>
						<div class="text-muted" style="font-size:11px;margin-top:2px;">${sub || ""}</div>
					</div>
				`;
				this._panel.find(".ch-pb-kpi-strip").html(`
					${card(__("Proforma Today"), k.proforma.today_count, `\u20B9${format_number(k.proforma.today_value)}`, "#0369a1")}
					${card(__("Proforma Open"), k.proforma.open_count, __("last {0} days", [k.window_days]), "#2563eb")}
					${card(__("Proforma Converted"), k.proforma.ordered_count, __("last {0} days", [k.window_days]), "#16a34a")}
					${card(__("Proforma Expired/Lost"), (k.proforma.expired_count + k.proforma.lost_count), __("needs follow-up"), "#dc2626")}
					${card(__("Pre-Bookings Open"), k.prebook.open_count, `\u20B9${format_number(k.prebook.open_balance)} ${__("balance")}`, "#7c3aed")}
					${card(__("Reserved IMEIs"), k.reserved_serials, __("across open pre-bookings"), "#f59e0b")}
				`);
			},
		});
	}

	// ─────────────────────────────────────────── Create subview ──

	_render_create() {
		const { cart, customer, total } = this._read_cart_ctx();
		const rows = cart.length
			? cart.map((it) => `
				<tr>
					<td>${frappe.utils.escape_html(it.item_name || it.item_code)}</td>
					<td style="text-align:right">${flt(it.qty || 1)}</td>
					<td style="text-align:right">\u20B9${format_number(it.rate || 0)}</td>
					<td style="text-align:right"><b>\u20B9${format_number(flt(it.qty || 1) * flt(it.rate || 0))}</b></td>
				</tr>
			`).join("")
			: `<tr><td colspan="4" class="text-muted" style="padding:20px;text-align:center;">
				${__("Cart is empty. Add items in the Sell workspace first, then return here.")}
			</td></tr>`;

		return `
			<div style="display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap;">
				<div style="flex:2;min-width:380px;">
					<div style="background:#fff;border:1px solid var(--pos-border);border-radius:var(--pos-radius);padding:14px;">
						<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
							<b>${__("Customer")}</b>
							<span class="text-muted">${frappe.utils.escape_html(customer || __("(no customer selected)"))}</span>
						</div>
						<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
							<b>${__("Cart Items")}</b>
							<span>${cart.length} ${__("line(s)")}</span>
						</div>
						<table class="table table-condensed" style="margin:0;">
							<thead>
								<tr>
									<th>${__("Item")}</th>
									<th style="text-align:right">${__("Qty")}</th>
									<th style="text-align:right">${__("Rate")}</th>
									<th style="text-align:right">${__("Amount")}</th>
								</tr>
							</thead>
							<tbody>${rows}</tbody>
							<tfoot>
								<tr>
									<th colspan="3" style="text-align:right">${__("Total")}</th>
									<th style="text-align:right">\u20B9${format_number(total)}</th>
								</tr>
							</tfoot>
						</table>
					</div>
				</div>

				<div style="flex:1;min-width:280px;">
					<div style="background:#fff;border:1px solid var(--pos-border);border-radius:var(--pos-radius);padding:14px;display:flex;flex-direction:column;gap:10px;">
						<button class="btn btn-primary btn-block ch-prebook-proforma" ${cart.length ? "" : "disabled"}>
							<i class="fa fa-file-text-o"></i> ${__("Generate Proforma Invoice")}
						</button>
						<div class="text-muted" style="font-size:11px;margin-top:-4px;">
							${__("Creates a submitted Quotation and opens the Proforma Invoice print format. No stock reservation.")}
						</div>
						<hr style="margin:8px 0;">
						<button class="btn btn-success btn-block ch-prebook-reserve" ${cart.length ? "" : "disabled"}>
							<i class="fa fa-bookmark"></i> ${__("Create Pre-Booking (Reserve Stock)")}
						</button>
						<div class="text-muted" style="font-size:11px;margin-top:-4px;">
							${__("Creates a Sales Order with stock reservation. Accepts an optional advance amount.")}
						</div>
					</div>
				</div>
			</div>
		`;
	}

	// ─────────────────────────────────────────── List subview ──

	_render_list_shell(tab) {
		const status_options = tab === "proformas"
			? ["All", "Open", "Ordered", "Lost", "Expired"]
			: ["All", "To Deliver and Bill", "To Bill", "To Deliver"];
		return `
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);">
				<div class="section-body">
					<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
						<input type="text" class="form-control ch-pb-search" placeholder="${__("Search number, customer…")}" style="flex:1;min-width:240px;">
						<select class="form-control ch-pb-status" style="width:200px;">
							${status_options.map(s => `<option value="${s}" ${s === this._tab_filter.status ? "selected" : ""}>${__(s)}</option>`).join("")}
						</select>
						<select class="form-control ch-pb-days" style="width:160px;">
							<option value="7">${__("Last 7 days")}</option>
							<option value="30" selected>${__("Last 30 days")}</option>
							<option value="90">${__("Last 90 days")}</option>
							<option value="180">${__("Last 6 months")}</option>
						</select>
						<label style="display:inline-flex;align-items:center;gap:6px;margin:0;">
							<input type="checkbox" class="ch-pb-mine" ${this._tab_filter.only_mine ? "checked" : ""}> ${__("Only mine")}
						</label>
						<button class="btn btn-default btn-sm ch-pb-refresh"><i class="fa fa-refresh"></i> ${__("Refresh")}</button>
					</div>
				</div>
			</div>
			<div class="ch-pos-section-card">
				<div class="section-body ch-pb-list">
					<div class="text-muted text-center" style="padding:20px;">${__("Loading…")}</div>
				</div>
			</div>
		`;
	}

	_reload_list() {
		if (this._active_tab === "create") return;
		if (!PosState.pos_profile) {
			this._panel.find(".ch-pb-list").html(
				`<div class="text-muted text-center" style="padding:20px;">${__("Select a POS profile first.")}</div>`
			);
			return;
		}
		const $list = this._panel.find(".ch-pb-list");
		$list.html(`<div class="text-muted text-center" style="padding:20px;">${__("Loading…")}</div>`);

		if (this._active_tab === "proformas") {
			frappe.call({
				method: "ch_pos.api.pos_api.list_my_proformas",
				args: {
					pos_profile: PosState.pos_profile,
					status: this._tab_filter.status,
					days: this._tab_filter.days,
					only_mine: this._tab_filter.only_mine,
					search: this._tab_filter.search || null,
					limit: 100,
				},
				callback: (r) => this._render_proforma_rows(r.message || []),
			});
		} else if (this._active_tab === "prebookings") {
			frappe.call({
				method: "ch_pos.api.pos_api.list_pickup_prebookings",
				args: {
					pos_profile: PosState.pos_profile,
					search: this._tab_filter.search || null,
					days_ahead: Math.max(this._tab_filter.days, 30),
					overdue_only: 0,
					limit: 100,
				},
				callback: (r) => this._render_prebooking_rows(r.message || []),
			});
		}
	}

	_render_proforma_rows(rows) {
		const $list = this._panel.find(".ch-pb-list");
		if (!rows.length) {
			$list.html(`<div class="text-muted text-center" style="padding:24px;">
				<i class="fa fa-inbox" style="font-size:32px;opacity:0.4;"></i>
				<div style="margin-top:8px;">${__("No proformas found.")}</div>
			</div>`);
			return;
		}
		const status_badge = (status, expiring, expired) => {
			let bg = "#e5e7eb", fg = "#374151";
			if (status === "Open") { bg = "#dbeafe"; fg = "#1d4ed8"; }
			else if (status === "Ordered") { bg = "#dcfce7"; fg = "#15803d"; }
			else if (status === "Lost") { bg = "#fee2e2"; fg = "#b91c1c"; }
			else if (status === "Expired") { bg = "#fef3c7"; fg = "#a16207"; }
			if (expired) { bg = "#fee2e2"; fg = "#b91c1c"; }
			else if (expiring) { bg = "#fef3c7"; fg = "#a16207"; }
			const label = expired ? __("Expired") : (expiring ? __("Expiring") : status);
			return `<span style="background:${bg};color:${fg};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">${label}</span>`;
		};
		$list.html(rows.map(r => `
			<div style="display:flex;gap:12px;padding:12px;border-bottom:1px solid var(--pos-border);align-items:flex-start;">
				<div style="flex:1.2;min-width:180px;">
					<div style="font-weight:600;">${frappe.utils.escape_html(r.customer_name)}</div>
					<div class="small text-muted">${frappe.utils.escape_html(r.customer)}</div>
					<div class="small text-muted" style="margin-top:4px;">
						<a href="/app/quotation/${encodeURIComponent(r.name)}" target="_blank">${r.name}</a>
					</div>
				</div>
				<div style="flex:1.4;min-width:200px;font-size:12px;">
					${(r.items || []).slice(0,3).map(it => `${frappe.utils.escape_html(it.item_name || it.item_code)} \u00D7 ${flt(it.qty)}`).join("<br>")}
					${(r.items || []).length > 3 ? `<div class="small text-muted">+${(r.items || []).length - 3} ${__("more")}</div>` : ""}
				</div>
				<div style="flex:0.9;min-width:140px;text-align:right;font-size:12px;">
					<div>${__("Total")}: <b>\u20B9${format_number(r.grand_total)}</b></div>
					${r.advance_received > 0 ? `<div class="text-success">${__("Advance")}: \u20B9${format_number(r.advance_received)}</div>` : ""}
					<div>${__("Valid till")}: ${r.valid_till || "—"}</div>
				</div>
				<div style="flex:0.7;min-width:110px;text-align:center;">
					${status_badge(r.status, r.is_expiring_soon, r.is_expired)}
					${r.days_left !== null && r.days_left !== undefined ? `<div class="small text-muted" style="margin-top:4px;">${r.days_left >= 0 ? __("{0}d left", [r.days_left]) : __("{0}d ago", [-r.days_left])}</div>` : ""}
				</div>
				<div style="flex:0;display:flex;flex-direction:column;gap:6px;min-width:130px;">
					<button class="btn btn-default btn-xs ch-pb-print" data-url="${r.print_url}">
						<i class="fa fa-print"></i> ${__("Print")}
					</button>
					<button class="btn btn-default btn-xs ch-pb-open" data-doctype="Quotation" data-name="${r.name}">
						<i class="fa fa-external-link"></i> ${__("Open")}
					</button>
				</div>
			</div>
		`).join(""));
	}

	_render_prebooking_rows(rows) {
		const $list = this._panel.find(".ch-pb-list");
		if (!rows.length) {
			$list.html(`<div class="text-muted text-center" style="padding:24px;">
				<i class="fa fa-inbox" style="font-size:32px;opacity:0.4;"></i>
				<div style="margin-top:8px;">${__("No pre-bookings found.")}</div>
			</div>`);
			return;
		}
		$list.html(rows.map(r => {
			const overdue = r.is_overdue
				? `<span style="background:#fee2e2;color:#b91c1c;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">${__("Overdue")}</span>`
				: "";
			const reserved = (r.reserved_serial_count || 0) > 0
				? `<div class="small text-muted" style="margin-top:4px;"><i class="fa fa-barcode"></i> ${__("{0} IMEI reserved", [r.reserved_serial_count])}</div>`
				: "";
			return `
				<div style="display:flex;gap:12px;padding:12px;border-bottom:1px solid var(--pos-border);align-items:flex-start;">
					<div style="flex:1.2;min-width:180px;">
						<div style="font-weight:600;">${frappe.utils.escape_html(r.customer_name)}</div>
						<div class="small text-muted">${frappe.utils.escape_html(r.customer)}</div>
						<div class="small text-muted" style="margin-top:4px;">
							<a href="/app/sales-order/${encodeURIComponent(r.name)}" target="_blank">${r.name}</a>
						</div>
						${reserved}
					</div>
					<div style="flex:1.4;min-width:200px;font-size:12px;">
						${(r.items || []).slice(0,3).map(it => `${frappe.utils.escape_html(it.item_name || it.item_code)} \u00D7 ${flt(it.qty)}`).join("<br>")}
						${(r.items || []).length > 3 ? `<div class="small text-muted">+${(r.items || []).length - 3} ${__("more")}</div>` : ""}
					</div>
					<div style="flex:0.9;min-width:140px;text-align:right;font-size:12px;">
						<div>${__("Total")}: <b>\u20B9${format_number(r.grand_total)}</b></div>
						${r.advance_paid > 0 ? `<div class="text-success">${__("Advance")}: \u20B9${format_number(r.advance_paid)}</div>` : ""}
						<div>${__("Balance")}: <b>\u20B9${format_number(r.balance_due)}</b></div>
						<div class="small text-muted">${__("Due")}: ${r.delivery_date || "—"} ${overdue}</div>
					</div>
					<div style="flex:0;display:flex;flex-direction:column;gap:6px;min-width:130px;">
						<button class="btn btn-default btn-xs ch-pb-open" data-doctype="Sales Order" data-name="${r.name}">
							<i class="fa fa-external-link"></i> ${__("Open SO")}
						</button>
					</div>
				</div>
			`;
		}).join(""));
	}

	_proforma_flow(cart, customer) {
		const cart_total = (cart || []).reduce(
			(s, it) => s + flt(it.qty || 1) * flt(it.rate || 0), 0,
		);
		const dlg = new frappe.ui.Dialog({
			title: __("Generate Proforma Invoice"),
			fields: [
				{
					fieldname: "customer", fieldtype: "Link", options: "Customer",
					label: __("Customer"), reqd: 1, default: customer,
				},
				{
					fieldname: "valid_till", fieldtype: "Date", label: __("Valid Till"),
					default: frappe.datetime.add_days(frappe.datetime.nowdate(), 15),
				},
				{ fieldname: "column_break_a", fieldtype: "Column Break" },
				{
					fieldname: "advance_amount", fieldtype: "Currency",
					label: __("Advance Amount"),
					description: __("Optional. Shown on the Proforma as Advance Received and Balance Due. Collect via Payment Entry separately."),
				},
				{ fieldname: "section_break_b", fieldtype: "Section Break" },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Terms / Notes") },
				{
					fieldname: "html_total", fieldtype: "HTML",
					options: `<div style="text-align:right;padding:6px 0;"><b>${__("Order Total")}:</b> \u20B9${format_number(cart_total)}</div>`,
				},
			],
			primary_action_label: __("Generate"),
			primary_action: (v) => {
				if (flt(v.advance_amount) > cart_total + 0.005) {
					frappe.show_alert({
						message: __("Advance cannot exceed Order Total"),
						indicator: "orange",
					});
					return;
				}
				frappe.call({
					method: "ch_pos.api.pos_api.create_pos_quotation",
					args: {
						pos_profile: PosState.pos_profile,
						customer: v.customer,
						items: cart.map((it) => ({
							item_code: it.item_code,
							qty: flt(it.qty || 1),
							rate: flt(it.rate || 0),
							uom: it.uom || "Nos",
							warehouse: it.warehouse,
						})),
						valid_till: v.valid_till,
						notes: v.notes,
						advance_amount: flt(v.advance_amount),
					},
					freeze: true,
					freeze_message: __("Creating Proforma..."),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						this._show_proforma_success(r.message);
					},
				});
			},
		});
		dlg.show();
	}

	_show_proforma_success(qtn) {
		const print_url = `/printview?doctype=Quotation&name=${encodeURIComponent(qtn.name)}`
			+ `&format=${encodeURIComponent(qtn.print_format || "Proforma Invoice")}&no_letterhead=0`;
		const adv = flt(qtn.advance_received);
		const bal = flt(qtn.balance_due);
		const advance_html = adv > 0
			? `<p>${__("Advance Received")}: <b>₹${format_number(adv)}</b></p>
			   <p>${__("Balance Due")}: <b>₹${format_number(bal)}</b></p>`
			: "";
		frappe.msgprint({
			title: __("Proforma Created"),
			indicator: "green",
			message: `
				<div style="text-align:center;padding:12px;">
					<i class="fa fa-check-circle text-success" style="font-size:42px;"></i>
					<h4 style="margin:14px 0 6px;">${frappe.utils.escape_html(qtn.name)}</h4>
					<p>${__("Grand Total")}: <b>₹${format_number(qtn.grand_total)}</b></p>
					${advance_html}
					<p class="text-muted">${__("Valid till")} ${qtn.valid_till}</p>
					<div style="margin-top:14px;display:flex;gap:8px;justify-content:center;">
						${qtn.docstatus === 1
							? `<a class="btn btn-primary btn-sm" target="_blank" href="${print_url}">
								<i class="fa fa-print"></i> ${__("Print Proforma")}
							</a>`
							: `<span class="text-warning" style="font-size:12px;align-self:center;">
								<i class="fa fa-exclamation-triangle"></i> ${__("Proforma saved as draft — submit manually before printing.")}
							</span>`
						}
						
					</div>
				</div>`,
		});
	}

	_prebook_flow(cart, customer, total) {
		const dlg = new frappe.ui.Dialog({
			title: __("Create Pre-Booking (Reserve Stock)"),
			fields: [
				{
					fieldname: "customer", fieldtype: "Link", options: "Customer",
					label: __("Customer"), reqd: 1, default: customer,
				},
				{
					fieldname: "delivery_date", fieldtype: "Date", label: __("Delivery Date"), reqd: 1,
					default: frappe.datetime.add_days(frappe.datetime.nowdate(), 7),
				},
				{ fieldname: "column_break_a", fieldtype: "Column Break" },
				{
					fieldname: "advance_amount", fieldtype: "Currency", label: __("Advance Amount"),
					description: __("Optional. Logged as a comment on the Sales Order; collect payment via Payment Entry."),
				},
				{
					fieldname: "reserve_stock", fieldtype: "Check",
					label: __("Reserve Stock"), default: 1,
				},
				{ fieldname: "section_break_b", fieldtype: "Section Break" },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes") },
				{
					fieldname: "html_total", fieldtype: "HTML",
					options: `<div style="text-align:right;padding:6px 0;"><b>${__("Order Total")}:</b> ₹${format_number(total)}</div>`,
				},
			],
			primary_action_label: __("Create Pre-Booking"),
			primary_action: (v) => {
				frappe.call({
					method: "ch_pos.api.pos_api.create_pre_booking",
					args: {
						pos_profile: PosState.pos_profile,
						customer: v.customer,
						items: cart.map((it) => ({
							item_code: it.item_code,
							qty: flt(it.qty || 1),
							rate: flt(it.rate || 0),
							uom: it.uom || "Nos",
							warehouse: it.warehouse,
						})),
						delivery_date: v.delivery_date,
						advance_amount: flt(v.advance_amount),
						notes: v.notes,
						reserve_stock: v.reserve_stock ? 1 : 0,
					},
					freeze: true,
					freeze_message: __("Creating Pre-Booking..."),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						const so = r.message;
						PosState.reset_transaction();
						const so_name = so.name || __("Sales Order");
						const so_url = so.name ? `/app/sales-order/${encodeURIComponent(so.name)}` : "/app/sales-order";
						// frappe.msgprint({
						// 	title: __("Pre-Booking Created"),
						// 	indicator: so.docstatus === 1 ? "green" : "orange",
						// 	message: `
						// 		<div style="text-align:center;padding:12px;">
						// 			<i class="fa fa-bookmark text-success" style="font-size:42px;"></i>
						// 			<h4 style="margin:14px 0 6px;">${frappe.utils.escape_html(so_name)}</h4>
						// 			<p>${__("Created Sales Order")}: <b>${frappe.utils.escape_html(so_name)}</b></p>
						// 			<p>${__("Status")}: <b>${frappe.utils.escape_html(so.status || "-")}</b></p>
						// 			<p class="text-muted">${__("Delivery")}: ${frappe.utils.escape_html(so.delivery_date || "-")} · ${__("Stock reserved")}: ${so.reserve_stock ? __("Yes") : __("No")}</p>
						// 			${so.warning ? `<p class="text-warning">${frappe.utils.escape_html(so.warning)}</p>` : ""}
						// 			<a class="btn btn-default btn-sm" target="_blank" href="${so_url}">
						// 				<i class="fa fa-external-link"></i> ${__("Open Sales Order")}
						// 			</a>
						// 		</div>`,
						// });
					},
				});
			},
		});
		dlg.show();
	}
}
