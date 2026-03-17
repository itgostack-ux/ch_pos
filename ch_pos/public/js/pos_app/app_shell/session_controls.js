/**
 * CH POS — Session Controls
 *
 * Adds session-related actions to the POS sidebar:
 * - Business date display
 * - Session info (cashier, shift start)
 * - Cash Drop button
 * - X Report (interim)
 * - Close Session button
 * - Switch Cashier
 */
import { PosState, EventBus } from "../state.js";
import { SessionClosingDashboard } from "../shared/session_closing_dashboard.js";

export class SessionControls {
	constructor() {
		this._closing_dashboard = new SessionClosingDashboard();
		this._bind_events();
	}

	_bind_events() {
		EventBus.on("session:loaded", (session_data) => {
			PosState.session_name = session_data.session_name;
			PosState.business_date = session_data.business_date;
			PosState.store = session_data.store;
		});

		// Re-render into sidebar whenever it redraws
		EventBus.on("sidebar:rendered", (wrapper) => {
			if (PosState.session_name) {
				this.render(wrapper);
			}
		});
	}

	/**
	 * Render the session info panel at the bottom of the sidebar.
	 */
	render(container) {
		this._container = container;
		const html = `
		<div class="ch-session-controls" style="padding:12px;border-top:1px solid var(--border-color);font-size:0.82rem">
			<div class="ch-session-info" style="margin-bottom:8px">
				<div style="font-weight:700;color:var(--primary);margin-bottom:4px">
					<i class="fa fa-calendar"></i>
					<span class="ch-biz-date">${PosState.business_date || ""}</span>
				</div>
				<div class="text-muted ch-session-id" style="font-size:0.75rem"></div>
			</div>
			<div class="ch-session-actions" style="display:flex;flex-direction:column;gap:6px">
				<button class="btn btn-xs btn-default ch-btn-x-report" title="${__("X Report (Interim)")}">
					<i class="fa fa-file-text-o"></i> ${__("X Report")}
				</button>
				<button class="btn btn-xs btn-default ch-btn-cash-drop" title="${__("Cash Drop to Safe")}">
					<i class="fa fa-money"></i> ${__("Cash Drop")}
				</button>
				<button class="btn btn-xs btn-default ch-btn-switch-user" title="${__("Switch Cashier")}">
					<i class="fa fa-user"></i> ${__("Switch Cashier")}
				</button>
				<button class="btn btn-xs btn-danger ch-btn-close-session" title="${__("Close Session")}">
					<i class="fa fa-power-off"></i> ${__("Close Session")}
				</button>
			</div>
		</div>`;

		container.append(html);
		this._bind_buttons(container);
		this._update_info();
	}

	_update_info() {
		if (!this._container) return;
		const panel = this._container.find(".ch-session-controls");
		panel.find(".ch-biz-date").text(PosState.business_date || __("No date"));
		panel.find(".ch-session-id").text(PosState.session_name || "");
	}

	_bind_buttons(container) {
		container.find(".ch-btn-x-report").on("click", () => this._show_x_report());
		container.find(".ch-btn-cash-drop").on("click", () => this._show_cash_drop());
		container.find(".ch-btn-switch-user").on("click", () => this._show_switch_user());
		container.find(".ch-btn-close-session").on("click", () => this._show_close_session());
	}

	_show_x_report() {
		if (!PosState.session_name) return frappe.msgprint(__("No active session"));
		frappe.xcall("ch_pos.api.session_api.get_x_report", {
			session_name: PosState.session_name,
		}).then((data) => {
			const payment_rows = (data.payment_modes || [])
				.map((p) => `<tr><td>${frappe.utils.escape_html(p.mode)}</td><td class="text-right">${frappe.format(p.total, { fieldtype: "Currency" })}</td></tr>`)
				.join("");

			frappe.msgprint({
				title: __("X Report — {0}", [data.business_date]),
				message: `
				<div style="font-size:0.9rem">
					<table class="table table-sm">
						<tr><td>${__("Session")}</td><td><b>${data.session_name}</b></td></tr>
						<tr><td>${__("Cashier")}</td><td>${frappe.utils.escape_html(data.cashier)}</td></tr>
						<tr><td>${__("Shift Start")}</td><td>${data.shift_start}</td></tr>
						<tr><td>${__("Opening Cash")}</td><td>${frappe.format(data.opening_cash, { fieldtype: "Currency" })}</td></tr>
					</table>
					<hr>
					<table class="table table-sm">
						<tr><td>${__("Invoices")}</td><td><b>${data.invoices_count}</b></td></tr>
						<tr><td>${__("Total Sales")}</td><td>${frappe.format(data.total_sales, { fieldtype: "Currency" })}</td></tr>
						<tr><td>${__("Returns")}</td><td>${data.returns_count} (${frappe.format(data.total_returns, { fieldtype: "Currency" })})</td></tr>
						<tr><td><b>${__("Net Sales")}</b></td><td><b>${frappe.format(data.net_sales, { fieldtype: "Currency" })}</b></td></tr>
						<tr><td>${__("Tax Collected")}</td><td>${frappe.format(data.total_tax, { fieldtype: "Currency" })}</td></tr>
					</table>
					<hr>
					<h6>${__("Payment Modes")}</h6>
					<table class="table table-sm">${payment_rows}</table>
					<hr>
					<table class="table table-sm">
						<tr><td>${__("Cash Drops")}</td><td>${frappe.format(data.total_cash_drops, { fieldtype: "Currency" })}</td></tr>
						<tr><td><b>${__("Cash in Drawer")}</b></td><td><b>${frappe.format(data.cash_in_drawer, { fieldtype: "Currency" })}</b></td></tr>
					</table>
				</div>`,
				wide: true,
			});
		});
	}

	_show_cash_drop() {
		if (!PosState.session_name) return frappe.msgprint(__("No active session"));
		const dlg = new frappe.ui.Dialog({
			title: __("Cash Drop to Safe"),
			fields: [
				{
					fieldname: "amount",
					fieldtype: "Currency",
					label: __("Amount (₹)"),
					reqd: 1,
				},
				{
					fieldname: "reason",
					fieldtype: "Small Text",
					label: __("Reason"),
					reqd: 1,
					default: "Excess cash — moving to safe",
				},
				{ fieldtype: "Section Break", label: __("Manager Approval") },
				{
					fieldname: "manager_pin",
					fieldtype: "Password",
					label: __("Manager PIN"),
					reqd: 1,
				},
			],
			primary_action_label: __("Submit Cash Drop"),
			primary_action: (values) => {
				dlg.disable_primary_action();
				frappe.call({
					method: "ch_pos.api.session_api.create_cash_drop",
					args: {
						session_name: PosState.session_name,
						amount: values.amount,
						reason: values.reason,
						manager_pin: values.manager_pin,
					},
					callback: (r) => {
						if (r.message) {
							dlg.hide();
							frappe.show_alert({
								message: __("Cash drop of ₹{0} recorded. Approved by {1}.", [
									r.message.amount, r.message.approved_by,
								]),
								indicator: "green",
							});
						}
					},
					error: () => dlg.enable_primary_action(),
				});
			},
		});
		dlg.show();
	}

	_show_switch_user() {
		if (!PosState.session_name) return frappe.msgprint(__("No active session"));
		const dlg = new frappe.ui.Dialog({
			title: __("Switch Cashier"),
			fields: [
				{
					fieldname: "info",
					fieldtype: "HTML",
					options: `<div class="text-muted mb-3">${__("Current cashier")}: <b>${frappe.session.user}</b></div>`,
				},
				{
					fieldname: "new_user",
					fieldtype: "Link",
					label: __("New Cashier"),
					options: "User",
					reqd: 1,
				},
				{
					fieldname: "manager_pin",
					fieldtype: "Password",
					label: __("Manager PIN"),
					reqd: 1,
				},
			],
			primary_action_label: __("Switch"),
			primary_action: (values) => {
				frappe.call({
					method: "ch_pos.api.session_api.switch_user",
					args: {
						session_name: PosState.session_name,
						new_user: values.new_user,
						manager_pin: values.manager_pin,
					},
					callback: (r) => {
						if (r.message) {
							dlg.hide();
							frappe.show_alert({
								message: __("Cashier switched to {0}", [r.message.user]),
								indicator: "green",
							});
						}
					},
				});
			},
		});
		dlg.show();
	}

	_show_close_session() {
		if (!PosState.session_name) return frappe.msgprint(__("No active session"));
		this._closing_dashboard.show(PosState.session_name);
	}

	destroy() {
		this._closing_dashboard.destroy();
	}
}
