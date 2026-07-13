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
		// Deep-link hint: the Pickup/Bill "Proforma Open" KPI fires this so
		// the workspace opens directly on the My Proformas tab where the
		// cashier can Convert → Sale or Convert → Pre-Booking.
		EventBus.on("prebook:goto_proformas", () => {
			this._active_tab = "proformas";
			if (this._panel && this._panel.is(":visible")) {
				this._switch_tab("proformas");
			}
		});
	}

	render(panel) {
		this._panel = panel;
		// Guard: if reopening from a tab that was removed, fall back to Create.
		if (this._active_tab === "prebookings") this._active_tab = "create";
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header" style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
					<div>
						<h4>
							<span class="mode-icon" style="background:#e0f2fe;color:#0369a1">
								<i class="fa fa-bookmark"></i>
							</span>
							${__("Pre-Book / Proforma")}
						</h4>
						<span class="ch-mode-hint">${__("Quote and reserve future sales \u2014 Proforma Invoice (Quotation) or Pre-Booking (Sales Order) with stock reservation and advance.")}</span>
					</div>
					<button class="btn btn-default btn-sm ch-pb-go-pickup" title="${__("Pickup / Bill is where pre-bookings are billed at handover.")}">
						<i class="fa fa-cube"></i> ${__("Go to Pickup / Bill")}
					</button>
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
		panel.on("click", ".ch-pb-go-pickup", () => {
			EventBus.emit("mode:switch", "pickup");
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
		panel.on("click", ".ch-pb-cancel", (e) => {
			this._cancel_prebook_flow(
				$(e.currentTarget).data("name"),
				flt($(e.currentTarget).data("advance")));
		});
		// Proforma → Sale: load Quotation items into Sell cart, switch mode.
		// Cashier can then add/remove items, scan IMEIs, and press PAY.
		panel.on("click", ".ch-pb-convert-sale", (e) => {
			const name = $(e.currentTarget).data("name");
			if (name) this._convert_proforma_to_sale(name);
		});
		// Proforma → Pre-Booking: pre-fill the prebook dialog with Quotation
		// items + customer; cashier sets delivery date and collects advance.
		panel.on("click", ".ch-pb-convert-prebook", (e) => {
			const name = $(e.currentTarget).data("name");
			if (name) this._convert_proforma_to_prebook(name);
		});
	}

	_read_cart_ctx() {
		const cart = PosState.cart || [];
		const customer = PosState.customer || PosState.default_customer || "";
		const total = cart.reduce((s, it) => s + flt(it.qty || 1) * flt(it.rate || 0), 0);
		return { cart, customer, total };
	}

	/**
	 * Fetch the active selling price for an item — CH Item Price (POS
	 * channel, Active) first, then fall back to the active Price List
	 * (POS profile's selling_price_list, else Standard Selling), else
	 * Item.standard_rate.
	 *
	 * Returns { rate, mrp, stock_uom } or {} when nothing resolves.
	 */
	async _resolve_item_price(item_code) {
		if (!item_code) return {};
		try {
			const detail = await frappe.xcall(
				"ch_pos.api.search.get_item_detail_for_pos",
				{ item_code, warehouse: PosState.warehouse || null }
			);
			const rate = flt(detail?.selling_price || detail?.mrp || 0);
			if (rate > 0 || flt(detail?.mrp) > 0) {
				return { rate: flt(detail.selling_price || detail.mrp), mrp: flt(detail.mrp), stock_uom: null };
			}
		} catch (e) {
			// fall through to Item Price / standard_rate
		}
		try {
			const price_list = PosState.price_list
				|| frappe.defaults.get_default("selling_price_list")
				|| "Standard Selling";
			const ip = await frappe.db.get_value("Item Price",
				{ item_code, price_list, selling: 1 },
				"price_list_rate");
			if (ip && flt(ip.message?.price_list_rate) > 0) {
				return { rate: flt(ip.message.price_list_rate), mrp: 0, stock_uom: null };
			}
		} catch (e) { /* ignore */ }
		try {
			const item = await frappe.db.get_value("Item", item_code,
				["standard_rate", "stock_uom"]);
			return {
				rate: flt(item?.message?.standard_rate || 0),
				mrp: 0,
				stock_uom: item?.message?.stock_uom || null,
			};
		} catch (e) {
			return {};
		}
	}

	/**
	 * Return an onchange handler for the item_code child field in a
	 * Dialog Table so picking an Item auto-populates rate (from CH Item
	 * Price / Item Price) and stock UOM. Called with `this` = the child
	 * cell control (Frappe grid_row semantics). Also re-renders the
	 * dialog's html_total block if present.
	 *
	 * `get_dlg` is a thunk because the enclosing dialog is often not yet
	 * constructed at the point the field spec is built.
	 */
	_make_item_code_onchange(get_dlg, items_fieldname) {
		const workspace = this;
		return function () {
			// `this` = the item_code cell control; this.doc = row plain obj
			const row = this.doc;
			if (!row || !row.item_code) return;
			// Snapshot the item we are resolving so stale async responses
			// from a rapidly changed item do not overwrite a newer selection.
			const resolved_for = row.item_code;
			workspace._resolve_item_price(row.item_code).then((info) => {
				if (row.item_code !== resolved_for) return; // item changed again
				const dlg = get_dlg();
				const grid = dlg?.fields_dict?.[items_fieldname]?.grid;
				const grid_row = grid?.grid_rows_by_docname?.[row.name];
				if (!grid_row) return;
				const patch = {};
				// Always overwrite rate and warehouse when item changes —
				// a stale rate from the previous item must not be carried over.
				if (info.rate > 0) patch.rate = info.rate;
				else if (!flt(row.rate)) patch.rate = 0; // clear stale rate
				if (info.stock_uom) patch.uom = info.stock_uom;
				// Reset warehouse to store default on item change so the row
				// doesn't inherit the warehouse from the previously selected item.
				const default_wh = PosState.warehouse || "";
				if (default_wh) patch.warehouse = default_wh;
				Object.entries(patch).forEach(([field, value]) => {
					row[field] = value;
					grid.set_value(field, value, row);
				});
				workspace._refresh_dialog_total(dlg, items_fieldname);
				if (!info.rate) {
					frappe.show_alert({
						message: __("No selling price configured for {0}. Enter rate manually.", [row.item_code]),
						indicator: "orange",
					}, 5);
				}
			});
		};
	}

	/**
	 * Recompute the Order Total shown in the dialog's html_total HTML
	 * block from the current items table.
	 */
	_refresh_dialog_total(dlg, items_fieldname) {
		if (!dlg) return;
		const rows = dlg.get_value(items_fieldname) || [];
		const total = rows.reduce((s, r) => s + flt(r.qty || 0) * flt(r.rate || 0), 0);
		const html_field = dlg.fields_dict.html_total;
		if (!html_field) return;
		html_field.$wrapper.find(".ch-pb-order-total").text("\u20B9" + format_number(total));
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
		const has_cart = cart.length > 0;
		const rows = has_cart
			? cart.map((it) => `
				<tr>
					<td>${frappe.utils.escape_html(it.item_name || it.item_code)}</td>
					<td style="text-align:right">${flt(it.qty || 1)}</td>
					<td style="text-align:right">\u20B9${format_number(it.rate || 0)}</td>
					<td style="text-align:right"><b>\u20B9${format_number(flt(it.qty || 1) * flt(it.rate || 0))}</b></td>
				</tr>
			`).join("")
			: "";

		const cart_card = has_cart
			? `<div style="background:#fff;border:1px solid var(--pos-border);border-radius:var(--pos-radius);padding:14px;">
					<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
						<b>${__("Customer")}</b>
						<span class="text-muted">${frappe.utils.escape_html(customer || __("(no customer selected)"))}</span>
					</div>
					<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
						<b>${__("Cart Items (optional starting point)")}</b>
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
					<div class="text-muted" style="font-size:11px;margin-top:8px;">
						${__("These cart items are only a starting point. You can add more items — including out-of-stock or upcoming-launch items — inside the Pre-Booking form.")}
					</div>
				</div>`
			: `<div style="background:#fff;border:1px solid var(--pos-border);border-radius:var(--pos-radius);padding:24px;text-align:center;">
					<i class="fa fa-bookmark" style="font-size:36px;color:#0369a1;opacity:0.6;"></i>
					<h4 style="margin:12px 0 6px;">${__("Start a New Pre-Booking")}</h4>
					<p class="text-muted" style="font-size:13px;margin-bottom:16px;">
						${__("Add any item from the full Item list — including items not currently in stock (upcoming launches, back-orders, custom orders). Reserve stock when available, or tag IMEI later before billing.")}
					</p>
					<button class="btn btn-success btn-lg ch-prebook-reserve">
						<i class="fa fa-plus-circle"></i> ${__("New Pre-Booking")}
					</button>
					<button class="btn btn-default btn-lg ch-prebook-proforma" style="margin-left:8px;">
						<i class="fa fa-file-text-o"></i> ${__("New Proforma")}
					</button>
					<div class="text-muted" style="font-size:11px;margin-top:12px;">
						${__("Pre-Booking = Sales Order with reservation + advance. Proforma = non-binding quotation.")}
					</div>
				</div>`;

		return `
			<div style="display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap;">
				<div style="flex:2;min-width:380px;">
					${cart_card}
				</div>

				<div style="flex:1;min-width:280px;">
					<div style="background:#fff;border:1px solid var(--pos-border);border-radius:var(--pos-radius);padding:14px;display:flex;flex-direction:column;gap:10px;">
						<button class="btn btn-primary btn-block ch-prebook-proforma">
							<i class="fa fa-file-text-o"></i> ${__("Generate Proforma Invoice")}
						</button>
						<div class="text-muted" style="font-size:11px;margin-top:-4px;">
							${__("Creates a submitted Quotation and opens the Proforma Invoice print format. No stock reservation.")}
						</div>
						<hr style="margin:8px 0;">
						<button class="btn btn-success btn-block ch-prebook-reserve">
							<i class="fa fa-bookmark"></i> ${__("Create Pre-Booking (Reserve Stock)")}
						</button>
						<div class="text-muted" style="font-size:11px;margin-top:-4px;">
							${__("Opens an editable Pre-Booking form. Pick any item — stock availability is not required.")}
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
					<div class="small text-muted" style="margin-top:4px;">${r.name}</div>
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
				<div style="flex:0;display:flex;flex-direction:column;gap:6px;min-width:160px;">
					<button class="btn btn-default btn-xs ch-pb-print" data-url="${r.print_url}">
						<i class="fa fa-print"></i> ${__("Print")}
					</button>
					${r.status === "Open" && !r.is_expired ? `
						<button class="btn btn-primary btn-xs ch-pb-convert-sale" data-name="${r.name}" title="${__("Bill this proforma now — items load into the Sell cart; add accessories or scan IMEIs before PAY")}">
							<i class="fa fa-shopping-cart"></i> ${__("Convert → Sale")}
						</button>
						<button class="btn btn-success btn-xs ch-pb-convert-prebook" data-name="${r.name}" title="${__("Reserve stock and collect advance — opens Pre-Booking dialog pre-filled with these items")}">
							<i class="fa fa-bookmark"></i> ${__("Convert → Pre-Booking")}
						</button>
					` : ""}
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
						<div class="small text-muted" style="margin-top:4px;">${r.name}</div>
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
					<div style="flex:0;display:flex;flex-direction:column;gap:6px;min-width:120px;">
						<button class="btn btn-default btn-xs ch-pb-cancel" data-name="${r.name}" data-advance="${flt(r.advance_paid)}">
							<i class="fa fa-times-circle"></i> ${__("Cancel")}
						</button>
					</div>
				</div>
			`;
		}).join(""));
	}

	_cancel_prebook_flow(name, advance) {
		const d = new frappe.ui.Dialog({
			title: __("Cancel Pre-Booking {0}", [name]),
			fields: [
				advance > 0
					? {
						fieldtype: "Select", fieldname: "action", reqd: 1,
						label: __("Advance ₹{0} collected — what to do?", [format_number(advance)]),
						options: [__("Refund to customer"), __("Keep as credit (adjust to another bill)")].join("\n"),
						default: __("Refund to customer"),
					}
					: { fieldtype: "HTML", fieldname: "noadv", options: `<div class="text-muted">${__("No advance collected on this pre-booking.")}</div>` },
				{
					fieldtype: "Link", fieldname: "refund_mode", label: __("Refund Mode"), options: "Mode of Payment",
					depends_on: `eval:doc.action == '${__("Refund to customer")}'`,
					description: __("Posts the cash / UPI refund back to the customer."),
				},
				{ fieldtype: "Small Text", fieldname: "reason", label: __("Reason") },
			],
			primary_action_label: __("Cancel Pre-Booking"),
			primary_action: (v) => {
				const retain = v.action === __("Keep as credit (adjust to another bill)");
				d.hide();
				frappe.confirm(
					__("Cancel pre-booking {0}? Any reserved stock is released.", [name]),
					() => {
						frappe.call({
							method: "ch_pos.api.pos_api.cancel_pre_booking",
							args: {
								sales_order: name,
								action: retain ? "retain_credit" : "refund",
								refund_mode_of_payment: retain ? null : (v.refund_mode || null),
								reason: v.reason || null,
							},
							freeze: true,
							freeze_message: __("Cancelling…"),
							callback: (r) => {
								if (!r.message) return;
								const m = r.message;
								frappe.show_alert({
									message: retain
										? __("Cancelled — ₹{0} kept as customer credit.", [format_number(m.retained)])
										: __("Cancelled — advance handled."),
									indicator: "orange",
								});
								this._reload_list();
							},
						});
					});
			},
		});
		d.show();
	}

	_proforma_flow(cart, customer) {
		let seed_items = (cart || []).map((it) => ({
			item_code: it.item_code,
			qty: flt(it.qty || 1),
			rate: flt(it.rate || 0),
			uom: it.uom || "Nos",
			warehouse: it.warehouse || PosState.warehouse || "",
		}));
		if (!seed_items.length) {
			seed_items = [{
				item_code: "",
				qty: 1,
				rate: 0,
				uom: "Nos",
				warehouse: PosState.warehouse || "",
			}];
		}
		const initial_total = (seed_items || []).reduce(
			(s, it) => s + flt(it.qty || 0) * flt(it.rate || 0), 0,
		);
		let dlg;
		const get_dlg = () => dlg;
		dlg = new frappe.ui.Dialog({
			title: __("Generate Proforma Invoice"),
			size: "extra-large",
			fields: [
				{
					fieldname: "customer", fieldtype: "Link", options: "Customer",
					label: __("Customer"), reqd: 1, default: customer,
				},
				{
					fieldname: "valid_till", fieldtype: "Date", label: __("Valid Till"),
					default: frappe.datetime.add_days(frappe.datetime.nowdate(), 15),
				},
				{ fieldname: "section_break_items", fieldtype: "Section Break", label: __("Proforma Items") },
				{
					fieldname: "items_hint",
					fieldtype: "HTML",
					options: `<div class="text-muted" style="font-size:12px;margin-bottom:6px;">${__("Select Item to search the full item list (not limited by current sellable stock).")}</div>`,
				},
				{
					fieldname: "proforma_items",
					fieldtype: "Table",
					label: __("Items"),
					reqd: 1,
					in_place_edit: 1,
					data: seed_items,
					fields: [
						{
							fieldname: "item_code",
							fieldtype: "Link",
							options: "Item",
							label: __("Item"),
							reqd: 1,
							in_list_view: 1,
							get_query: () => ({ filters: { disabled: 0, is_sales_item: 1 } }),
							onchange: this._make_item_code_onchange(get_dlg, "proforma_items"),
						},
						{
							fieldname: "qty", fieldtype: "Float", label: __("Qty"),
							reqd: 1, in_list_view: 1, default: 1,
							onchange: () => this._refresh_dialog_total(get_dlg(), "proforma_items"),
						},
						{
							fieldname: "rate", fieldtype: "Currency", label: __("Rate"),
							reqd: 1, in_list_view: 1, default: 0,
							onchange: () => this._refresh_dialog_total(get_dlg(), "proforma_items"),
						},
						{ fieldname: "uom", fieldtype: "Data", label: __("UOM"), in_list_view: 1, default: "Nos" },
						{ fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: __("Warehouse"), in_list_view: 1 },
					],
				},
				{ fieldname: "section_break_b", fieldtype: "Section Break" },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Terms / Notes") },
				{
					fieldname: "html_total", fieldtype: "HTML",
					options: `<div style="text-align:right;padding:6px 0;"><b>${__("Order Total")}:</b> <span class="ch-pb-order-total">\u20B9${format_number(initial_total)}</span></div>
						<div class="text-muted" style="font-size:11px;text-align:right;">${__("Proforma is a non-binding quote. Collect advance via Pre-Booking only.")}</div>`,
				},
			],
			primary_action_label: __("Generate"),
			primary_action: (v) => {
				const table_rows = (v.proforma_items || [])
					.filter((r) => r && r.item_code && flt(r.qty) > 0)
					.map((r) => ({
						item_code: r.item_code,
						qty: flt(r.qty || 1),
						rate: flt(r.rate || 0),
						uom: r.uom || "Nos",
						warehouse: r.warehouse || PosState.warehouse || null,
					}));
				if (!table_rows.length) {
					frappe.show_alert({
						message: __("Add at least one item in the proforma table."),
						indicator: "red",
					});
					return;
				}
				frappe.call({
					method: "ch_pos.api.pos_api.create_pos_quotation",
					args: {
						pos_profile: PosState.pos_profile,
						customer: v.customer,
						items: table_rows,
						valid_till: v.valid_till,
						notes: v.notes,
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
		frappe.msgprint({
			title: __("Proforma Created"),
			indicator: "green",
			message: `
				<div style="text-align:center;padding:12px;">
					<i class="fa fa-check-circle text-success" style="font-size:42px;"></i>
					<h4 style="margin:14px 0 6px;">${frappe.utils.escape_html(qtn.name)}</h4>
					<p>${__("Grand Total")}: <b>₹${format_number(qtn.grand_total)}</b></p>
					<p class="text-muted" style="font-size:12px;">${__("Convert to Pre-Booking to collect advance and reserve stock.")}</p>
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
		let seed_items = (cart || []).map((it) => ({
			item_code: it.item_code,
			qty: flt(it.qty || 1),
			rate: flt(it.rate || 0),
			uom: it.uom || "Nos",
			warehouse: it.warehouse || PosState.warehouse || "",
			serial_no: String(it.serial_no || "").trim(),
		}));
		if (!seed_items.length) {
			seed_items = [{
				item_code: "",
				qty: 1,
				rate: 0,
				uom: "Nos",
				warehouse: PosState.warehouse || "",
				serial_no: "",
			}];
		}
		const initial_total = (seed_items || []).reduce((s, it) => s + flt(it.qty || 0) * flt(it.rate || 0), 0);

		let dlg;
		const get_dlg = () => dlg;
		dlg = new frappe.ui.Dialog({
			title: __("Create Pre-Booking (Reserve Stock)"),
			size: "extra-large",
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
					description: __("Optional. A Payment Entry is created against the Sales Order."),
				},
				{
					fieldname: "reserve_stock", fieldtype: "Check",
					label: __("Reserve Stock"), default: 1,
				},
				{ fieldname: "section_break_items", fieldtype: "Section Break", label: __("Pre-Booking Items") },
				{
					fieldname: "items_hint",
					fieldtype: "HTML",
					options: `<div class="text-muted" style="font-size:12px;margin-bottom:6px;">${__("Select Item to search the full item list (not limited by current sellable stock).")}</div>`,
				},
				{
					fieldname: "prebook_items",
					fieldtype: "Table",
					label: __("Items"),
					reqd: 1,
					in_place_edit: 1,
					data: seed_items,
					fields: [
						{
							fieldname: "item_code",
							fieldtype: "Link",
							options: "Item",
							label: __("Item"),
							reqd: 1,
							in_list_view: 1,
							get_query: () => ({ filters: { disabled: 0, is_sales_item: 1 } }),
							onchange: this._make_item_code_onchange(get_dlg, "prebook_items"),
						},
						{
							fieldname: "qty", fieldtype: "Float", label: __("Qty"),
							reqd: 1, in_list_view: 1, default: 1,
							onchange: () => this._refresh_dialog_total(get_dlg(), "prebook_items"),
						},
						{
							fieldname: "rate", fieldtype: "Currency", label: __("Rate"),
							reqd: 1, in_list_view: 1, default: 0,
							onchange: () => this._refresh_dialog_total(get_dlg(), "prebook_items"),
						},
						{ fieldname: "uom", fieldtype: "Data", label: __("UOM"), in_list_view: 1, default: "Nos" },
						{ fieldname: "warehouse", fieldtype: "Link", options: "Warehouse", label: __("Warehouse"), in_list_view: 1 },
						{ fieldname: "serial_no", fieldtype: "Data", label: __("IMEI / Serial"), in_list_view: 1 },
					],
				},
				{ fieldname: "section_break_pay", fieldtype: "Section Break" },
				{
					fieldname: "payments_html", fieldtype: "HTML",
					options: `<div class="ch-pb-pay-block"></div>`,
				},
				// Hidden payload populated by the inline split-tender UI.
				{ fieldname: "payments_json", fieldtype: "Data", hidden: 1, default: "[]" },
				{ fieldname: "section_break_b", fieldtype: "Section Break" },
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes") },
				{
					fieldname: "html_total", fieldtype: "HTML",
					options: `<div style="text-align:right;padding:6px 0;"><b>${__("Order Total")}:</b> <span class="ch-pb-order-total">\u20B9${format_number(initial_total || total || 0)}</span></div>
						<div class="text-muted" style="font-size:11px;text-align:right;">${__("Order total is computed from the item rows above.")}</div>`,
				},
			],
			primary_action_label: __("Create Pre-Booking"),
			primary_action: (v) => {
				const table_rows = (v.prebook_items || [])
					.filter((r) => r && r.item_code && flt(r.qty) > 0)
					.map((r) => ({
						item_code: r.item_code,
						qty: flt(r.qty || 1),
						rate: flt(r.rate || 0),
						uom: r.uom || "Nos",
						warehouse: r.warehouse || PosState.warehouse || null,
						serial_no: String(r.serial_no || "").trim(),
					}));

				if (!table_rows.length) {
					frappe.show_alert({
						message: __("Add at least one item in the pre-booking table."),
						indicator: "red",
					});
					return;
				}

				// Parse split-tender rows from the inline UI's hidden payload
				let payments = [];
				try { payments = JSON.parse(v.payments_json || "[]"); } catch (e) { payments = []; }
				payments = (payments || [])
					.filter((p) => p && p.mode_of_payment && flt(p.amount) > 0)
					.map((p) => ({
						mode_of_payment: p.mode_of_payment,
						amount: flt(p.amount),
						reference_no: (p.reference_no || "").trim() || null,
					}));

				const advance = flt(v.advance_amount);
				if (advance > 0) {
					// Fallback safety: if UI rows were not added yet, auto-create one
					// default payment row so cashiers are never blocked in submit.
					if (!payments.length) {
						const fallback_modes = (PosState.payment_modes || [])
							.map((m) => (m.mode_of_payment || "").trim())
							.filter(Boolean);
						const fallback_default = ((PosState.payment_modes || []).find((m) => m.default)
							|| {}).mode_of_payment || fallback_modes[0] || "Cash";
						payments = [{
							mode_of_payment: fallback_default,
							amount: advance,
							reference_no: null,
						}];
					}
					if (!payments.length) {
						frappe.show_alert({
							message: __("Add at least one payment row for the advance amount."),
							indicator: "red",
						});
						return;
					}
					const allocated = payments.reduce((s, p) => s + flt(p.amount), 0);
					if (Math.abs(allocated - advance) > 0.01) {
						frappe.show_alert({
							message: __("Allocated ₹{0} must equal advance ₹{1}.", [
								format_number(allocated), format_number(advance),
							]),
							indicator: "red",
						});
						return;
					}
				}

				const reserve_stock = v.reserve_stock ? 1 : 0;
				const duplicate_serials = [];
				const seen_serials = new Set();
				const items_payload = table_rows.map((it) => {
					const serial_no = String(it.serial_no || "").trim();
					if (serial_no) {
						if (seen_serials.has(serial_no)) duplicate_serials.push(serial_no);
						seen_serials.add(serial_no);
					}
					return {
						item_code: it.item_code,
						qty: flt(it.qty || 1),
						rate: flt(it.rate || 0),
						uom: it.uom || "Nos",
						warehouse: it.warehouse,
						serial_no,
					};
				});

				if (duplicate_serials.length) {
					frappe.show_alert({
						message: __("Duplicate IMEI/Serial in pre-booking cart: {0}", [
							[...new Set(duplicate_serials)].join(", "),
						]),
						indicator: "red",
					});
					return;
				}

				frappe.call({
					method: "ch_pos.api.pos_api.create_pre_booking",
					args: {
						pos_profile: PosState.pos_profile,
						customer: v.customer,
						items: items_payload,
						delivery_date: v.delivery_date,
						advance_amount: flt(v.advance_amount),
						payments: payments,
						notes: v.notes,
						reserve_stock,
					},
					freeze: true,
					freeze_message: __("Creating Pre-Booking..."),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						const so = r.message;
						PosState.reset_transaction();
						this._show_prebook_success(so);
					},
				});
			},
		});
		dlg.show();
		this._mount_advance_payments_ui(dlg);
	}

	_show_prebook_success(so) {
		const so_name = so.name || __("Sales Order");
		const so_url = `/app/sales-order/${encodeURIComponent(so_name)}`;
		const so_print = `/printview?doctype=Sales%20Order&name=${encodeURIComponent(so_name)}&no_letterhead=0`;
		const pending_rows = (so.serial_pending_rows || []).filter(Boolean);
		const qty_pending_rows = (so.qty_pending_rows || []).filter(Boolean);
		const pe_details = (so.advance_payment_entries_detail || []).filter(Boolean);
		const pe_names = pe_details.length
			? pe_details.map((d) => d.name).filter(Boolean)
			: (so.advance_payment_entries || []).filter(Boolean);
		const pe_state_map = {};
		pe_details.forEach((d) => {
			if (!d || !d.name) return;
			pe_state_map[d.name] = {
				receipt_state: d.receipt_state || (cint(d.docstatus) === 1 ? "Final" : "Draft"),
				docstatus: cint(d.docstatus || 0),
			};
		});
		const pe_links = pe_names.length
			? pe_names.map((pe) => {
				const open_url = `/app/payment-entry/${encodeURIComponent(pe)}`;
				const print_url = `/printview?doctype=Payment%20Entry&name=${encodeURIComponent(pe)}&no_letterhead=0`;
				const st = pe_state_map[pe] || { receipt_state: "Draft", docstatus: 0 };
				const is_final = cint(st.docstatus) === 1 || String(st.receipt_state || "").toLowerCase() === "final";
				const badge = `<span class="badge" style="background:${is_final ? "#dcfce7" : "#fef3c7"};color:${is_final ? "#166534" : "#92400e"};margin-left:6px;">${is_final ? __("Receipt Final") : __("Receipt Draft")}</span>`;
				return `
					<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:6px;">
						<span style="font-size:12px;">${frappe.utils.escape_html(pe)} ${badge}</span>
						<span style="display:flex;gap:6px;">
							<a class="btn btn-default btn-xs" target="_blank" href="${open_url}">
								<i class="fa fa-external-link"></i> ${__("Open")}
							</a>
							<a class="btn btn-default btn-xs" target="_blank" href="${print_url}">
								<i class="fa fa-print"></i> ${__("Print Receipt")}
							</a>
						</span>
					</div>`;
			}).join("")
			: `<div class="text-muted" style="font-size:12px;">${__("No advance receipt generated.")}</div>`;

		const pending_html = pending_rows.length
			? `<div style="margin-top:10px;padding:10px;border:1px solid #f59e0b;border-radius:8px;background:#fffbeb;">
				<div style="font-weight:600;color:#92400e;margin-bottom:6px;">
					<i class="fa fa-exclamation-triangle"></i> ${__("IMEI Pending Items")}
				</div>
				<div class="text-muted" style="font-size:12px;margin-bottom:6px;">
					${__("Pre-booking is created as launch/backorder. Tag IMEI before billing in Pickup flow.")}
				</div>
				<div style="font-size:12px;">
					${pending_rows.map((r) => {
						const label = frappe.utils.escape_html(r.item_name || r.item_code || "");
						const code = frappe.utils.escape_html(r.item_code || "");
						return `<div style="margin:2px 0;">• <b>${label}</b> <span class="text-muted">(${code})</span> — ${__("Need")}: ${cint(r.qty)} · ${__("Assigned")}: ${cint(r.assigned)} · <span style="color:#b91c1c;">${__("Pending")}: ${cint(r.pending)}</span></div>`;
					}).join("")}
				</div>
			</div>`
			: "";

		const qty_pending_html = qty_pending_rows.length
			? `<div style="margin-top:10px;padding:10px;border:1px solid #fb923c;border-radius:8px;background:#fff7ed;">
				<div style="font-weight:600;color:#9a3412;margin-bottom:6px;">
					<i class="fa fa-hourglass-half"></i> ${__("Backorder Qty Pending")}
				</div>
				<div class="text-muted" style="font-size:12px;margin-bottom:6px;">
					${__("No physical stock in the source warehouse — pre-booking is accepted without a stock reservation. Fulfilment resumes when supply arrives (matches SAP MTO / Oracle ATP / Zoho backorder).")}
				</div>
				<div style="font-size:12px;">
					${qty_pending_rows.map((r) => {
						const label = frappe.utils.escape_html(r.item_name || r.item_code || "");
						const code = frappe.utils.escape_html(r.item_code || "");
						const wh = frappe.utils.escape_html(r.warehouse || "");
						return `<div style="margin:2px 0;">• <b>${label}</b> <span class="text-muted">(${code})</span> — ${__("Warehouse")}: ${wh} · ${__("Need")}: ${cint(r.qty)} · ${__("On-hand")}: ${cint(r.available)} · <span style="color:#b91c1c;">${__("Backorder")}: ${cint(r.pending)}</span></div>`;
					}).join("")}
				</div>
			</div>`
			: "";

		frappe.msgprint({
			title: __("Pre-Booking Created"),
			indicator: so.docstatus === 1 ? "green" : "orange",
			message: `
				<div style="padding:8px 2px;">
					<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;">
						<div>
							<div style="font-weight:700;font-size:16px;">${frappe.utils.escape_html(so_name)}</div>
							<div class="text-muted" style="font-size:12px;">
								${__("Delivery")}: ${frappe.utils.escape_html(so.delivery_date || "-")} ·
								${__("Stock reserved")}: ${so.reserve_stock ? __("Yes") : __("No")}
							</div>
						</div>
						<div style="font-size:12px;text-align:right;">
							<div>${__("Advance")}: <b>₹${format_number(so.advance_amount || 0)}</b></div>
							<div>${__("Status")}: <b>${frappe.utils.escape_html(so.status || "-")}</b></div>
						</div>
					</div>
					${so.warning ? `<div class="text-warning" style="font-size:12px;margin-top:8px;">${frappe.utils.escape_html(so.warning)}</div>` : ""}
					<div style="margin-top:10px;padding:8px;border:1px solid #e5e7eb;border-radius:8px;background:#fafafa;">
						<div style="font-weight:600;margin-bottom:6px;">${__("Advance Receipt(s)")}</div>
						${pe_links}
						<div class="text-muted" style="font-size:11px;margin-top:6px;">
							${__("If workflow is enabled, receipt docs may remain Draft until checker approval.")}
						</div>
					</div>
					<div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
						<a class="btn btn-default btn-sm" target="_blank" href="${so_url}">
							<i class="fa fa-external-link"></i> ${__("Open Sales Order")}
						</a>
						<a class="btn btn-default btn-sm" target="_blank" href="${so_print}">
							<i class="fa fa-print"></i> ${__("Print Pre-Booking")}
						</a>
					</div>
					${pending_html}
					${qty_pending_html}
				</div>`,
			primary_action: {
				label: __("Open Pickup / Bill Queue"),
				action: () => {
					EventBus.emit("pickup:focus_so", so_name);
					EventBus.emit("mode:switch", "pickup");
				},
			},
		});
	}

	/**
	 * Mount the inline split-tender block inside the Pre-Booking dialog.
	 * Mirrors the main PaymentDialog UX — MOP quick-add buttons (Cash / UPI /
	 * Card / …) feed rows of {mode, amount, reference}. The hidden
	 * `payments_json` field carries the canonical payload to the backend.
	 *
	 * Industry parity (SAP Retail / Oracle Xstore / GoFrugal):
	 *   advance/deposit collection is a *mini-tender* on the booking form,
	 *   not a single forced MOP — cashiers can split UPI + cash.
	 */
	_mount_advance_payments_ui(dlg) {
		const $wrap = dlg.$wrapper.find(".ch-pb-pay-block");
		if (!$wrap.length) return;

		const modes = (PosState.payment_modes || [])
			.map((m) => (m.mode_of_payment || "").trim())
			.filter(Boolean);
		const fallback = modes.length ? modes : ["Cash", "UPI", "Credit Card"];
		const default_mop = ((PosState.payment_modes || []).find((m) => m.default)
			|| {}).mode_of_payment || fallback[0];

		let rows = []; // [{ mode_of_payment, amount, reference_no }]

		const _mop_kind = (name) => {
			const lc = (name || "").toLowerCase();
			if (lc.includes("upi")) return "upi";
			if (lc.includes("card")) return "card";
			if (lc.includes("cash")) return "cash";
			if (lc.includes("cheque")) return "cheque";
			if (lc.includes("wallet")) return "wallet";
			if (lc.includes("finance") || lc.includes("emi")) return "finance";
			return "other";
		};
		const _mop_icon = (name) => ({
			upi: "fa fa-mobile-alt",
			card: "fa fa-credit-card",
			cash: "fa fa-money-bill",
			cheque: "fa fa-money-check",
			wallet: "fa fa-wallet",
			finance: "fa fa-handshake",
			other: "fa fa-circle",
		})[_mop_kind(name)];
		const _needs_ref = (name) => {
			const k = _mop_kind(name);
			return k === "upi" || k === "card" || k === "cheque";
		};

		const sync_payload = () => dlg.set_value("payments_json", JSON.stringify(rows));

		const remaining_advance = () => {
			const advance = flt(dlg.get_value("advance_amount"));
			const allocated = rows.reduce((s, p) => s + flt(p.amount), 0);
			return Math.max(0, advance - allocated);
		};

		const update_totals_only = () => {
			const advance = flt(dlg.get_value("advance_amount"));
			const allocated = rows.reduce((s, p) => s + flt(p.amount), 0);
			const balance = advance - allocated;
			const balance_cls = Math.abs(balance) < 0.01 ? "text-success"
				: (balance > 0 ? "text-warning" : "text-danger");
			$wrap.find(".ch-pb-pay-card-head").html(`
				<div style="font-weight:600;">${__("Collect Advance")}</div>
				<div style="font-size:12px;">
					<span class="text-muted">${__("Allocated")}:</span>
					<b>₹${format_number(allocated)}</b>
					<span class="text-muted" style="margin-left:8px;">${__("Balance")}:</span>
					<b class="${balance_cls}">₹${format_number(Math.abs(balance))}</b>
				</div>
			`);
		};

		const add_row = (mop) => {
			const advance = flt(dlg.get_value("advance_amount"));
			if (advance <= 0) {
				frappe.show_alert({
					message: __("Enter Advance Amount first."),
					indicator: "orange",
				});
				return;
			}
			const left = remaining_advance();
			const amt = rows.length === 0 ? advance : left;
			if (amt <= 0) {
				frappe.show_alert({
					message: __("Advance is fully allocated. Remove a row to add another."),
					indicator: "orange",
				});
				return;
			}
			rows.push({
				mode_of_payment: mop || default_mop,
				amount: amt,
				reference_no: "",
			});
			render();
		};

		const render = () => {
			const advance = flt(dlg.get_value("advance_amount"));
			const btns_html = fallback.map((m) => `
				<button type="button" class="btn btn-default btn-sm ch-pb-mop-btn"
				        data-mop="${frappe.utils.escape_html(m)}"
				        style="margin:2px;display:inline-flex;align-items:center;gap:6px;">
					<i class="${_mop_icon(m)}"></i> ${frappe.utils.escape_html(m)}
				</button>`).join("");

			const rows_html = rows.length === 0
				? `<div class="text-muted" style="padding:8px 4px;font-size:12px;">
					${advance > 0
						? __("Tap a payment mode above to split the advance, or keep single-row default.")
						: __("Enter Advance Amount first, then choose payment mode(s).")}
				</div>`
				: `<table class="table table-sm" style="margin-bottom:6px;">
					<thead><tr>
						<th style="width:32%;">${__("Mode")}</th>
						<th style="width:24%;text-align:right;">${__("Amount")}</th>
						<th>${__("Reference")}</th>
						<th style="width:36px;"></th>
					</tr></thead>
					<tbody>
					${rows.map((r, i) => `
						<tr data-idx="${i}">
							<td>
								<select class="form-control input-sm ch-pb-row-mop">
									${fallback.map((m) => `
										<option value="${frappe.utils.escape_html(m)}"
										        ${m === r.mode_of_payment ? "selected" : ""}>
											${frappe.utils.escape_html(m)}
										</option>`).join("")}
								</select>
							</td>
							<td>
								<input type="number" step="0.01" min="0"
								       class="form-control input-sm ch-pb-row-amt text-right"
								       value="${flt(r.amount)}">
							</td>
							<td>
								<input type="text" class="form-control input-sm ch-pb-row-ref"
								       placeholder="${_needs_ref(r.mode_of_payment) ? __("UPI / RRN / Cheque #") : __("optional")}"
								       value="${frappe.utils.escape_html(r.reference_no || "")}">
							</td>
							<td style="text-align:center;">
								<button type="button" class="btn btn-link btn-xs ch-pb-row-del"
								        title="${__("Remove")}">
									<i class="fa fa-times text-danger"></i>
								</button>
							</td>
						</tr>`).join("")}
					</tbody>
				</table>`;

			$wrap.html(`
				<div class="ch-pb-pay-card" style="border:1px solid var(--border-color,#e7e7e9);border-radius:8px;padding:10px 12px;background:var(--bg-light,#fafafa);">
					<div class="ch-pb-pay-card-head" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;"></div>
					<div class="text-muted" style="font-size:11px;margin-bottom:8px;">
						${__("Advance Payment Split")} · ${__("Supports Cash/UPI/Card split in a single booking.")}
					</div>
					<div class="ch-pb-mop-btns" style="margin-bottom:8px;">${btns_html}</div>
					${rows_html}
				</div>
			`);
			update_totals_only();
			sync_payload();
		};

		// Re-render when the advance amount changes; auto-fit a single row.
		if (dlg.fields_dict.advance_amount) {
			dlg.fields_dict.advance_amount.df.onchange = () => {
				const advance = flt(dlg.get_value("advance_amount"));
				if (advance <= 0) {
					rows = [];
				} else if (rows.length === 0) {
					rows = [{
						mode_of_payment: default_mop,
						amount: advance,
						reference_no: "",
					}];
				} else if (rows.length === 1) {
					rows[0].amount = advance;
				}
				render();
			};
		}

		// Event delegation for the dynamic block
		$wrap.on("click", ".ch-pb-mop-btn", (e) => {
			add_row($(e.currentTarget).data("mop"));
		});
		$wrap.on("change", ".ch-pb-row-mop", (e) => {
			const i = parseInt($(e.currentTarget).closest("tr").data("idx"), 10);
			if (rows[i]) {
				rows[i].mode_of_payment = $(e.currentTarget).val();
				render();
			}
		});
		$wrap.on("input change", ".ch-pb-row-amt", (e) => {
			const i = parseInt($(e.currentTarget).closest("tr").data("idx"), 10);
			if (rows[i]) {
				rows[i].amount = flt($(e.currentTarget).val());
				update_totals_only();
				sync_payload();
			}
		});
		$wrap.on("input change", ".ch-pb-row-ref", (e) => {
			const i = parseInt($(e.currentTarget).closest("tr").data("idx"), 10);
			if (rows[i]) {
				rows[i].reference_no = $(e.currentTarget).val();
				sync_payload();
			}
		});
		$wrap.on("click", ".ch-pb-row-del", (e) => {
			const i = parseInt($(e.currentTarget).closest("tr").data("idx"), 10);
			rows.splice(i, 1);
			render();
		});

		render();
	}

	// -----------------------------------------------------------------------
	// Proforma → Sale / Pre-Booking conversion
	// -----------------------------------------------------------------------
	// Industry parity (SAP SD "Create with Reference", Oracle Xstore "Convert
	// Quote", MS D365 Retail "Quote → Sales Order", Zoho/Odoo/GoFrugal/Tally
	// "Convert to Invoice", ERPNext core "Make → Sales Invoice"). All seven
	// systems converge on: (1) load source items into an editable target,
	// (2) allow add/remove items + change IMEI/serial before commit,
	// (3) preserve audit linkage back to the source Quotation.
	//
	// In ch_pos this maps cleanly to:
	//   * Convert → Sale: seed Sell cart, switch to sell mode. Cashier scans
	//     IMEIs / adds accessories / presses PAY. Source quotation reference
	//     is stamped on each cart row (`source_quotation`, `quotation_item`).
	//   * Convert → Pre-Booking: open the existing prebook dialog pre-filled,
	//     letting the cashier set delivery date + collect advance with the
	//     split-tender mini-tender.
	// -----------------------------------------------------------------------
	_load_quotation(name) {
		return frappe.xcall("ch_pos.api.pos_api.load_quotation_to_cart", {
			pos_profile: PosState.pos_profile,
			quotation: name,
		});
	}

	_confirm_discard_cart(message) {
		return new Promise((resolve) => {
			if (!(PosState.cart || []).length) return resolve(true);
			frappe.confirm(message, () => resolve(true), () => resolve(false));
		});
	}

	_convert_proforma_to_sale(name) {
		if (!PosState.pos_profile) {
			frappe.show_alert({ message: __("POS Profile not loaded"), indicator: "red" });
			return;
		}
		this._confirm_discard_cart(
			__("The current cart has items. Discard them and load Proforma {0}?", [name]),
		).then((ok) => {
			if (!ok) return;
			this._load_quotation(name).then((res) => {
				if (!res || !res.items || !res.items.length) {
					frappe.show_alert({
						message: __("Proforma has no items to convert"),
						indicator: "orange",
					});
					return;
				}
				if (res.warning) {
					frappe.show_alert({ message: res.warning, indicator: "orange" });
				}
				// Seed the Sell cart with proforma items + stamp linkage.
				PosState.reset_transaction();
				PosState.customer = res.customer;
				PosState.cart = res.items;
				PosState.source_quotation = res.quotation;
				PosState.source_quotation_total = flt(res.grand_total || 0);
				EventBus.emit("mode:switch", "sell");
				EventBus.emit("customer:set", res.customer);
				EventBus.emit("cart:updated");
				frappe.show_alert({
					message: __("Loaded {0} from Proforma {1} — scan IMEIs and PAY",
						[res.item_count, res.quotation]),
					indicator: "green",
				});
			}).catch((err) => {
				console.error("[ch_pos] Proforma → Sale failed", err);
			});
		});
	}

	_convert_proforma_to_prebook(name) {
		if (!PosState.pos_profile) {
			frappe.show_alert({ message: __("POS Profile not loaded"), indicator: "red" });
			return;
		}
		this._load_quotation(name).then((res) => {
			if (!res || !res.items || !res.items.length) {
				frappe.show_alert({
					message: __("Proforma has no items to convert"),
					indicator: "orange",
				});
				return;
			}
			if (res.warning) {
				frappe.show_alert({ message: res.warning, indicator: "orange" });
			}
			// Open prebook dialog pre-filled. The dialog reads from the cart
			// array we pass in — no PosState mutation needed here.
			const cart = res.items.map((it) => ({
				item_code: it.item_code,
				item_name: it.item_name,
				qty: flt(it.qty || 1),
				rate: flt(it.rate || 0),
				uom: it.uom || "Nos",
				warehouse: it.warehouse,
				has_serial_no: cint(it.has_serial_no || 0),
				serial_no: (it.serial_no || "").trim(),
				source_quotation: res.quotation,
				quotation_item: it.quotation_item,
			}));
			this._prebook_flow(cart, res.customer, flt(res.grand_total || 0));
		}).catch((err) => {
			console.error("[ch_pos] Proforma → Pre-Booking failed", err);
		});
	}
}
