/**
 * CH POS — Pickup / Bill Workspace
 *
 * For store staff: shows all submitted Pre-Bookings (Sales Orders) that are
 * still pending pickup/billing, and lets the cashier convert one to a POS
 * Sales Invoice in a single click — collecting the balance payment and
 * pulling in any advance already paid on the SO.
 *
 * Reuse-first: backend wrappers live in ch_pos/api/pos_api.py
 *   - list_pickup_prebookings(pos_profile, search, days_ahead, overdue_only)
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
		this._rows = [];
		this._filter = { search: "", days_ahead: 30, overdue_only: 0 };
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "pickup") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this._panel = panel;
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#ecfdf5;color:#047857">
							<i class="fa fa-cube"></i>
						</span>
						${__("Pickup / Bill")}
					</h4>
					<span class="ch-mode-hint">${__("Customer is here to pick up a pre-booking? Find it below and bill it in one click.")}</span>
				</div>

				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-body">
						<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
							<input type="text" class="form-control ch-pickup-search"
								placeholder="${__("Search SO #, customer, tracking…")}"
								style="flex:1;min-width:240px;">
							<select class="form-control ch-pickup-window" style="width:180px;">
								<option value="7">${__("Due in 7 days")}</option>
								<option value="30" selected>${__("Due in 30 days")}</option>
								<option value="90">${__("Due in 90 days")}</option>
								<option value="0">${__("All upcoming")}</option>
							</select>
							<label style="display:inline-flex;align-items:center;gap:6px;margin:0;">
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
						<span><i class="fa fa-list"></i> ${__("Pending Pickups")}</span>
						<span class="ch-pickup-count text-muted small"></span>
					</div>
					<div class="section-body ch-pickup-list">
						<div class="text-muted text-center" style="padding:20px;">${__("Loading…")}</div>
					</div>
				</div>
			</div>
		`);

		this._bind(panel);
		this._load();
	}

	_bind(panel) {
		let debounce = null;
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
		panel.on("click", ".ch-pickup-refresh", () => this._load());
		panel.on("click", ".ch-pickup-open", (e) => {
			const name = $(e.currentTarget).data("name");
			window.open(`/app/sales-order/${encodeURIComponent(name)}`, "_blank");
		});
		panel.on("click", ".ch-pickup-bill", (e) => {
			const name = $(e.currentTarget).data("name");
			const row = this._rows.find(r => r.name === name);
			if (row) this._bill_flow(row);
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
				this._render_list();
			},
		});
	}

	_render_list() {
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
			const item_summary = (r.items || []).slice(0, 3).map(
				(it) => `${frappe.utils.escape_html(it.item_name || it.item_code)} × ${flt(it.qty)}`
			).join("<br>");
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

			return `
				<div class="ch-pickup-row" style="display:flex;gap:12px;padding:12px;border-bottom:1px solid var(--pos-border);align-items:flex-start;">
					<div style="flex:1.2;min-width:180px;">
						<div style="font-weight:600;">${frappe.utils.escape_html(r.customer_name)}${reserve_badge}</div>
						<div class="small text-muted">${frappe.utils.escape_html(r.customer)}</div>
						<div class="small ${due_class}" style="margin-top:4px;">
							<i class="fa fa-calendar"></i> ${__("Due")}: ${due_label} ${due_badge}
						</div>
						<div class="small text-muted">
							<a href="/app/sales-order/${encodeURIComponent(r.name)}" target="_blank">${r.name}</a>
						</div>
					</div>
					<div style="flex:1.5;min-width:200px;font-size:12px;">
						${item_summary || `<span class="text-muted">${__("(no items)")}</span>`}
						${extra}
					</div>
					<div style="flex:0.9;min-width:140px;text-align:right;font-size:12px;">
						<div>${__("Total")}: <b>₹${format_number(r.grand_total)}</b></div>
						${adv > 0 ? `<div class="text-success">${__("Advance")}: ₹${format_number(adv)}</div>` : ""}
						<div style="margin-top:2px;"><b>${__("Balance")}: ₹${format_number(bal)}</b></div>
					</div>
					<div style="flex:0;display:flex;flex-direction:column;gap:6px;min-width:140px;">
						<button class="btn btn-success btn-sm ch-pickup-bill" data-name="${r.name}">
							<i class="fa fa-check"></i> ${__("Bill & Pickup")}
						</button>
						<button class="btn btn-default btn-xs ch-pickup-open" data-name="${r.name}">
							<i class="fa fa-external-link"></i> ${__("Open SO")}
						</button>
					</div>
				</div>
			`;
		}).join("");

		$list.html(rows);
	}

	_bill_flow(row) {
		const balance = flt(row.balance_due);
		const advance = flt(row.advance_paid);

		const dlg = new frappe.ui.Dialog({
			title: __("Bill & Pickup — {0}", [row.customer_name]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "summary_html",
					options: `
						<div style="background:#f8fafc;border:1px solid var(--pos-border);
							border-radius:var(--pos-radius);padding:10px;margin-bottom:8px;">
							<div><b>${__("Sales Order")}:</b> ${row.name}</div>
							<div><b>${__("Grand Total")}:</b> ₹${format_number(row.grand_total)}</div>
							${advance > 0 ? `<div class="text-success"><b>${__("Advance already paid")}:</b> ₹${format_number(advance)}</div>` : ""}
							<div><b>${__("Balance due now")}:</b> ₹${format_number(balance)}</div>
						</div>`,
				},
				{
					fieldtype: "Link",
					fieldname: "mode_of_payment",
					label: __("Collect Balance Via"),
					options: "Mode of Payment",
					default: "Cash",
					description: __("Leave the amount as 0 to create the invoice without collecting payment now."),
				},
				{
					fieldtype: "Currency",
					fieldname: "paid_amount",
					label: __("Amount Collected"),
					default: balance > 0 ? balance : 0,
				},
				{ fieldtype: "Column Break" },
				{
					fieldtype: "Check",
					fieldname: "apply_advance",
					label: __("Apply Advance Paid"),
					default: advance > 0 ? 1 : 0,
				},
				{
					fieldtype: "Check",
					fieldname: "print_after",
					label: __("Print Invoice After Billing"),
					default: 1,
				},
			],
			primary_action_label: __("Create Invoice"),
			primary_action: (v) => {
				const args = {
					pos_profile: PosState.pos_profile,
					sales_order: row.name,
					mode_of_payment: v.mode_of_payment || null,
					paid_amount: flt(v.paid_amount || 0),
					apply_advance: v.apply_advance ? 1 : 0,
					client_request_id: frappe.utils.get_random(20),
				};
				frappe.call({
					method: "ch_pos.api.pos_api.convert_prebooking_to_invoice",
					args,
					freeze: true,
					freeze_message: __("Creating invoice…"),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						this._show_success(r.message, !!v.print_after);
						this._load();
					},
				});
			},
		});
		dlg.show();
	}

	_show_success(inv, auto_print) {
		const desk_url = `/app/sales-invoice/${encodeURIComponent(inv.name)}`;
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
						<a class="btn btn-default btn-sm" target="_blank" href="${desk_url}">
							<i class="fa fa-external-link"></i> ${__("Open in Desk")}
						</a>
					</div>
				</div>`,
		});

		if (auto_print) {
			window.open(print_url, "_blank");
		}
	}
}
