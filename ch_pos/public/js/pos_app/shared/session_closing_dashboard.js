/**
 * CH POS — Session Closing Dashboard
 *
 * Full-screen closing experience:
 * 1. Shows X Report summary (sales, payments, drops)
 * 2. Denomination-wise cash counting
 * 3. Variance display with auto-allow < ₹100
 * 4. Manager PIN for variance > ₹100
 * 5. Reason entry for discrepancies
 */
import { PosState, EventBus } from "../state.js";

const DENOMINATIONS = [2000, 500, 200, 100, 50, 20, 10, 5, 2, 1];

export class SessionClosingDashboard {
	constructor() {
		this._panel = null;
	}

	show(session_name) {
		this._session_name = session_name;
		// Fetch X Report data first
		frappe.xcall("ch_pos.api.session_api.get_x_report", { session_name })
			.then((data) => this._render(data))
			.catch((err) => {
				console.error("Session close error:", err);
				frappe.msgprint({
					title: __("Failed to load session data"),
					message: err.message || err.exc || JSON.stringify(err),
					indicator: "red",
				});
			});
	}

	_render(data) {
		this._data = data;

		const dlg = new frappe.ui.Dialog({
			title: __("Close Session — {0}", [data.business_date]),
			size: "extra-large",
			fields: [
				{
					fieldname: "summary_html",
					fieldtype: "HTML",
					options: this._build_summary_html(data),
				},
				{ fieldtype: "Section Break", label: __("Cash Denomination Count") },
				{
					fieldname: "denom_html",
					fieldtype: "HTML",
					options: this._build_denomination_html(data),
				},
				{ fieldtype: "Section Break", label: __("Closing") },
				{
					fieldname: "closing_cash",
					fieldtype: "Currency",
					label: __("Total Counted Cash (₹)"),
					read_only: 1,
					default: 0,
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "variance_display",
					fieldtype: "HTML",
					options: `<div class="ch-variance-display" style="font-size:1.1rem;padding:8px 0"></div>`,
				},
				{ fieldtype: "Section Break" },
				{
					fieldname: "variance_reason",
					fieldtype: "Small Text",
					label: __("Variance Reason"),
					description: __("Required if variance > ₹100"),
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "manager_pin",
					fieldtype: "Data",
					label: __("Manager PIN"),
					options: "",
					description: __("Required if variance > ₹100"),
				},
			],
			primary_action_label: __("Close Session"),
			primary_action: (values) => this._submit_closing(dlg, values),
		});

		dlg.show();
		this._dialog = dlg;

		// Bind denomination counting
		setTimeout(() => this._bind_denomination_inputs(dlg, data), 100);
	}

	_build_summary_html(d) {
		const payment_rows = (d.payment_modes || [])
			.map((p) => `<tr><td>${frappe.utils.escape_html(p.mode)}</td><td class="text-right">${frappe.format(p.total, { fieldtype: "Currency" })}</td></tr>`)
			.join("");

		return `
		<div class="row" style="font-size:0.95rem">
			<div class="col-md-4">
				<h6 style="font-weight:700;margin-bottom:8px">${__("Session Summary")}</h6>
				<table class="table table-sm table-borderless">
					<tr><td class="text-muted">${__("Cashier")}</td><td>${frappe.utils.escape_html(d.cashier)}</td></tr>
					<tr><td class="text-muted">${__("Shift Start")}</td><td>${d.shift_start}</td></tr>
					<tr><td class="text-muted">${__("Store")}</td><td>${frappe.utils.escape_html(d.store)}</td></tr>
					<tr><td class="text-muted">${__("Opening Cash")}</td><td>${frappe.format(d.opening_cash, { fieldtype: "Currency" })}</td></tr>
				</table>
			</div>
			<div class="col-md-4">
				<h6 style="font-weight:700;margin-bottom:8px">${__("Sales")}</h6>
				<table class="table table-sm table-borderless">
					<tr><td class="text-muted">${__("Invoices")}</td><td><b>${d.invoices_count}</b></td></tr>
					<tr><td class="text-muted">${__("Total Sales")}</td><td>${frappe.format(d.total_sales, { fieldtype: "Currency" })}</td></tr>
					<tr><td class="text-muted">${__("Returns")}</td><td>${d.returns_count} (${frappe.format(d.total_returns, { fieldtype: "Currency" })})</td></tr>
					<tr><td class="text-muted"><b>${__("Net Sales")}</b></td><td><b>${frappe.format(d.net_sales, { fieldtype: "Currency" })}</b></td></tr>
					<tr><td class="text-muted">${__("Tax")}</td><td>${frappe.format(d.total_tax, { fieldtype: "Currency" })}</td></tr>
				</table>
			</div>
			<div class="col-md-4">
				<h6 style="font-weight:700;margin-bottom:8px">${__("Payment Modes")}</h6>
				<table class="table table-sm table-borderless">
					${payment_rows}
				</table>
				<hr>
				<table class="table table-sm table-borderless">
					<tr><td class="text-muted">${__("Cash Drops")}</td><td>${frappe.format(d.total_cash_drops, { fieldtype: "Currency" })}</td></tr>
					<tr><td class="text-muted"><b>${__("Cash in Drawer")}</b></td><td><b>${frappe.format(d.cash_in_drawer, { fieldtype: "Currency" })}</b></td></tr>
				</table>
			</div>
		</div>`;
	}

	_build_denomination_html() {
		const rows = DENOMINATIONS.map((d) => `
			<div class="d-flex align-items-center mb-2" style="gap:12px">
				<span style="width:60px;font-weight:600;text-align:right">₹${d}</span>
				<span style="color:var(--text-muted)">×</span>
				<input type="number" class="form-control ch-denom-input"
					data-denom="${d}" min="0" value="0"
					style="width:80px;text-align:center">
				<span style="color:var(--text-muted)">=</span>
				<span class="ch-denom-total" data-denom="${d}" style="width:90px;text-align:right;font-weight:500">₹0</span>
			</div>
		`).join("");

		return `
		<div style="max-height:340px;overflow-y:auto;padding:4px 0">
			${rows}
			<hr>
			<div class="d-flex align-items-center" style="gap:12px">
				<span style="width:60px;font-weight:700;text-align:right">${__("Total")}</span>
				<span></span>
				<span></span>
				<span></span>
				<span class="ch-denom-grand-total" style="font-size:1.2rem;font-weight:700;color:var(--primary)">₹0</span>
			</div>
		</div>`;
	}

	_bind_denomination_inputs(dlg, data) {
		const wrapper = dlg.$wrapper;
		wrapper.find(".ch-denom-input").on("input", () => {
			let total = 0;
			wrapper.find(".ch-denom-input").each(function () {
				const denom = parseInt($(this).data("denom"));
				const count = Math.max(0, parseInt($(this).val()) || 0);
				const subtotal = denom * count;
				wrapper.find(`.ch-denom-total[data-denom="${denom}"]`).text(
					`₹${subtotal.toLocaleString("en-IN")}`
				);
				total += subtotal;
			});
			wrapper.find(".ch-denom-grand-total").text(`₹${total.toLocaleString("en-IN")}`);
			dlg.set_value("closing_cash", total);

			// Show variance
			const expected = data.cash_in_drawer || 0;
			const variance = total - expected;
			const abs_var = Math.abs(variance);
			const color = abs_var === 0 ? "green" : abs_var <= 100 ? "orange" : "red";
			const icon = abs_var === 0 ? "✓" : abs_var <= 100 ? "⚠" : "✗";
			wrapper.find(".ch-variance-display").html(`
				<span style="color:var(--${color === "green" ? "dark-green" : color})">
					${icon} ${__("Variance")}: ₹${variance.toLocaleString("en-IN")}
					${abs_var <= 100 && abs_var > 0 ? `<small>(${__("auto-allowed")})</small>` : ""}
					${abs_var > 100 ? `<small>(${__("manager approval needed")})</small>` : ""}
				</span>
			`);
		});
	}

	_submit_closing(dlg, values) {
		const wrapper = dlg.$wrapper;
		// Collect denominations
		const denominations = [];
		wrapper.find(".ch-denom-input").each(function () {
			const count = parseInt($(this).val()) || 0;
			if (count > 0) {
				denominations.push({
					denomination: parseInt($(this).data("denom")),
					count: count,
				});
			}
		});

		dlg.disable_primary_action();
		frappe.call({
			method: "ch_pos.api.session_api.close_session",
			args: {
				session_name: this._session_name,
				closing_cash: values.closing_cash || 0,
				denominations: JSON.stringify(denominations),
				variance_reason: values.variance_reason || "",
				manager_pin: values.manager_pin || null,
			},
			callback: (r) => {
				if (r.message) {
					dlg.hide();
					const msg = r.message;
					frappe.show_alert({
						message: __("Session closed. Net sales: {0}, Variance: ₹{1}", [
							frappe.format(msg.net_sales, { fieldtype: "Currency" }),
							msg.cash_variance,
						]),
						indicator: msg.cash_variance == 0 ? "green" : "orange",
					});

					if (msg.advance_message) {
						frappe.msgprint({
							title: __("Business Date Update"),
							indicator: msg.business_date_advanced ? "green" : "orange",
							message: msg.business_date_advanced
								? __("{0}<br><br>Next Business Date: <b>{1}</b>", [msg.advance_message, msg.next_business_date])
								: msg.advance_message,
						});
					}

					PosState.session_name = null;
					PosState.session_status = null;
					PosState.device = null;
					EventBus.emit("session:closed");
					// POS-16 fix: Force redirect and invalidate POS session to prevent
					// further transactions after session close
					setTimeout(() => {
						// Clear any cached POS data
						if (typeof localStorage !== "undefined") {
							Object.keys(localStorage).forEach((key) => {
								if (key.startsWith("pos_") || key.startsWith("POS_")) {
									localStorage.removeItem(key);
								}
							});
						}
							frappe.set_route("app", "ch-pos-app");
						// Force page refresh to fully clear POS state
						setTimeout(() => { window.location.reload(); }, 500);
					}, 1500);
				}
			},
			error: (err) => {
				dlg.enable_primary_action();
				const msg = err && err.message ? err.message : __("Failed to close session. Check console for details.");
				frappe.msgprint({ title: __("Close Session Error"), message: msg, indicator: "red" });
			},
		});
	}

	destroy() {
		if (this._dialog) {
			this._dialog.hide();
			this._dialog = null;
		}
	}
}
