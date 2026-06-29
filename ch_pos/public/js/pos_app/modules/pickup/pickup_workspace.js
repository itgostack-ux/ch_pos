/**
 * CH POS — Pickup / Bill Workspace
 *
 * For store staff: shows all submitted Pre-Bookings (Sales Orders) that are
 * still pending pickup/billing, and lets the cashier convert one to a POS
 * Sales Invoice in a single click — collecting the balance payment and
 * pulling in any advance already paid on the SO.
 *
 * Tabs (parity with SAP Retail / Oracle Xstore / GoFrugal POS):
 *   - Pending Pickups (default)
 *   - Reserved IMEIs / Serials  (operational reservation view)
 *   - Today Billed              (audit / handover view)
 *
 * Reuse-first: backend wrappers in ch_pos/api/pos_api.py
 *   - list_pickup_prebookings(pos_profile, search, days_ahead, overdue_only)
 *   - list_reserved_serials(pos_profile, search)
 *   - get_prebook_pickup_kpis(pos_profile)
 *   - convert_prebooking_to_invoice(pos_profile, sales_order, mode_of_payment, paid_amount)
 *
 * The conversion uses ERPNext's standard SO→SI mapper, so taxes, advances,
 * and item mapping behave identically to billing from Desk.
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class PickupWorkspace {
	constructor() {
		this._panel = null;
		this._active_tab = "pending"; // pending | reserved | today
		this._rows = [];
		this._reserved_rows = [];
		this._today_rows = [];
		this._filter = { search: "", days_ahead: 30, overdue_only: 0 };
		this._focus_so = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "pickup") return;
			this.render(ctx.panel);
		});
		// Deep-link from prebooking success: jump directly to pending queue
		// and filter to the newly created Sales Order.
		EventBus.on("pickup:focus_so", (so_name) => {
			this._focus_so = (so_name || "").trim();
			this._active_tab = "pending";
			if (this._panel && this._panel.is(":visible")) {
				this._apply_focus_filter();
			}
		});
	}

	render(panel) {
		this._panel = panel;
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header" style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
					<div>
						<h4>
							<span class="mode-icon" style="background:#ecfdf5;color:#047857">
								<i class="fa fa-cube"></i>
							</span>
							${__("Pickup / Bill")}
						</h4>
						<span class="ch-mode-hint">${__("Hand over goods and bill open pre-bookings \u2014 verify reserved IMEI, collect balance, generate Tax Invoice.")}</span>
					</div>
					<button class="btn btn-default btn-sm ch-pu-go-prebook" title="${__("Create new Proforma or Pre-Booking.")}">
						<i class="fa fa-bookmark"></i> ${__("New Pre-Book / Proforma")}
					</button>
				</div>

				<div class="ch-pu-kpi-strip" style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:var(--pos-space-md);"></div>

				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);">
					<div class="section-body" style="padding:0;">
						<div class="ch-pu-tabs" style="display:flex;border-bottom:1px solid var(--pos-border);">
							<div class="ch-pu-tab" data-tab="pending" style="padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;">
								<i class="fa fa-list"></i> ${__("Pending Pickups")}
							</div>
							<div class="ch-pu-tab" data-tab="reserved" style="padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;">
								<i class="fa fa-barcode"></i> ${__("Reserved IMEIs")}
							</div>
							<div class="ch-pu-tab" data-tab="today" style="padding:10px 16px;cursor:pointer;border-bottom:2px solid transparent;">
								<i class="fa fa-check-square-o"></i> ${__("Today Billed")}
							</div>
						</div>
					</div>
				</div>

				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md);">
					<div class="section-body">
						<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
							<input type="text" class="form-control ch-pickup-search"
								placeholder="${__("Search SO #, customer, IMEI, tracking…")}"
								style="flex:1;min-width:240px;">
							<select class="form-control ch-pickup-window" style="width:180px;">
								<option value="7">${__("Due in 7 days")}</option>
								<option value="30" selected>${__("Due in 30 days")}</option>
								<option value="90">${__("Due in 90 days")}</option>
								<option value="0">${__("All upcoming")}</option>
							</select>
							<label class="ch-pu-overdue-wrap" style="display:inline-flex;align-items:center;gap:6px;margin:0;">
								<input type="checkbox" class="ch-pickup-overdue"> ${__("Overdue only")}
							</label>
							<button class="btn btn-default btn-sm ch-pickup-refresh">
								<i class="fa fa-refresh"></i> ${__("Refresh")}
							</button>
						</div>
					</div>
				</div>

				<div class="ch-pos-section-card">
					<div class="section-header" style="display:flex;justify-content:space-between;align-items:center;">
						<span class="ch-pu-section-title"><i class="fa fa-list"></i> ${__("Pending Pickups")}</span>
						<span class="ch-pickup-count text-muted small"></span>
					</div>
					<div class="section-body ch-pickup-list">
						<div class="text-muted text-center" style="padding:20px;">${__("Loading…")}</div>
					</div>
				</div>
			</div>
		`);

		this._bind(panel);
		this._refresh_kpis();
		if (this._focus_so) {
			this._apply_focus_filter();
		} else {
			this._switch_tab(this._active_tab);
		}
	}

	_bind(panel) {
		let debounce = null;
		panel.on("click", ".ch-pu-tab", (e) => {
			const tab = $(e.currentTarget).data("tab");
			if (tab) this._switch_tab(tab);
		});
		panel.on("click", ".ch-pu-go-prebook", () => {
			EventBus.emit("mode:switch", "prebook");
		});
		// Proforma Open KPI is a deep-link into Prebook → My Proformas where
		// the cashier can Convert → Sale or Convert → Pre-Booking.
		panel.on("click", ".ch-pu-kpi-proforma", () => {
			EventBus.emit("prebook:goto_proformas");
			EventBus.emit("mode:switch", "prebook");
		});
		panel.on("input", ".ch-pickup-search", (e) => {
			this._filter.search = e.target.value.trim();
			clearTimeout(debounce);
			debounce = setTimeout(() => this._load(), 300);
		});
		panel.on("change", ".ch-pickup-window", (e) => {
			this._filter.days_ahead = parseInt(e.target.value, 10) || 0;
			this._load();
		});
		panel.on("change", ".ch-pickup-overdue", (e) => {
			this._filter.overdue_only = e.target.checked ? 1 : 0;
			this._load();
		});
		panel.on("click", ".ch-pickup-refresh", () => {
			this._refresh_kpis();
			this._load();
		});
		panel.on("click", ".ch-pickup-bill", (e) => {
			const name = $(e.currentTarget).data("name");
			const row = this._rows.find(r => r.name === name);
			if (!row) return;
			// Reserved-stock prebookings need their IMEI tagged first. Previously
			// the button was simply disabled, so clicking did nothing with no
			// explanation — now give clear, actionable feedback instead.
			const requires_gate = cint(row.reserve_stock) === 1;
			const ready = !requires_gate || (row.reserved_serials || []).length > 0;
			if (!ready) {
				frappe.msgprint({
					title: __("Reserved IMEI Not Tagged"),
					indicator: "orange",
					message: __("This prebooking reserves stock. Tag the reserved IMEI in the 'Reserved IMEIs / Serials' tab before billing."),
				});
				return;
			}
			this._bill_flow(row);
		});
		panel.on("click", ".ch-pickup-invoice-print", (e) => {
			const url = $(e.currentTarget).data("url");
			if (url) window.open(url, "_blank");
		});
	}

	_switch_tab(tab) {
		this._active_tab = tab;
		const $tabs = this._panel.find(".ch-pu-tab");
		$tabs.each((_, el) => {
			const $el = $(el);
			const active = $el.data("tab") === tab;
			$el.css({
				"border-bottom-color": active ? "var(--pos-primary, #047857)" : "transparent",
				"color": active ? "var(--pos-primary, #047857)" : "inherit",
				"font-weight": active ? "600" : "400",
			});
		});
		// Hide overdue-only and window selectors when not on Pending
		this._panel.find(".ch-pickup-window, .ch-pu-overdue-wrap").toggle(tab === "pending");
		// Update section title
		const titles = {
			pending: `<i class="fa fa-list"></i> ${__("Pending Pickups")}`,
			reserved: `<i class="fa fa-barcode"></i> ${__("Reserved IMEIs / Serials")}`,
			today: `<i class="fa fa-check-square-o"></i> ${__("Today Billed")}`,
		};
		this._panel.find(".ch-pu-section-title").html(titles[tab] || titles.pending);
		this._load();
	}

	_apply_focus_filter() {
		if (!this._focus_so) return;
		this._active_tab = "pending";
		this._filter.search = this._focus_so;
		this._filter.overdue_only = 0;
		this._filter.days_ahead = 30;
		this._panel.find(".ch-pickup-search").val(this._focus_so);
		this._panel.find(".ch-pickup-overdue").prop("checked", false);
		this._panel.find(".ch-pickup-window").val("30");
		this._focus_so = null;
		this._switch_tab("pending");
	}

	_refresh_kpis() {
		if (!PosState.pos_profile) {
			this._panel.find(".ch-pu-kpi-strip").html("");
			return;
		}
		frappe.call({
			method: "ch_pos.api.pos_api.get_prebook_pickup_kpis",
			args: { pos_profile: PosState.pos_profile, days: 30 },
			callback: (r) => {
				if (!r.message) return;
				const k = r.message;
				const card = (label, value, sub, color, klass) => `
					<div class="${klass || ""}" style="flex:1;min-width:140px;background:#fff;border:1px solid var(--pos-border);
						border-left:3px solid ${color};border-radius:var(--pos-radius);padding:10px 12px;${klass ? "cursor:pointer;" : ""}">
						<div class="text-muted" style="font-size:11px;text-transform:uppercase;letter-spacing:0.3px;">${label}</div>
						<div style="font-size:20px;font-weight:600;margin-top:2px;">${value}</div>
						<div class="text-muted" style="font-size:11px;margin-top:2px;">${sub || ""}</div>
					</div>
				`;
				this._panel.find(".ch-pu-kpi-strip").html(`
					${card(__("Open Pickups"), k.prebook.open_count, `\u20B9${format_number(k.prebook.open_balance)} ${__("balance")}`, "#047857")}
					${card(__("Overdue"), k.prebook.overdue_count, __("needs attention"), "#dc2626")}
					${card(__("Billed Today"), k.prebook.billed_today_count, `\u20B9${format_number(k.prebook.billed_today_value)}`, "#2563eb")}
					${card(__("Reserved IMEIs"), k.reserved_serials, __("across open pre-bookings"), "#f59e0b")}
					${card(__("Proforma Open"), k.proforma.open_count, __("click to convert →"), "#0ea5e9", "ch-pu-kpi-proforma")}
				`);
			},
		});
	}

	_load() {
		if (!PosState.pos_profile) {
			this._panel.find(".ch-pickup-list").html(
				`<div class="text-muted text-center" style="padding:20px;">${__("Select a POS profile first.")}</div>`
			);
			return;
		}
		const $list = this._panel.find(".ch-pickup-list");
		$list.html(`<div class="text-muted text-center" style="padding:20px;">${__("Loading…")}</div>`);

		if (this._active_tab === "pending") {
			frappe.call({
				method: "ch_pos.api.pos_api.list_pickup_prebookings",
				args: {
					pos_profile: PosState.pos_profile,
					search: this._filter.search || null,
					days_ahead: this._filter.days_ahead,
					overdue_only: this._filter.overdue_only,
				},
				callback: (r) => {
					this._rows = r.message || [];
					this._render_pending();
				},
			});
		} else if (this._active_tab === "reserved") {
			frappe.call({
				method: "ch_pos.api.pos_api.list_reserved_serials",
				args: {
					pos_profile: PosState.pos_profile,
					search: this._filter.search || null,
					limit: 300,
				},
				callback: (r) => {
					this._reserved_rows = r.message || [];
					this._render_reserved();
				},
			});
		} else if (this._active_tab === "today") {
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Sales Invoice",
					filters: {
						posting_date: frappe.datetime.nowdate(),
						docstatus: 1,
						pos_profile: PosState.pos_profile,
					},
					fields: ["name", "customer", "customer_name", "grand_total",
						"outstanding_amount", "posting_date", "posting_time"],
					order_by: "creation desc",
					limit_page_length: 100,
				},
				callback: (r) => {
					this._today_rows = r.message || [];
					this._render_today();
				},
			});
		}
	}

	_render_pending() {
		const $list = this._panel.find(".ch-pickup-list");
		this._panel.find(".ch-pickup-count").text(
			this._rows.length ? __("{0} pending", [this._rows.length]) : ""
		);

		if (!this._rows.length) {
			$list.html(`
				<div class="text-muted text-center" style="padding:24px;">
					<i class="fa fa-inbox" style="font-size:32px;opacity:0.4;"></i>
					<div style="margin-top:8px;">${__("No pre-bookings pending pickup.")}</div>
				</div>
			`);
			return;
		}

		const rows = this._rows.map((r) => {
			const item_summary = (r.items || []).slice(0, 3).map((it) => {
				const title = `${frappe.utils.escape_html(it.item_name || it.item_code)} \u00D7 ${flt(it.qty)}`;
				const item_reserved = (it.reserved_serials || []).map((s) => frappe.utils.escape_html(s));
				const item_imei = item_reserved.length
					? `<div class="small" style="margin-top:2px;color:#1d4ed8;">
						<i class="fa fa-barcode"></i> ${item_reserved.slice(0, 2).map((s) => `<code>${s}</code>`).join(" ")}
						${item_reserved.length > 2 ? `<span class="text-muted">+${item_reserved.length - 2}</span>` : ""}
					</div>`
					: "";
				return `<div style="margin-bottom:4px;">${title}${item_imei}</div>`;
			}).join("");
			const extra = (r.items || []).length > 3
				? `<div class="small text-muted">+${(r.items || []).length - 3} ${__("more")}</div>`
				: "";

			const due_label = r.delivery_date
				? frappe.datetime.str_to_user(r.delivery_date)
				: "—";
			let due_class = "text-muted", due_badge = "";
			if (r.is_overdue) {
				due_class = "text-danger";
				due_badge = `<span class="badge badge-danger" style="background:#dc2626;color:#fff;margin-left:4px;">${__("Overdue")}</span>`;
			} else if (r.days_to_delivery !== null && r.days_to_delivery <= 2) {
				due_class = "text-warning";
			}

			const adv = flt(r.advance_paid);
			const bal = flt(r.balance_due);
			const reserve_badge = r.reserve_stock
				? `<span class="badge" style="background:#dbeafe;color:#1d4ed8;font-size:10px;margin-left:4px;">${__("Reserved")}</span>`
				: "";

			const reserved_serials = (r.reserved_serials || []);
			const requires_imei_gate = cint(r.reserve_stock) === 1;
			const imei_ready = !requires_imei_gate || reserved_serials.length > 0;
			const readiness_badge = imei_ready
				? `<span class="badge" style="background:#dcfce7;color:#166534;font-size:10px;margin-left:4px;">${__("Ready")}</span>`
				: `<span class="badge" style="background:#fee2e2;color:#991b1b;font-size:10px;margin-left:4px;">${__("Not Ready")}</span>`;
			const imei_badge = reserved_serials.length
				? `<div class="small" style="margin-top:6px;color:#1d4ed8;">
					<i class="fa fa-barcode"></i> ${reserved_serials.slice(0,3).map(s => `<code>${frappe.utils.escape_html(s)}</code>`).join(" ")}
					${reserved_serials.length > 3 ? `<span class="text-muted">+${reserved_serials.length - 3}</span>` : ""}
				</div>`
				: (requires_imei_gate
					? `<div class="small" style="margin-top:6px;color:#991b1b;">
						<i class="fa fa-exclamation-triangle"></i> ${__("Reserved IMEI not tagged yet")}
					</div>`
					: "");
			const bill_title = imei_ready
				? __("Verify IMEI and create invoice")
				: __("Cannot bill until reserved IMEI is available");
			const row_state_class = imei_ready ? "ch-pickup-row-ready" : "ch-pickup-row-not-ready";

			return `
				<div class="ch-pickup-row ${row_state_class}" style="display:flex;gap:12px;padding:12px;border-bottom:1px solid var(--pos-border);align-items:flex-start;">
					<div style="flex:1.2;min-width:180px;">
						<div style="font-weight:600;">${frappe.utils.escape_html(r.customer_name)}${reserve_badge}${readiness_badge}</div>
						<div class="small text-muted">${frappe.utils.escape_html(r.customer)}</div>
						<div class="small ${due_class}" style="margin-top:4px;">
							<i class="fa fa-calendar"></i> ${__("Due")}: ${due_label} ${due_badge}
						</div>
						<div class="small text-muted">${r.name}</div>
						${imei_badge}
					</div>
					<div style="flex:1.5;min-width:200px;font-size:12px;">
						${item_summary || `<span class="text-muted">${__("(no items)")}</span>`}
						${extra}
					</div>
					<div style="flex:0.9;min-width:140px;text-align:right;font-size:12px;">
						<div>${__("Total")}: <b>\u20B9${format_number(r.grand_total)}</b></div>
						${adv > 0 ? `<div class="text-success">${__("Advance")}: \u20B9${format_number(adv)}</div>` : ""}
						<div style="margin-top:2px;"><b>${__("Balance")}: \u20B9${format_number(bal)}</b></div>
					</div>
					<div style="flex:0;display:flex;flex-direction:column;gap:6px;min-width:140px;">
						<button class="btn btn-success btn-sm ch-pickup-bill" data-name="${r.name}" title="${frappe.utils.escape_html(bill_title)}">
							<i class="fa fa-check"></i> ${__("Bill & Pickup")}
						</button>
					</div>
				</div>
			`;
		}).join("");

		$list.html(rows);
	}

	_render_reserved() {
		const $list = this._panel.find(".ch-pickup-list");
		this._panel.find(".ch-pickup-count").text(
			this._reserved_rows.length ? __("{0} IMEI reserved", [this._reserved_rows.length]) : ""
		);
		if (!this._reserved_rows.length) {
			$list.html(`
				<div class="text-muted text-center" style="padding:24px;">
					<i class="fa fa-barcode" style="font-size:32px;opacity:0.4;"></i>
					<div style="margin-top:8px;">${__("No IMEIs/serials are currently reserved.")}</div>
				</div>
			`);
			return;
		}
		$list.html(`
			<table class="table table-condensed" style="margin:0;">
				<thead>
					<tr>
						<th>${__("IMEI / Serial")}</th>
						<th>${__("Item")}</th>
						<th>${__("Customer")}</th>
						<th>${__("Sales Order")}</th>
						<th>${__("Due")}</th>
						<th>${__("Warehouse")}</th>
					</tr>
				</thead>
				<tbody>
					${this._reserved_rows.map(r => {
						const overdue = r.is_overdue
							? `<span style="background:#fee2e2;color:#b91c1c;padding:2px 6px;border-radius:8px;font-size:10px;font-weight:600;margin-left:6px;">${__("Overdue")}</span>`
							: "";
						return `
							<tr>
								<td><code>${frappe.utils.escape_html(r.serial_no)}</code></td>
								<td>${frappe.utils.escape_html(r.item_name || r.item_code)}<div class="small text-muted">${frappe.utils.escape_html(r.item_code)}</div></td>
								<td>${frappe.utils.escape_html(r.customer_name)}<div class="small text-muted">${frappe.utils.escape_html(r.customer)}</div></td>
								<td>${frappe.utils.escape_html(r.sales_order)}</td>
								<td>${r.delivery_date || "—"}${overdue}</td>
								<td>${frappe.utils.escape_html(r.warehouse || "—")}</td>
							</tr>
						`;
					}).join("")}
				</tbody>
			</table>
		`);
	}

	_render_today() {
		const $list = this._panel.find(".ch-pickup-list");
		this._panel.find(".ch-pickup-count").text(
			this._today_rows.length ? __("{0} billed", [this._today_rows.length]) : ""
		);
		if (!this._today_rows.length) {
			$list.html(`
				<div class="text-muted text-center" style="padding:24px;">
					<i class="fa fa-check-square-o" style="font-size:32px;opacity:0.4;"></i>
					<div style="margin-top:8px;">${__("No invoices billed today on this POS profile.")}</div>
				</div>
			`);
			return;
		}
		$list.html(this._today_rows.map(r => `
			<div style="display:flex;gap:12px;padding:12px;border-bottom:1px solid var(--pos-border);align-items:center;">
				<div style="flex:1.2;min-width:180px;">
					<div style="font-weight:600;">${frappe.utils.escape_html(r.customer_name || r.customer)}</div>
					<div class="small text-muted">${frappe.utils.escape_html(r.customer)}</div>
					<div class="small text-muted" style="margin-top:4px;">
						${r.name} \u00B7 ${r.posting_time || ""}
					</div>
				</div>
				<div style="flex:0.9;min-width:140px;text-align:right;font-size:12px;">
					<div>${__("Total")}: <b>\u20B9${format_number(r.grand_total)}</b></div>
					${flt(r.outstanding_amount) > 0
						? `<div class="text-warning">${__("Outstanding")}: \u20B9${format_number(r.outstanding_amount)}</div>`
						: `<div class="text-success">${__("Fully Paid")}</div>`}
				</div>
				<div style="flex:0;display:flex;flex-direction:column;gap:6px;min-width:140px;">
					<button class="btn btn-default btn-xs ch-pickup-invoice-print"
						data-url="/printview?doctype=Sales%20Invoice&name=${encodeURIComponent(r.name)}&format=Custom%20Sales%20Invoice&no_letterhead=0">
						<i class="fa fa-print"></i> ${__("Print")}
					</button>
				</div>
			</div>
		`).join(""));
	}

	_bill_flow(row) {
		// New flow: load the Sales Order into the right-panel cart so the
		// cashier sees items + advance subtraction and bills via the regular
		// PAY (F8) flow. Reserved IMEIs still require physical scan-confirm
		// before billing — that gate is a smaller, focused dialog now.
		const reserved = (row.reserved_serials || []).map((s) => String(s).trim()).filter(Boolean);
		if (reserved.length) {
			this._confirm_imeis_then_load(row, reserved);
		} else {
			this._load_so_into_cart(row);
		}
	}

	_confirm_imeis_then_load(row, reserved) {
		const scanned = [];

		const dlg = new frappe.ui.Dialog({
			title: __("Confirm IMEI Hand-over — {0}", [row.customer_name]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "summary_html",
					options: `
						<div style="background:#f8fafc;border:1px solid var(--pos-border);
							border-radius:var(--pos-radius);padding:10px;margin-bottom:8px;">
							<div><b>${__("Sales Order")}:</b> ${row.name}</div>
							<div><b>${__("Customer")}:</b> ${frappe.utils.escape_html(row.customer_name || row.customer || "")}</div>
							<div><b>${__("Grand Total")}:</b> ₹${format_number(row.grand_total)}
								&nbsp;\u00B7&nbsp;
								<span class="text-success"><b>${__("Advance")}: ₹${format_number(row.advance_paid)}</b></span>
							</div>
						</div>`,
				},
				{
					fieldtype: "HTML",
					fieldname: "imei_scan",
					options: `
						<div class="pk-imei-confirm" style="border:1px solid #f59e0b;background:#fffbeb;
							border-radius:8px;padding:10px;">
							<div style="font-weight:600;margin-bottom:6px;">
								<i class="fa fa-barcode"></i> ${__("Scan all reserved IMEIs to continue")} (${reserved.length})
							</div>
							<input type="text" class="form-control input-sm pk-imei-input"
								placeholder="${__("Scan reserved IMEI, press Enter")}">
							<div class="pk-imei-chips" style="margin-top:6px;"></div>
							<div class="pk-imei-req text-muted" style="font-size:0.78rem;margin-top:4px;"></div>
							<div class="pk-imei-progress" style="font-size:0.78rem;margin-top:4px;font-weight:600;"></div>
						</div>`,
				},
			],
			primary_action_label: __("Load into Cart"),
			primary_action: () => {
				const missing = reserved.filter((s) => !scanned.includes(s));
				if (missing.length) {
					frappe.msgprint({
						title: __("IMEI Scan Required"),
						indicator: "orange",
						message: __("Scan all reserved IMEIs before billing: {0}", [missing.join(", ")]),
					});
					return;
				}
				dlg.hide();
				this._load_so_into_cart(row, scanned);
			},
		});
		dlg.show();

		const $w = dlg.$wrapper;
		const render = () => {
			const missing = reserved.filter((s) => !scanned.includes(s));
			const done = reserved.length - missing.length;
			$w.find(".pk-imei-chips").html(
				scanned.map((s, i) =>
					`<span class="badge" data-idx="${i}" style="background:#e0e7ff;color:#3730a3;margin:2px;cursor:pointer">${frappe.utils.escape_html(s)} ✕</span>`
				).join("")
			);
			$w.find(".pk-imei-req").html(
				reserved.map((s) => {
					const ok = scanned.includes(s);
					return `<span style="margin-right:10px;color:${ok ? "#16a34a" : "#b91c1c"}">${ok ? "✔" : "○"} <code>${frappe.utils.escape_html(s)}</code></span>`;
				}).join("")
			);
			$w.find(".pk-imei-progress").html(
				missing.length
					? `<span style="color:#b91c1c">${__("{0}/{1} IMEIs confirmed ", [done, reserved.length])}· ${__("{0} pending", [missing.length])}</span>`
					: `<span style="color:#166534">${__("All reserved IMEIs confirmed")}</span>`
			);
			if (missing.length) {
				dlg.disable_primary_action();
			} else {
				dlg.enable_primary_action();
			}
		};
		$w.find(".pk-imei-input").on("keydown", function (e) {
			if (e.key !== "Enter") return;
			e.preventDefault();
			const val = ($(this).val() || "").trim();
			if (!val) return;
			if (!reserved.includes(val)) {
				frappe.show_alert({ message: __("IMEI {0} is not reserved on this pre-booking", [val]), indicator: "red" });
				$(this).val("");
				return;
			}
			if (!scanned.includes(val)) scanned.push(val);
			$(this).val("");
			render();
		});
		$w.on("click", ".pk-imei-chips .badge", function () {
			scanned.splice($(this).data("idx"), 1);
			render();
		});
		dlg.disable_primary_action();
		render();
		setTimeout(() => $w.find(".pk-imei-input").trigger("focus"), 100);
	}

	_load_so_into_cart(row, scanned_serials) {
		if (!PosState.pos_profile) {
			frappe.show_alert({ message: __("POS Profile not loaded"), indicator: "red" });
			return;
		}
		// Cart already has items? Confirm before discarding.
		const proceed = () => this._do_load_so_into_cart(row, scanned_serials);
		if ((PosState.cart || []).length > 0) {
			frappe.confirm(
				__("The current cart has items. Discard them and load Sales Order {0}?", [row.name]),
				proceed,
			);
		} else {
			proceed();
		}
	}

	_do_load_so_into_cart(row, scanned_serials) {
		frappe.xcall("ch_pos.api.pos_api.load_sales_order_to_cart", {
			pos_profile: PosState.pos_profile,
			sales_order: row.name,
		}).then((res) => {
			if (!res || !res.items || !res.items.length) {
				frappe.show_alert({ message: __("Nothing left to bill on this Sales Order"), indicator: "orange" });
				return;
			}

			// Reset transaction, then seed from SO.
			PosState.reset_transaction();
			PosState.customer = res.customer;
			PosState.sale_type = res.sale_type || null;
			PosState.cart = res.items.map((it) => {
				// Stamp scanned IMEIs back onto matching serial rows when the
				// cashier completed the IMEI confirm step.
				if (scanned_serials && scanned_serials.length && it.has_serial_no && !it.serial_no) {
					const next = scanned_serials.shift();
					if (next) it.serial_no = String(next);
				}
				return it;
			});
			PosState.sales_order_reference = res.sales_order;
			PosState.sales_order_advance = flt(res.advance_paid || 0);
			PosState.sales_order_grand_total = flt(res.grand_total || 0);
			PosState.sales_order_summary = {
				name: res.sales_order,
				customer_name: res.customer_name,
				balance_due: flt(res.balance_due || 0),
				reserved_serials: res.reserved_serials || [],
				delivery_date: res.delivery_date || null,
			};

			// Switch to the sell workspace so the cashier sees the cart and PAY.
			EventBus.emit("mode:switch", "sell");
			EventBus.emit("customer:changed");
			EventBus.emit("cart:updated");

			frappe.show_alert({
				message: __("Sales Order {0} loaded — review & press PAY (F8)", [row.name]),
				indicator: "green",
			});
		}).catch((err) => {
			const msg = err && err.message ? err.message : __("Failed to load Sales Order");
			frappe.msgprint({ title: __("Load Failed"), indicator: "red", message: msg });
		});
	}

	_show_success(inv, auto_print) {
		const print_url = inv.print_url;

		frappe.show_alert({
			message: __("Invoice {0} created", [inv.name]),
			indicator: "green",
		}, 6);

		frappe.msgprint({
			title: __("Pickup Billed"),
			indicator: "green",
			message: `
				<div style="text-align:center;padding:12px;">
					<i class="fa fa-check-circle text-success" style="font-size:42px;"></i>
					<h4 style="margin:14px 0 6px;">${frappe.utils.escape_html(inv.name)}</h4>
					<p>${frappe.utils.escape_html(inv.customer_name || inv.customer || "")}</p>
					<p>${__("Grand Total")}: <b>₹${format_number(inv.grand_total)}</b></p>
					${flt(inv.outstanding_amount) > 0
						? `<p class="text-warning"><b>${__("Outstanding")}: ₹${format_number(inv.outstanding_amount)}</b></p>`
						: `<p class="text-success"><b>${__("Fully Paid")}</b></p>`}
					<div style="margin-top:14px;display:flex;gap:8px;justify-content:center;flex-wrap:wrap;">
						<a class="btn btn-primary btn-sm" target="_blank" href="${print_url}">
							<i class="fa fa-print"></i> ${__("Print Invoice")}
						</a>
					</div>
				</div>`,
		});

		if (auto_print) {
			window.open(print_url, "_blank");
		}
	}
}
