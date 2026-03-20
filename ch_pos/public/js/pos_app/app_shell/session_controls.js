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
			PosState.device = session_data.device || null;
			PosState.session_status = "Open";
			if (session_data.company) {
				PosState.company = session_data.company;
			}
		});

		EventBus.on("session:locked", () => {
			PosState.session_status = "Locked";
			this._update_info();
		});

		EventBus.on("session:unlocked", () => {
			PosState.session_status = "Open";
			this._update_info();
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
				${PosState.company ? `<div class="text-muted" style="font-size:0.72rem"><i class="fa fa-building-o"></i> <span class="ch-company">${frappe.utils.escape_html(PosState.company)}</span></div>` : ""}
				${PosState.device ? `<div class="text-muted" style="font-size:0.72rem"><i class="fa fa-desktop"></i> <span class="ch-device">${frappe.utils.escape_html(PosState.device)}</span></div>` : ""}
				<div class="ch-session-status-badge" style="font-size:0.72rem;margin-top:2px"></div>
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
				<button class="btn btn-xs btn-warning ch-btn-lock-session" title="${__("Lock Session (temporary pause)")}">
					<i class="fa fa-lock"></i> ${__("Lock Session")}
				</button>
				<button class="btn btn-xs btn-info ch-btn-unlock-session" title="${__("Unlock Session")}" style="display:none">
					<i class="fa fa-unlock"></i> ${__("Unlock Session")}
				</button>
				<button class="btn btn-xs btn-default ch-btn-settlement" title="${__("Settlement (EOD Cash Reconciliation)")}">
					<i class="fa fa-calculator"></i> ${__("Settlement")}
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

		// Status badge
		const status = PosState.session_status || "Open";
		const status_color = {
			Open: "green", Locked: "orange", "Pending Close": "blue", Closed: "red",
		}[status] || "gray";
		panel.find(".ch-session-status-badge").html(
			`<span class="indicator-pill ${status_color}">${__(status)}</span>`
		);

		// Toggle lock/unlock buttons based on status
		const is_locked = status === "Locked";
		panel.find(".ch-btn-lock-session").toggle(!is_locked);
		panel.find(".ch-btn-unlock-session").toggle(is_locked);

		// Disable transactional buttons when locked
		panel.find(".ch-btn-cash-drop, .ch-btn-switch-user").prop("disabled", is_locked);
	}

	_bind_buttons(container) {
		container.find(".ch-btn-x-report").on("click", () => this._show_x_report());
		container.find(".ch-btn-cash-drop").on("click", () => this._show_cash_drop());
		container.find(".ch-btn-switch-user").on("click", () => this._show_switch_user());
		container.find(".ch-btn-lock-session").on("click", () => this._lock_session());
		container.find(".ch-btn-unlock-session").on("click", () => this._unlock_session());
		container.find(".ch-btn-settlement").on("click", () => this._show_settlement());
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

	_lock_session() {
		if (!PosState.session_name) return frappe.msgprint(__("No active session"));
		frappe.confirm(
			__("Lock this session? No transactions can be made until it is unlocked."),
			() => {
				frappe.xcall("ch_pos.api.isolation_api.lock_session", {
					session_name: PosState.session_name,
				}).then(() => {
					PosState.session_status = "Locked";
					EventBus.emit("session:locked");
					frappe.show_alert({ message: __("Session locked"), indicator: "orange" });
				});
			}
		);
	}

	_unlock_session() {
		if (!PosState.session_name) return frappe.msgprint(__("No active session"));
		frappe.xcall("ch_pos.api.isolation_api.unlock_session", {
			session_name: PosState.session_name,
		}).then(() => {
			PosState.session_status = "Open";
			EventBus.emit("session:unlocked");
			frappe.show_alert({ message: __("Session unlocked"), indicator: "green" });
		});
	}

	_show_settlement() {
		if (!PosState.session_name) return frappe.msgprint(__("No active session"));

		// First fetch X Report data for the settlement context
		frappe.xcall("ch_pos.api.session_api.get_x_report", {
			session_name: PosState.session_name,
		}).then((data) => {
			this._render_settlement_dialog(data);
		}).catch((err) => {
			console.error("Settlement load error:", err);
			frappe.msgprint({
				title: __("Settlement Error"),
				message: err.message || err.exc || __("Failed to load session data for settlement"),
				indicator: "red",
			});
		});
	}

	_render_settlement_dialog(x_data) {
		const DENOMS = [2000, 500, 200, 100, 50, 20, 10, 5, 2, 1];
		const denom_rows = DENOMS.map((d) => `
			<div class="d-flex align-items-center mb-2" style="gap:12px">
				<span style="width:60px;font-weight:600;text-align:right">₹${d}</span>
				<span style="color:var(--text-muted)">×</span>
				<input type="number" class="form-control ch-settle-denom"
					data-denom="${d}" min="0" value="0"
					style="width:80px;text-align:center">
				<span style="color:var(--text-muted)">=</span>
				<span class="ch-settle-denom-total" data-denom="${d}" style="width:90px;text-align:right;font-weight:500">₹0</span>
			</div>
		`).join("");

		const dlg = new frappe.ui.Dialog({
			title: __("Settlement — {0}", [x_data.business_date]),
			size: "extra-large",
			fields: [
				{
					fieldname: "summary_html",
					fieldtype: "HTML",
					options: `<div class="row" style="font-size:0.9rem">
						<div class="col-md-6">
							<table class="table table-sm table-borderless">
								<tr><td class="text-muted">${__("Opening Cash")}</td><td>${frappe.format(x_data.opening_cash, { fieldtype: "Currency" })}</td></tr>
								<tr><td class="text-muted">${__("Cash Sales")}</td><td>${frappe.format(x_data.cash_in_drawer - x_data.opening_cash + x_data.total_cash_drops, { fieldtype: "Currency" })}</td></tr>
								<tr><td class="text-muted">${__("Cash Drops")}</td><td>- ${frappe.format(x_data.total_cash_drops, { fieldtype: "Currency" })}</td></tr>
								<tr><td class="text-muted"><b>${__("Expected Cash")}</b></td><td><b>${frappe.format(x_data.cash_in_drawer, { fieldtype: "Currency" })}</b></td></tr>
							</table>
						</div>
						<div class="col-md-6">
							<table class="table table-sm table-borderless">
								<tr><td class="text-muted">${__("Invoices")}</td><td>${x_data.invoices_count}</td></tr>
								<tr><td class="text-muted">${__("Net Sales")}</td><td>${frappe.format(x_data.net_sales, { fieldtype: "Currency" })}</td></tr>
							</table>
						</div>
					</div>`,
				},
				{ fieldtype: "Section Break", label: __("Denomination Count") },
				{
					fieldname: "denom_html",
					fieldtype: "HTML",
					options: `<div style="max-height:320px;overflow-y:auto">${denom_rows}
					<hr><div class="d-flex align-items-center" style="gap:12px">
						<span style="width:60px;font-weight:700;text-align:right">${__("Total")}</span>
						<span></span><span></span><span></span>
						<span class="ch-settle-grand-total" style="font-size:1.2rem;font-weight:700;color:var(--primary)">₹0</span>
					</div></div>`,
				},
				{ fieldtype: "Section Break" },
				{
					fieldname: "actual_closing_cash",
					fieldtype: "Currency",
					label: __("Actual Closing Cash (₹)"),
					read_only: 1,
					default: 0,
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "variance_html",
					fieldtype: "HTML",
					options: `<div class="ch-settle-variance" style="font-size:1.1rem;padding:8px 0"></div>`,
				},
				{ fieldtype: "Section Break" },
				{
					fieldname: "variance_reason",
					fieldtype: "Small Text",
					label: __("Variance Reason"),
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "manager_pin",
					fieldtype: "Password",
					label: __("Manager PIN"),
					description: __("Required if variance exceeds threshold"),
				},
			],
			primary_action_label: __("Submit Settlement"),
			primary_action: (values) => {
				const denominations = [];
				dlg.$wrapper.find(".ch-settle-denom").each(function () {
					const count = parseInt($(this).val()) || 0;
					if (count > 0) {
						denominations.push({
							denomination: parseInt($(this).data("denom")),
							quantity: count,
						});
					}
				});

				dlg.disable_primary_action();
				frappe.call({
					method: "ch_pos.api.isolation_api.create_settlement",
					args: {
						session_name: PosState.session_name,
						actual_closing_cash: values.actual_closing_cash || 0,
						denominations: JSON.stringify(denominations),
						variance_reason: values.variance_reason || "",
						manager_pin: values.manager_pin || null,
					},
					callback: (r) => {
						if (r.message) {
							dlg.hide();
							PosState.session_status = "Pending Close";
							this._update_info();
							frappe.show_alert({
								message: __("Settlement submitted. Session is now Pending Close."),
								indicator: "blue",
							});
						}
					},
					error: (err) => {
						dlg.enable_primary_action();
						const msg = err && err.message ? err.message : __("Failed to submit settlement. Check console for details.");
						frappe.msgprint({ title: __("Settlement Error"), message: msg, indicator: "red" });
					},
				});
			},
		});

		dlg.show();

		// Bind denomination inputs
		setTimeout(() => {
			dlg.$wrapper.find(".ch-settle-denom").on("input", () => {
				let total = 0;
				dlg.$wrapper.find(".ch-settle-denom").each(function () {
					const denom = parseInt($(this).data("denom"));
					const count = Math.max(0, parseInt($(this).val()) || 0);
					const subtotal = denom * count;
					dlg.$wrapper.find(`.ch-settle-denom-total[data-denom="${denom}"]`).text(
						`₹${subtotal.toLocaleString("en-IN")}`
					);
					total += subtotal;
				});
				dlg.$wrapper.find(".ch-settle-grand-total").text(`₹${total.toLocaleString("en-IN")}`);
				dlg.set_value("actual_closing_cash", total);

				const expected = x_data.cash_in_drawer || 0;
				const variance = total - expected;
				const abs_var = Math.abs(variance);
				const color = abs_var === 0 ? "green" : abs_var <= 100 ? "orange" : "red";
				dlg.$wrapper.find(".ch-settle-variance").html(`
					<span style="color:var(--${color === "green" ? "dark-green" : color})">
						${__("Variance")}: ₹${variance.toLocaleString("en-IN")}
						${abs_var > 100 ? `<small>(${__("manager approval needed")})</small>` : ""}
					</span>
				`);
			});
		}, 100);
	}

	_show_close_session() {
		if (!PosState.session_name) return frappe.msgprint(__("No active session"));
		this._closing_dashboard.show(PosState.session_name);
	}

	destroy() {
		this._closing_dashboard.destroy();
	}
}
