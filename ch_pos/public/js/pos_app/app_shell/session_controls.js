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
		this._container = null;
		this._bind_events();
		this.render();
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
			EventBus.emit("session:status_changed", PosState.session_status);
			this.render();
		});

		EventBus.on("session:locked", () => {
			PosState.session_status = "Locked";
			EventBus.emit("session:status_changed", PosState.session_status);
			this.render();
		});

		EventBus.on("session:unlocked", () => {
			PosState.session_status = "Open";
			EventBus.emit("session:status_changed", PosState.session_status);
			this.render();
		});

		EventBus.on("profile:loaded", () => this.render());
		EventBus.on("sessionbar:rendered", (wrapper) => {
			this._container = wrapper;
			this.render(wrapper);
		});
	}

	_get_container(container) {
		if (container) {
			this._container = container;
		}
		if (this._container?.length) {
			return this._container;
		}
		const wrapper = $(".ch-pos-session-bar");
		if (wrapper.length) {
			this._container = wrapper;
		}
		return this._container;
	}

	_can_manage_session() {
		const exec = PosState.executive_access?.own_executive || {};
		const role = String(exec.role || "").toLowerCase();
		return Boolean(
			PosState.executive_access?.is_manager
			|| /manager|head|admin|owner/.test(role)
			|| (frappe.user_roles || []).includes("System Manager")
		);
	}

	render(container) {
		const wrapper = this._get_container(container);
		if (!wrapper?.length) return;

		const slot = wrapper.find(".ch-session-profile-slot");
		if (!slot.length) return;

		const can_manage = this._can_manage_session();
		const exec = PosState.executive_access?.own_executive || {};
		const display_name = exec.executive_name || frappe.session.user_fullname || frappe.session.user;
		const company = PosState.active_company || PosState.company || __("No Company");
		const initials = (display_name || "U")
			.split(" ")
			.map((part) => part[0])
			.join("")
			.substring(0, 2)
			.toUpperCase();
		const is_locked = PosState.session_status === "Locked";
		const has_session = Boolean(PosState.session_name);

		slot.html(`
			<div class="ch-session-profile-tools">
				<button class="ch-session-walkin-quick" title="${__("Log Walk-in")}">
					<i class="fa fa-sign-in"></i>
					<span>${__("Log Walk-in")}</span>
				</button>
				<div class="ch-session-profile-shell${can_manage ? " is-manager" : " is-readonly"}">
					<button class="ch-session-profile-trigger${can_manage ? " is-manager" : ""}" type="button"
						title="${can_manage ? __("Session actions") : __("Logged in user")}">
						<span class="ch-session-profile-avatar">${frappe.utils.escape_html(initials)}</span>
						<span class="ch-session-profile-meta">
							<span class="ch-session-profile-name">${frappe.utils.escape_html(display_name)}</span>
							<span class="ch-session-profile-company">${frappe.utils.escape_html(company)}</span>
						</span>
						${can_manage ? `<i class="fa fa-chevron-down ch-session-profile-caret"></i>` : ""}
					</button>
					${can_manage ? `
						<div class="ch-session-profile-dropdown">
							<button class="ch-session-menu-item ch-btn-x-report" ${has_session ? "" : "disabled"}>
								<i class="fa fa-file-text-o"></i>
								<span>${__("X Report")}</span>
							</button>
							<button class="ch-session-menu-item ch-btn-cash-drop" ${(has_session && !is_locked) ? "" : "disabled"}>
								<i class="fa fa-money"></i>
								<span>${__("Cash Drop")}</span>
							</button>
							<button class="ch-session-menu-item ch-btn-switch-company">
								<i class="fa fa-building-o"></i>
								<span>${__("Switch Company")}</span>
							</button>
							<button class="ch-session-menu-item ch-btn-switch-user" ${(has_session && !is_locked) ? "" : "disabled"}>
								<i class="fa fa-user"></i>
								<span>${__("Switch Cashier")}</span>
							</button>
							<button class="ch-session-menu-item ch-session-menu-lock" ${has_session ? "" : "disabled"}>
								<i class="fa ${is_locked ? "fa-unlock" : "fa-lock"}"></i>
								<span class="ch-session-menu-label">${is_locked ? __("Unlock Session") : __("Lock Session")}</span>
							</button>
							<button class="ch-session-menu-item ch-btn-settlement" ${has_session ? "" : "disabled"}>
								<i class="fa fa-calculator"></i>
								<span>${__("Settlement")}</span>
							</button>
							<button class="ch-session-menu-item ch-btn-close-session is-danger" ${has_session ? "" : "disabled"}>
								<i class="fa fa-power-off"></i>
								<span>${__("Close Session")}</span>
							</button>
						</div>` : ""}
				</div>
			</div>
		`);

		this._bind_buttons(slot);
		this._update_info();
	}

	_update_info() {
		if (!this._container?.length) return;
		const slot = this._container.find(".ch-session-profile-slot");
		if (!slot.length) return;

		const status = PosState.session_status || "Open";
		const is_locked = status === "Locked";
		const has_session = Boolean(PosState.session_name);

		slot.find(".ch-btn-x-report, .ch-btn-settlement, .ch-btn-close-session, .ch-session-menu-lock").prop("disabled", !has_session);
		slot.find(".ch-btn-cash-drop, .ch-btn-switch-user").prop("disabled", !has_session || is_locked);
		slot.find(".ch-session-menu-lock i").attr("class", `fa ${is_locked ? "fa-unlock" : "fa-lock"}`);
		slot.find(".ch-session-menu-label").text(is_locked ? __("Unlock Session") : __("Lock Session"));
	}

	_bind_buttons(container) {
		container.find(".ch-session-walkin-quick").on("click", () => EventBus.emit("walkin:open"));
		container.find(".ch-session-profile-trigger.is-manager").on("click", (e) => {
			e.preventDefault();
			e.stopPropagation();
			container.find(".ch-session-profile-shell").toggleClass("open");
		});

		$(document).off("click.ch_pos_profile_menu").on("click.ch_pos_profile_menu", (e) => {
			if (!$(e.target).closest(".ch-session-profile-shell").length) {
				$(".ch-session-profile-shell").removeClass("open");
			}
		});

		container.find(".ch-btn-x-report").on("click", () => {
			container.find(".ch-session-profile-shell").removeClass("open");
			this._show_x_report();
		});
		container.find(".ch-btn-cash-drop").on("click", () => {
			container.find(".ch-session-profile-shell").removeClass("open");
			this._show_cash_drop();
		});
		container.find(".ch-btn-switch-company").on("click", () => {
			container.find(".ch-session-profile-shell").removeClass("open");
			this._show_switch_company();
		});
		container.find(".ch-btn-switch-user").on("click", () => {
			container.find(".ch-session-profile-shell").removeClass("open");
			this._show_switch_user();
		});
		container.find(".ch-session-menu-lock").on("click", () => {
			container.find(".ch-session-profile-shell").removeClass("open");
			if (PosState.session_status === "Locked") {
				this._unlock_session();
			} else {
				this._lock_session();
			}
		});
		container.find(".ch-btn-settlement").on("click", () => {
			container.find(".ch-session-profile-shell").removeClass("open");
			this._show_settlement();
		});
		container.find(".ch-btn-close-session").on("click", () => {
			container.find(".ch-session-profile-shell").removeClass("open");
			this._show_close_session();
		});
	}

	_show_switch_company() {
		// Build list of accessible companies
		const access = PosState.executive_access;
		let companies = [];

		if (access && access.companies && access.companies.length) {
			companies = access.companies.map(c => c.company);
		}

		// System Manager with no executive records — fetch all companies
		if (!companies.length && (frappe.user_roles || []).includes("System Manager")) {
			frappe.xcall("frappe.client.get_list", {
				doctype: "Company",
				fields: ["name"],
				limit_page_length: 0,
			}).then(result => {
				const all_companies = (result || []).map(r => r.name);
				this._open_company_dialog(all_companies);
			});
			return;
		}

		if (companies.length <= 1) {
			frappe.show_alert({ message: __("Only one company available"), indicator: "blue" });
			return;
		}

		this._open_company_dialog(companies);
	}

	_open_company_dialog(companies) {
		const current = PosState.active_company || PosState.company || "";
		const options = companies.map(c => c).join("\n");

		const d = new frappe.ui.Dialog({
			title: __("Switch Company"),
			fields: [
				{
					fieldname: "company",
					fieldtype: "Select",
					label: __("Company"),
					options: options,
					default: current,
					reqd: 1,
				},
			],
			primary_action_label: __("Switch"),
			primary_action: (values) => {
				d.hide();
				if (values.company === current) return;

				PosState.active_company = values.company;
				PosState.company = values.company;

				// Update company_type from server-provided data
				const access = PosState.executive_access;
				const entry = access && access.companies
					? access.companies.find(c => c.company === values.company)
					: null;
				PosState.active_company_type = entry ? entry.company_type : null;

				// Update company display in session bar
				const short = values.company.replace(/ Pvt Ltd| Private Limited| Ltd/gi, "").trim();
				$(".ch-session-profile-company").text(short);

				// Re-render sidebar modes and cart executive bar
				EventBus.emit("company:switched", values.company);
				EventBus.emit("profile:loaded", PosState);

				frappe.show_alert({
					message: __("Switched to {0}", [short]),
					indicator: "green",
				});
			},
		});
		d.show();
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
				}).catch((err) => {
					console.error("Lock session error:", err);
					frappe.msgprint({ title: __("Lock Failed"), message: err.message || __("Failed to lock session"), indicator: "red" });
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
		}).catch((err) => {
			console.error("Unlock session error:", err);
			frappe.msgprint({ title: __("Unlock Failed"), message: err.message || __("Failed to unlock session"), indicator: "red" });
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
