/**
 * CH POS — Exception Request Workspace
 *
 * Allows store staff to:
 *   - Raise exception requests (price override, discount beyond limit, etc.)
 *   - Track pending / approved / rejected exceptions
 *   - Apply approved exceptions to the current transaction
 */
import { PosState, EventBus } from "../../state.js";

export class ExceptionWorkspace {
	constructor() {
		this._panel = null;
		this._open_context = null;
		this._locked_form_context = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "exceptions") return;
			this.render(ctx.panel);
		});

		EventBus.on("exception:open", (ctx) => {
			this._open_context = ctx || null;
			// Apply immediately only when Exceptions workspace is the active mode.
			// Otherwise keep context and apply on next render to avoid losing prefill.
			if (this._panel && PosState.active_mode === "exceptions") {
				this._apply_open_context(this._panel);
			}
		});
	}

	render(panel) {
		this._panel = panel;
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#fef3c7;color:#d97706">
							<i class="fa fa-exclamation-triangle"></i>
						</span>
						${__("Bill Exceptions")}
					</h4>
					<span class="ch-mode-hint">${__("Cashier overrides at billing time \u2014 extra discount, free accessory, below-margin sale, return beyond policy. For stock count variances see Stock Audit.")}</span>
				</div>

				<!-- Raise New Exception -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header">
						<i class="fa fa-plus-circle"></i> ${__("Raise New Exception")}
					</div>
					<div class="section-body ch-exc-form">
						${this._render_form()}
					</div>
				</div>

				<!-- My Pending Requests -->
				<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-md)">
					<div class="section-header" style="display:flex;align-items:center;justify-content:space-between">
						<span><i class="fa fa-clock-o"></i> ${__("My Requests")}</span>
						<button class="btn btn-xs btn-default ch-exc-refresh" style="border-radius:var(--pos-radius-sm)">
							<i class="fa fa-refresh"></i>
						</button>
					</div>
					<div class="section-body ch-exc-list">
						<div class="text-muted text-center" style="padding:16px">${__("Loading...")}</div>
					</div>
				</div>
			</div>
		`);

		this._bind(panel);
		this._apply_open_context(panel);
		this._load_exception_types(panel);
		this._load_my_requests(panel);
	}

	// ── Form ────────────────────────────────────────────────────

	_render_form() {
		return `
			<div class="row" style="gap:8px 0">
				<div class="col-sm-6">
					<label class="control-label">${__("Exception Type")} *</label>
					<select class="form-control ch-exc-type"></select>
				</div>
				<div class="col-sm-6">
					<label class="control-label">${__("Item")}</label>
					<div class="ch-exc-item-link"></div>
					<div class="small text-muted ch-exc-item-display" style="margin-top:4px;display:none"></div>
				</div>
				<div class="col-sm-6" style="margin-top:8px">
					<label class="control-label">${__("Original Value")}</label>
					<input type="number" class="form-control ch-exc-original" placeholder="Current price / limit" step="any" readonly>
				</div>
				<div class="col-sm-6" style="margin-top:8px">
					<label class="control-label">${__("Requested Value")}</label>
					<input type="number" class="form-control ch-exc-requested" placeholder="Requested price / amount" step="any">
				</div>
				<div class="col-sm-6" style="margin-top:8px">
					<label class="control-label">${__("Serial / IMEI")}</label>
					<input type="text" class="form-control ch-exc-serial" placeholder="Optional">
				</div>
				<div class="col-sm-6" style="margin-top:8px">
					<label class="control-label">${__("Customer")}</label>
					<div class="ch-exc-customer-link"></div>
				</div>
				<div class="col-sm-12" style="margin-top:8px">
					<label class="control-label">${__("Reason")} *</label>
					<textarea class="form-control ch-exc-reason" rows="2"
						placeholder="${__("Why is this exception needed?")}"></textarea>
				</div>
				<div class="col-sm-12" style="margin-top:12px">
					<button class="btn btn-primary btn-sm ch-exc-submit">
						<i class="fa fa-paper-plane"></i> ${__("Submit Request")}
					</button>
				</div>
			</div>
		`;
	}

	_bind(panel) {
		// Submit request
		panel.on("click", ".ch-exc-submit", () => this._submit_request(panel));
		panel.on("click", ".ch-exc-refresh", () => this._load_my_requests(panel));

		// Detail view
		panel.on("click", ".ch-exc-view", (e) => {
			const name = $(e.currentTarget).data("name");
			if (name) frappe.set_route("Form", "CH Exception Request", name);
		});

		// Apply Approved exception to current cart and switch to Sell mode
		// (Oracle Retail / SAP CAR pattern: surface the override directly in the
		// transaction context so the cashier doesn't re-navigate back to billing).
		panel.on("click", ".ch-exc-apply-bill", (e) => {
			e.stopPropagation();
			const name = $(e.currentTarget).data("name");
			if (!name) return;
			this._apply_and_bill(name);
		});

		// Item link control
		this._item_control = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Item", fieldname: "exc_item", placeholder: __("Select Item"),
			get_query: () => ({ filters: { has_variants: 0 } }) },
			parent: panel.find(".ch-exc-item-link"),
			render_input: true,
		});
		this._item_control.$input.addClass("form-control");

		// Issue #6a: Auto-fetch item selling price into Original Value when item is selected
		this._item_control.df.onchange = () => {
			const item_code = this._item_control.get_value();
			if (!item_code) {
				panel.find(".ch-exc-original").val("").prop("readonly", true);
				return;
			}
			const price_list = frappe.defaults.get_default("selling_price_list") || "Standard Selling";
			frappe.xcall("frappe.client.get_value", {
				doctype: "Item Price",
				filters: { item_code, price_list, selling: 1 },
				fieldname: "price_list_rate",
			}).then((r) => {
				if (r && r.price_list_rate) {
					panel.find(".ch-exc-original").val(flt(r.price_list_rate, 2));
				}
			}).catch(() => {});
		};

		// Customer link control
		this._customer_control = frappe.ui.form.make_control({
			df: { fieldtype: "Link", options: "Customer", fieldname: "exc_customer", placeholder: __("Select Customer") },
			parent: panel.find(".ch-exc-customer-link"),
			render_input: true,
		});
		this._customer_control.$input.addClass("form-control");

		// Default from in-progress POS transaction when not launched from a line action.
		if (!this._open_context?.customer && PosState.customer) {
			this._customer_control.set_value(PosState.customer);
		}
	}

	_apply_open_context(panel) {
		const ctx = this._open_context;
		const serial_input = panel.find(".ch-exc-serial");
		const item_display = panel.find(".ch-exc-item-display");

		if (!ctx || ctx.source !== "cart_line") {
			this._locked_form_context = null;
			serial_input.val("").prop("readonly", false);
			item_display.hide().text("");
			if (this._customer_control?.$input) {
				this._customer_control.$input.prop("disabled", false).prop("readonly", false);
			}
			return;
		}

		const initial_item_code = (ctx.item_code || "").trim();
		if (initial_item_code) this._set_item_prefill(initial_item_code);
		const customer_value = (ctx.customer || PosState.customer || PosState.default_customer || "").trim();
		if (this._customer_control) this._customer_control.set_value(customer_value);
		serial_input.val(ctx.serial_no || "");

		const item_label = [ctx.item_name || "", ctx.item_code || ""].filter(Boolean).join(" (") + (ctx.item_name && ctx.item_code ? ")" : "");
		if (item_label) item_display.text(item_label).show();

		if (ctx.lock_serial) serial_input.prop("readonly", true);
		if (ctx.lock_customer && this._customer_control?.$input) {
			this._customer_control.$input.prop("disabled", true).prop("readonly", true);
		}

		this._locked_form_context = {
			cart_idx: (ctx.cart_idx !== undefined && ctx.cart_idx !== null) ? parseInt(ctx.cart_idx, 10) : null,
			item_code: initial_item_code,
			serial_no: ctx.serial_no || "",
			customer: customer_value,
		};

		if (!initial_item_code && ctx.serial_no) {
			this._resolve_item_from_serial(ctx.serial_no).then((resolved) => {
				if (!resolved?.item_code) return;
				this._set_item_prefill(resolved.item_code);
				if (this._locked_form_context) this._locked_form_context.item_code = resolved.item_code;
				const label = [resolved.item_name || ctx.item_name || "", resolved.item_code]
					.filter(Boolean)
					.join(" (") + ((resolved.item_name || ctx.item_name) ? ")" : "");
				if (label) item_display.text(label).show();
			}).catch(() => {});
		}

		frappe.show_alert({
			message: __("Exception form opened for IMEI {0}. IMEI and customer are locked.", [ctx.serial_no || ""]),
			indicator: "blue",
		});

		// One-shot launch context.
		this._open_context = null;
	}

	_set_item_prefill(item_code) {
		if (!item_code || !this._item_control) return;
		this._item_control.set_value(item_code);
		if (!this._item_control.get_value() && this._item_control.$input) {
			this._item_control.$input.val(item_code);
		}
	}

	_resolve_item_from_serial(serial_no) {
		return frappe.xcall("frappe.client.get_value", {
			doctype: "Serial No",
			filters: { name: serial_no },
			fieldname: ["item_code", "item_name"],
		}).then((sn) => {
			if (!sn?.item_code) return null;
			if (sn.item_name) return { item_code: sn.item_code, item_name: sn.item_name };
			return frappe.xcall("frappe.client.get_value", {
				doctype: "Item",
				filters: { name: sn.item_code },
				fieldname: ["item_name"],
			}).then((it) => ({ item_code: sn.item_code, item_name: it?.item_name || "" }));
		});
	}

	_load_exception_types(panel) {
		frappe.xcall("frappe.client.get_list", {
			doctype: "CH Exception Type",
			filters: {
				enabled: 1,
				// Stock Count Variance is auto-raised by the cycle-count submit
				// flow, never picked by a cashier from this form.
				name: ["not in", ["Stock Count Variance"]],
			},
			fields: ["name", "exception_type"],
			order_by: "exception_type asc",
			limit_page_length: 0,
		}).then((types) => {
			const sel = panel.find(".ch-exc-type");
			sel.empty().append(`<option value="">${__("Select type...")}</option>`);
			(types || []).forEach((t) => {
				sel.append(`<option value="${frappe.utils.escape_html(t.name)}">${frappe.utils.escape_html(t.exception_type || t.name)}</option>`);
			});
		});
	}

	// ── Submit Request ──────────────────────────────────────────

	_submit_request(panel) {
		const exception_type = panel.find(".ch-exc-type").val();
		const reason = panel.find(".ch-exc-reason").val().trim();
		const requested_value = parseFloat(panel.find(".ch-exc-requested").val()) || 0;
		const original_value = parseFloat(panel.find(".ch-exc-original").val()) || 0;
		const locked_ctx = this._locked_form_context || null;
		const serial_no = (locked_ctx?.serial_no || panel.find(".ch-exc-serial").val() || "").trim();
		const item_code = (locked_ctx?.item_code || (this._item_control ? this._item_control.get_value() : "") || "").trim();
		const customer = (locked_ctx?.customer || (this._customer_control ? this._customer_control.get_value() : "") || "").trim();

		if (!exception_type) {
			frappe.show_alert({ message: __("Select an exception type"), indicator: "orange" });
			return;
		}
		if (!reason) {
			frappe.show_alert({ message: __("Please provide a reason"), indicator: "orange" });
			return;
		}
		if (!customer) {
			frappe.show_alert({ message: __("Select customer to raise exception"), indicator: "orange" });
			return;
		}

		// Issue #1 & #6b: company is mandatory in CH Exception Request;
		// PosState.company can be null/undefined before session is fully loaded.
		// JS JSON.stringify drops undefined values → missing required positional arg in Python.
		const company = PosState.company || "";
		const store_warehouse = PosState.warehouse || "";
		const pos_profile = PosState.pos_profile || "";
		if (!company) {
			frappe.show_alert({ message: __("POS session not active. Please open a session first."), indicator: "red" });
			return;
		}

		const btn = panel.find(".ch-exc-submit");
		btn.prop("disabled", true).html(`<i class="fa fa-spinner fa-spin"></i> ${__("Submitting...")}`);

		frappe.xcall(
			"ch_item_master.ch_item_master.exception_api.raise_exception",
			{
				exception_type,
				company,
				reason,
				requested_value,
				original_value,
				item_code: item_code || "",
				serial_no: serial_no || "",
				store_warehouse,
				pos_profile,
				customer: customer || "",
			}
		).then((res) => {
			btn.prop("disabled", false).html(`<i class="fa fa-paper-plane"></i> ${__("Submit Request")}`);

			const submitted = {
				name: res?.name || null,
				status: res?.status || "Pending",
				exception_type,
				item_code: item_code || "",
				serial_no: serial_no || "",
				customer: customer || "",
				requested_value,
				original_value,
			};
			this._bind_request_to_cart_line(submitted, locked_ctx);

			if (res.status === "Auto-Approved") {
				frappe.show_alert({ message: __("Exception auto-approved! Ref: {0}", [res.name]), indicator: "green" });
				frappe.xcall(
					"ch_item_master.ch_item_master.exception_api.check_exception_valid",
					{ exception_name: res.name }
				).then((full) => {
					if (full && full.valid) {
						// Bill-level mirror (legacy listeners + persistence).
						PosState.exception_request = res.name;
						PosState.exception_request_data = full;
						// Replace the partial snapshot we bound earlier with the
						// FULL exception data (carries resolution_value /
						// requested_value needed for per-line price override).
						// This is what unlocks multi-exception per bill: every
						// cart line owns its own exception_request_data so
						// cart_service can apply pricing per line.
						this._bind_request_to_cart_line(
							{ ...full, name: res.name },
							locked_ctx,
						);
						EventBus.emit("exception:applied", { name: res.name, data: full });
					}
				}).catch(() => {});
			} else {
				frappe.show_alert({ message: __("Request submitted: {0}. Status: {1}", [res.name, res.status]), indicator: "blue" });
			}

			// Clear form
			panel.find(".ch-exc-type").val("");
			panel.find(".ch-exc-reason").val("");
			panel.find(".ch-exc-requested, .ch-exc-original, .ch-exc-serial").val("");
			panel.find(".ch-exc-item-display").hide().text("");
			if (this._item_control) this._item_control.set_value("");
			if (this._customer_control) this._customer_control.set_value("");
			this._locked_form_context = null;
			panel.find(".ch-exc-serial").prop("readonly", false);
			if (this._customer_control?.$input) {
				this._customer_control.$input.prop("disabled", false).prop("readonly", false);
			}

			this._load_my_requests(panel);
		}).catch(() => {
			btn.prop("disabled", false).html(`<i class="fa fa-paper-plane"></i> ${__("Submit Request")}`);
		});
	}

	_bind_request_to_cart_line(submitted, locked_ctx) {
		if (!submitted || !submitted.name) return;
		const idx = locked_ctx && locked_ctx.cart_idx !== undefined && locked_ctx.cart_idx !== null
			? parseInt(locked_ctx.cart_idx, 10)
			: -1;

		let target = null;
		if (idx >= 0 && PosState.cart[idx]) {
			target = PosState.cart[idx];
		}

		if (!target) {
			target = PosState.cart.find((it) => {
				if (submitted.serial_no && (it.serial_no || "").trim() === submitted.serial_no) return true;
				return submitted.item_code && it.item_code === submitted.item_code;
			}) || null;
		}

		if (!target) return;
		target.exception_request = submitted.name;
		target.exception_request_status = submitted.status || "Pending";
		target.exception_request_data = submitted;
		EventBus.emit("cart:updated");
	}

	// ── My Requests List ────────────────────────────────────────

	_load_my_requests(panel) {
		const container = panel.find(".ch-exc-list");
		frappe.xcall(
			"ch_item_master.ch_item_master.exception_api.get_pending_exceptions",
			{
				company: PosState.company,
				store_warehouse: PosState.warehouse,
				scope: "bill",
			}
		).then((rows) => {
			if (!rows || !rows.length) {
				container.html(`<div class="text-muted text-center" style="padding:16px">${__("No requests found")}</div>`);
				return;
			}
			let html = `<table class="table table-condensed table-hover" style="margin:0;font-size:13px">
				<thead><tr>
					<th>${__("ID")}</th>
					<th>${__("Type")}</th>
					<th>${__("Item")}</th>
					<th style="text-align:right">${__("Requested")}</th>
					<th>${__("Status")}</th>
					<th>${__("Raised")}</th>
					<th></th>
				</tr></thead><tbody>`;

			rows.forEach((r) => {
				const status_cls = {
					"Pending": "warning", "Approved": "success",
					"Rejected": "danger", "Expired": "secondary",
					"Auto-Approved": "success",
				}[r.status] || "default";

				html += `<tr>
					<td><a class="ch-exc-view" data-name="${frappe.utils.escape_html(r.name)}" style="cursor:pointer;color:var(--primary)">${frappe.utils.escape_html(r.name)}</a></td>
					<td>${frappe.utils.escape_html(r.exception_type || "")}</td>
					<td>${frappe.utils.escape_html(r.item_code || "—")}</td>
					<td style="text-align:right">${r.requested_value ? format_currency(r.requested_value) : "—"}</td>
					<td><span class="badge badge-${status_cls}">${frappe.utils.escape_html(r.status || "")}</span></td>
					<td>${r.raised_at ? frappe.datetime.prettyDate(r.raised_at) : "—"}</td>
					<td>
						${this._is_appliable(r) ? `<button class="btn btn-xs btn-success ch-exc-apply-bill" data-name="${frappe.utils.escape_html(r.name)}" title="${__("Apply this exception to the current cart and switch to Billing")}"><i class="fa fa-shopping-cart"></i> ${__("Apply & Bill")}</button>` : ""}
					</td>
				</tr>`;
			});

			html += `</tbody></table>`;
			container.html(html);
		}).catch(() => {
			container.html(`<div class="text-muted text-center" style="padding:16px">${__("Error loading requests")}</div>`);
		});
	}
}

function format_currency(val) {
	return frappe.format(val, { fieldtype: "Currency" });
}

// Exposed as instance method via prototype assignment so it can use PosState/EventBus.
ExceptionWorkspace.prototype._is_appliable = function (r) {
	// Only fully-approved, not-yet-consumed exceptions can be applied to a cart.
	const status = (r && r.status) || "";
	if (status !== "Approved" && status !== "Auto-Approved") return false;
	if (r.pos_invoice) return false; // already consumed
	// Cycle-count variance approvals belong to Stock Audit, not the cart.
	if (r.exception_type === "Stock Count Variance") return false;
	if (r.reference_doctype === "CH Cycle Count") return false;
	return true;
};

ExceptionWorkspace.prototype._apply_and_bill = function (exception_name) {
	// Server-side validity check first (matches cart_panel.js behaviour).
	frappe.xcall(
		"ch_item_master.ch_item_master.exception_api.check_exception_valid",
		{ exception_name },
	).then((r) => {
		if (!r || !r.valid) {
			frappe.msgprint(__("Exception {0} is no longer valid (status: {1}). It may have expired or already been used.",
				[exception_name, r?.status || "Unknown"]));
			return;
		}
		// 1. Stash exception on POS state — payment_dialog.js forwards it to backend.
		PosState.exception_request = exception_name;
		PosState.exception_request_data = r;
		// 2. Switch to sell mode so the cashier lands directly on the cart with the
		//    exception banner already visible (cart_panel.js handles the banner render).
		PosState.active_mode = "sell";
		EventBus.emit("mode:set", "sell");
		EventBus.emit("mode:switch", "sell");
		// 3. Tell cart_panel/cart_service to apply pricing + refresh banner.
		//    cart_service.exception:applied is synchronous, so cart state reflects
		//    the outcome immediately after emit returns.
		EventBus.emit("exception:applied", { name: exception_name, data: r });

		// 4. Confirm whether ANY cart line actually accepted the exception —
		//    previously we blindly showed "billing mode active", which caused the
		//    cashier to think the discount was live when the per-item guards in
		//    cart_service._apply_exception_pricing_to_item had silently dropped it
		//    (mismatched item_code / serial / customer, or bill-level exception
		//    with no item_code).
		const applied_line = (PosState.cart || []).find(
			(it) => (it && it.exception_request) === exception_name
		);
		if (applied_line) {
			frappe.show_alert({
				message: __("Exception {0} applied to {1} — billing mode active",
					[exception_name, applied_line.item_name || applied_line.item_code || ""]),
				indicator: "green",
			});
			return;
		}
		const reason = ExceptionWorkspace.prototype._diagnose_apply_skip(r, PosState.cart || []);
		frappe.show_alert({
			message: __("Exception {0} could not be applied: {1}", [exception_name, reason]),
			indicator: "orange",
		}, 10);
	});
};

// Explain WHY an approved exception failed to attach to any cart line so the
// cashier can act (add the item, scan the right IMEI, switch customer, or ask
// the approver to re-raise the exception with the correct scope).
ExceptionWorkspace.prototype._diagnose_apply_skip = function (data, cart) {
	if (!data) return __("no exception data");
	if (!data.item_code) {
		return __("this exception has no item linked. Raise it against a specific item, or apply it as a line-level exception from the cart.");
	}
	const match = (cart || []).find((c) => c && c.item_code === data.item_code);
	if (!match) {
		return __("item {0} is not in the cart. Add it, then apply the exception.", [data.item_code]);
	}
	if (!data.customer) {
		return __("exception has no customer on record and cannot be applied. Re-raise it after selecting the customer on the cart.");
	}
	if (!PosState.customer) {
		return __("select a customer on the bill before applying the exception (the exception was approved for {0}).", [data.customer]);
	}
	if (data.customer !== PosState.customer) {
		return __("exception is tied to customer {0}; current cart customer is {1}.",
			[data.customer, PosState.customer]);
	}
	if (data.serial_no) {
		const cart_serial = (match.serial_no || "").trim();
		if (!cart_serial) {
			return __("exception requires IMEI {0}; the cart line has no serial scanned yet.", [data.serial_no]);
		}
		if (cart_serial !== (data.serial_no || "").trim()) {
			return __("exception is tied to IMEI {0}; cart line has {1}.", [data.serial_no, cart_serial]);
		}
	}
	if (flt(data.original_value) <= 0) {
		return __("exception has no original value on record — cannot compute discount.");
	}
	if (flt(data.resolution_value || data.requested_value) < 0) {
		return __("exception has an invalid resolution value.");
	}
	return __("cart line rejected the exception (check customer/phone/IMEI match).");
};