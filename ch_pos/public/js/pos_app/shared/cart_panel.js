/**
 * CH POS — Checkout Panel (Transaction Engine)
 *
 * Premium right-panel checkout experience:
 * A0. Executive / Company bar (billed-by selector)
 * A. Customer header with tag
 * B. Quick retail actions (exchange, VAS, swap, warranty)
 * C. Cart line items with qty controls, serial tags, discount badges
 * D. Coupon / discount block
 * E. Totals with grand total prominence
 * F. Footer CTA: Cancel · Hold · PAY
 */
import { PosState, EventBus } from "../state.js";
import { format_number } from "./helpers.js";

export class CartPanel {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this.customer_field = null;
		this.render();
		this.bind();
	}

	render() {
		this.wrapper.html(`
			<!-- A0. Executive / Company Bar -->
			<div class="ch-pos-executive-bar" style="display:none">
				<div class="ch-pos-company-switcher"></div>
				<div class="ch-pos-executive-select-wrapper">
					<label class="ch-pos-exec-label"><i class="fa fa-id-badge"></i> ${__("Billed By")}</label>
					<select class="form-control ch-pos-executive-select"></select>
				</div>
			</div>

			<!-- A. Customer Header -->
			<div class="ch-pos-customer-bar">
				<div class="ch-pos-customer-select"></div>
				<div class="ch-pos-customer-tag walk-in">
					<i class="fa fa-user-o" style="font-size:10px"></i> ${__("Walk-in")}
				</div>
			</div>
			<!-- A2. Customer Info Badges -->
			<div class="ch-pos-customer-info" style="display:none">
				<div class="ch-pos-cust-badges"></div>
			</div>

			<!-- A3. Sale Type Selector -->
			<div class="ch-pos-sale-type-bar" style="display:none">
				<div class="ch-pos-sale-type-pills"></div>
				<div class="ch-pos-sale-sub-type-row" style="display:none">
					<select class="form-control form-control-sm ch-pos-sale-sub-type-select"></select>
					<input type="text" class="form-control form-control-sm ch-pos-sale-reference-input"
						placeholder="${__("Reference No...")}" style="display:none;max-width:180px">
				</div>
			</div>

			<!-- B. Quick Retail Actions -->
			<div class="ch-pos-quick-actions">
				<button class="btn btn-outline-primary ch-pos-btn-exchange">
					<i class="fa fa-exchange"></i> ${__("Exchange")}
				</button>
				<button class="btn btn-outline-info ch-pos-btn-vas">
					<i class="fa fa-shield"></i> ${__("VAS")}
				</button>
				<button class="btn btn-outline-secondary ch-pos-btn-product-exchange">
					<i class="fa fa-retweet"></i> ${__("Swap")}
				</button>
				<div class="ch-pos-exchange-banner" style="display:none"></div>
				<div class="ch-pos-product-exchange-banner" style="display:none"></div>
				<div class="ch-pos-credit-warning" style="display:none"></div>
			</div>

			<!-- C. Cart Header -->
			<div class="ch-pos-cart-header">
				<span>${__("Item")}</span>
				<span style="text-align:center">${__("Qty")}</span>
				<span style="text-align:right">${__("Rate")}</span>
				<span style="text-align:right">${__("Amt")}</span>
			</div>

			<!-- C. Cart Items -->
			<div class="ch-pos-cart-items">
				<div class="ch-pos-cart-empty">
					<div class="empty-cart-icon"><i class="fa fa-shopping-bag"></i></div>
					<span>${__("Cart is empty")}</span>
					<span class="text-muted">${__("Scan or tap products to add")}</span>
				</div>
			</div>

			<!-- D. Discount / Coupon -->
			<div class="ch-pos-discount-bar">
				<div class="ch-pos-discount-row">
					<input type="text" class="form-control ch-pos-coupon-input"
						placeholder="${__("Coupon / Voucher code...")}">
					<button class="btn btn-sm btn-outline-primary ch-pos-coupon-apply">${__("Apply")}</button>
				</div>
				<div class="ch-pos-coupon-msg"></div>
				<div class="ch-pos-discount-row" style="margin-top:4px">
					<span class="ch-pos-discount-label">${__("Reason")}</span>
					<select class="form-control ch-pos-disc-reason" style="flex:1;font-size:12px">
						<option value="">${__("Select reason...")}</option>
					</select>
				</div>
				<div class="ch-pos-discount-row ch-pos-manual-disc" style="margin-top:4px;display:none">
					<span class="ch-pos-discount-label">${__("Discount")}</span>
					<div class="ch-pos-discount-inputs d-flex align-items-center">
						<input type="number" class="form-control ch-pos-disc-pct"
							placeholder="%" min="0" max="100" step="0.5">
						<span class="ch-pos-disc-or">${__("or")}</span>
						<input type="number" class="form-control ch-pos-disc-amt"
							placeholder="₹" min="0" step="1">
					</div>
				</div>
				<div class="ch-pos-disc-info text-muted" style="font-size:11px;padding:2px 4px;display:none"></div>
			</div>

			<!-- E. Totals -->
			<div class="ch-pos-cart-summary">
				<div class="summary-row total-qty">
					<span>${__("Items")}</span>
					<span class="value">0</span>
				</div>
				<div class="summary-row discount-total">
					<span>${__("Item Discounts")}</span>
					<span class="value">₹0</span>
				</div>
				<div class="summary-row additional-disc" style="display:none">
					<span>${__("Additional Discount")}</span>
					<span class="value">₹0</span>
				</div>
				<div class="summary-row coupon-disc" style="display:none">
					<span>🏷️ ${__("Coupon")}</span>
					<span class="value">₹0</span>
				</div>
				<div class="summary-row voucher-disc" style="display:none">
					<span>🎟️ ${__("Voucher")}</span>
					<span class="value">-₹0</span>
				</div>
				<div class="summary-row exchange-credit" style="display:none">
					<span><i class="fa fa-exchange"></i> ${__("Exchange Credit")}</span>
					<span class="value">-₹0</span>
				</div>
				<div class="summary-row product-exchange-credit" style="display:none">
					<span><i class="fa fa-retweet"></i> ${__("Swap Credit")}</span>
					<span class="value">-₹0</span>
				</div>
				<div class="summary-row grand-total">
					<span>${__("Total")}</span>
					<span class="value">₹0</span>
				</div>
			</div>

			<!-- F. Action Buttons -->
			<div class="ch-pos-cart-actions">
				<button class="btn btn-outline-danger ch-pos-btn-cancel">${__("Cancel")}</button>
				<button class="btn btn-outline-secondary ch-pos-btn-hold">
					<i class="fa fa-pause-circle-o"></i> ${__("Hold")}
				</button>
				<button class="btn btn-primary btn-lg ch-pos-btn-pay">
					<i class="fa fa-credit-card" style="margin-right:6px"></i>${__("PAY")}
				</button>
			</div>
		`);

		this._render_customer_selector();
		this._render_executive_bar();
		this._load_sale_types();
	}

	/** Populate the executive / company bar from PosState.executive_access */
	_render_executive_bar() {
		const bar = this.wrapper.find(".ch-pos-executive-bar");
		const access = PosState.executive_access;
		if (!access || !access.companies || !access.companies.length) {
			bar.hide();
			return;
		}

		// Company switcher (only if user has access to multiple companies)
		const switcher = bar.find(".ch-pos-company-switcher");
		if (access.companies.length > 1) {
			let btns = "";
			for (const cr of access.companies) {
				const short = cr.company.replace(/ Pvt Ltd| Private Limited| Ltd/gi, "").trim();
				const active = cr.company === PosState.active_company ? " active" : "";
				btns += `<button class="ch-pos-company-btn${active}" data-company="${frappe.utils.escape_html(cr.company)}">${frappe.utils.escape_html(short)}</button>`;
			}
			switcher.html(`<div class="ch-pos-company-pills">${btns}</div>`).show();
		} else {
			// Single company — show as badge, no switcher
			const comp = access.companies[0].company;
			const short = comp.replace(/ Pvt Ltd| Private Limited| Ltd/gi, "").trim();
			switcher.html(`<span class="ch-pos-company-single">${frappe.utils.escape_html(short)}</span>`).show();
		}

		// Executive selector dropdown
		this._populate_executive_dropdown();

		bar.show();
	}

	/** Populate the executive dropdown based on active_company */
	_populate_executive_dropdown() {
		const select = this.wrapper.find(".ch-pos-executive-select");
		const access = PosState.executive_access;
		if (!access) return;

		const company = PosState.active_company;
		const execs = (access.store_executives || {})[company] || [];

		let options = "";
		for (const ex of execs) {
			const sel = ex.name === PosState.sales_executive ? " selected" : "";
			const role_tag = ex.role !== "Executive" ? ` (${ex.role})` : "";
			options += `<option value="${frappe.utils.escape_html(ex.name)}"${sel}>${frappe.utils.escape_html(ex.executive_name)}${role_tag}</option>`;
		}

		if (!options) {
			options = `<option value="">${__("No executives for this company")}</option>`;
		}

		select.html(options);

		// Auto-select own executive if current selection is invalid
		if (!PosState.sales_executive || !execs.find((e) => e.name === PosState.sales_executive)) {
			const own = access.own_executive;
			if (own && execs.find((e) => e.name === own.name)) {
				select.val(own.name);
				PosState.sales_executive = own.name;
				PosState.sales_executive_name = own.executive_name;
			} else if (execs.length) {
				select.val(execs[0].name);
				PosState.sales_executive = execs[0].name;
				PosState.sales_executive_name = execs[0].executive_name;
			}
		}
	}

	/** Load sale types from server and render pills */
	_load_sale_types() {
		this._sale_types = [];
		frappe.xcall("ch_pos.api.pos_api.get_sale_types", {
			company: PosState.company,
		}).then((types) => {
			this._sale_types = types || [];
			if (this._sale_types.length) {
				this._render_sale_type_bar();
			}
		});
	}

	_render_sale_type_bar() {
		const bar = this.wrapper.find(".ch-pos-sale-type-bar");
		const pills = bar.find(".ch-pos-sale-type-pills");
		if (!this._sale_types.length) {
			bar.hide();
			return;
		}

		let btns = "";
		for (const st of this._sale_types) {
			const active = (st.is_default || st.sale_type_name === PosState.sale_type) ? " active" : "";
			btns += `<button class="ch-pos-saletype-btn${active}" data-type="${frappe.utils.escape_html(st.sale_type_name)}">${frappe.utils.escape_html(st.code || st.sale_type_name)}</button>`;
			if (st.is_default && !PosState.sale_type) {
				PosState.sale_type = st.sale_type_name;
			}
		}
		pills.html(btns);
		bar.show();

		// If the default sale type has sub-types, show them
		if (PosState.sale_type) {
			this._update_sub_type_selector(PosState.sale_type);
		}
	}

	_update_sub_type_selector(type_name) {
		const st = this._sale_types.find((t) => t.sale_type_name === type_name);
		const row = this.wrapper.find(".ch-pos-sale-sub-type-row");
		const sel = row.find(".ch-pos-sale-sub-type-select");
		const ref = row.find(".ch-pos-sale-reference-input");

		if (!st || !st.sub_types || !st.sub_types.length) {
			row.hide();
			PosState.sale_sub_type = null;
			PosState.sale_reference = null;
			return;
		}

		let options = `<option value="">${__("Select sub-type...")}</option>`;
		for (const sub of st.sub_types) {
			const selected = sub.sale_sub_type === PosState.sale_sub_type ? " selected" : "";
			options += `<option value="${frappe.utils.escape_html(sub.sale_sub_type)}" data-ref="${sub.requires_reference ? 1 : 0}">${frappe.utils.escape_html(sub.sale_sub_type)}</option>`;
		}
		sel.html(options);
		ref.hide().val("");
		row.show();
	}

	_render_customer_selector() {
		const el = this.wrapper.find(".ch-pos-customer-select");
		this.customer_field = frappe.ui.form.make_control({
			df: {
				fieldname: "customer",
				fieldtype: "Link",
				options: "Customer",
				placeholder: __("Search or add customer..."),
				change: () => this._on_customer_change(),
			},
			parent: el,
			render_input: true,
		});

		// Belt-and-suspenders: awesomplete fires reliably on dropdown pick
		if (this.customer_field.$input) {
			this.customer_field.$input.on("awesomplete-selectcomplete", (e) => {
				const val = e.originalEvent?.text?.value || this.customer_field.get_value();
				if (val) this._commit_customer(val);
			});
		}

		// Quick-create customer button
		const btn_wrap = $(`<div class="ch-pos-quick-customer-wrap" style="margin-top:4px">
			<button class="btn btn-xs btn-outline-secondary ch-pos-btn-new-customer">
				<i class="fa fa-user-plus"></i> ${__("New Customer")}
			</button>
		</div>`);
		el.append(btn_wrap);
	}

	_on_customer_change() {
		const val = this.customer_field.get_value();
		// Ignore empty — blur / link-revalidation can fire change with "".
		// Customer is only cleared via reset_transaction or explicit action.
		if (!val) return;
		this._commit_customer(val);
	}

	/** Actually persist a customer selection to state & UI */
	_commit_customer(val) {
		if (val === PosState.customer) return;
		PosState.customer = val;
		PosState.customer_info = null;
		EventBus.emit("customer:changed", val);
		// Update tag
		const tag = this.wrapper.find(".ch-pos-customer-tag");
		tag.removeClass("walk-in").addClass("existing")
			.html(`<i class="fa fa-user" style="font-size:10px"></i> ${frappe.utils.escape_html(val)}`);
		// Sync the Link control's display (in case called from outside change)
		if (this.customer_field && this.customer_field.get_value() !== val) {
			this.customer_field.set_value(val);
		}
		// Fetch customer POS info — price list, credit, loyalty, type
		frappe.xcall("ch_pos.api.pos_api.get_customer_pos_info", {
			customer: val,
			company: PosState.company,
		}).then((info) => {
			PosState.customer_info = info;
			if (info.price_list) {
				PosState.price_list = info.price_list;
			}
			if (info.loyalty) {
				PosState.loyalty_program = info.loyalty.program;
				PosState.loyalty_points = info.loyalty.points;
				PosState.conversion_factor = info.loyalty.conversion_factor;
			}
			EventBus.emit("customer:info_loaded", info);
			this._render_customer_info(info);
		});
	}

	_show_new_customer_dialog() {
		const d = new frappe.ui.Dialog({
			title: __("New Customer"),
			fields: [
				{ fieldname: "customer_name", fieldtype: "Data", label: __("Customer Name"), reqd: 1 },
				{ fieldname: "mobile_no", fieldtype: "Data", label: __("Mobile Number"), options: "Phone" },
				{ fieldtype: "Column Break" },
				{ fieldname: "email_id", fieldtype: "Data", label: __("Email"), options: "Email" },
				{ fieldname: "customer_group", fieldtype: "Link", label: __("Customer Group"),
				  options: "Customer Group", default: "Individual" },
			],
			size: "small",
			primary_action_label: __("Create"),
			primary_action: (values) => {
				frappe.xcall("ch_pos.api.pos_api.quick_create_customer", {
					customer_name: values.customer_name,
					mobile_no: values.mobile_no || "",
					email_id: values.email_id || "",
					customer_group: values.customer_group || "Individual",
					company: PosState.company,
				}).then((name) => {
					d.hide();
					frappe.show_alert({ message: __("Customer {0} created", [name]), indicator: "green" });
					this.customer_field.set_value(name);
					this._commit_customer(name);   // explicit — don't rely on change callback
				});
			},
		});
		d.show();
	}

	bind() {
		const w = this.wrapper;

		// New customer quick-create
		w.on("click", ".ch-pos-btn-new-customer", () => this._show_new_customer_dialog());
		EventBus.on("customer:new", () => this._show_new_customer_dialog());

		// Company switcher
		w.on("click", ".ch-pos-company-btn", (e) => {
			const btn = $(e.currentTarget);
			const company = btn.data("company");
			if (company === PosState.active_company) return;
			w.find(".ch-pos-company-btn").removeClass("active");
			btn.addClass("active");
			PosState.active_company = company;
			this._populate_executive_dropdown();
			EventBus.emit("company:switched", company);
		});

		// Executive selector change
		w.on("change", ".ch-pos-executive-select", (e) => {
			const val = $(e.currentTarget).val();
			const access = PosState.executive_access;
			const company = PosState.active_company;
			const execs = (access?.store_executives || {})[company] || [];
			const exec = execs.find((ex) => ex.name === val);
			PosState.sales_executive = val;
			PosState.sales_executive_name = exec ? exec.executive_name : "";
			EventBus.emit("executive:changed", val);
		});

		// Pre-pay sync: ensure customer value from Link field is committed
		EventBus.on("cart:pre_pay_sync", () => {
			const val = this.customer_field?.get_value();
			if (val && !PosState.customer) {
				PosState.customer = val;
			}
		});

		// Sale type pills
		w.on("click", ".ch-pos-saletype-btn", (e) => {
			const btn = $(e.currentTarget);
			const type = btn.data("type");
			w.find(".ch-pos-saletype-btn").removeClass("active");
			btn.addClass("active");
			PosState.sale_type = type;
			PosState.sale_sub_type = null;
			PosState.sale_reference = null;
			this._update_sub_type_selector(type);
			EventBus.emit("sale_type:changed", type);
		});

		// Sale sub-type select
		w.on("change", ".ch-pos-sale-sub-type-select", (e) => {
			const val = $(e.currentTarget).val();
			PosState.sale_sub_type = val || null;
			PosState.sale_reference = null;
			// Show reference input if the sub-type requires it
			const opt = $(e.currentTarget).find(":selected");
			const ref_input = w.find(".ch-pos-sale-reference-input");
			if (opt.data("ref")) {
				ref_input.show().val("");
			} else {
				ref_input.hide().val("");
			}
		});

		// Sale reference input
		w.on("change", ".ch-pos-sale-reference-input", (e) => {
			PosState.sale_reference = $(e.currentTarget).val().trim() || null;
		});

		// Cart actions
		w.on("click", ".ch-pos-btn-cancel", () => EventBus.emit("cart:cancel"));
		w.on("click", ".ch-pos-btn-hold", () => EventBus.emit("cart:hold"));
		w.on("click", ".ch-pos-btn-pay", () => EventBus.emit("cart:pay"));

		// Quick actions
		w.on("click", ".ch-pos-btn-exchange", () => EventBus.emit("exchange:open"));
		w.on("click", ".ch-pos-btn-vas", () => EventBus.emit("vas:open"));
		w.on("click", ".ch-pos-btn-product-exchange", () => EventBus.emit("product_exchange:open"));

		// Cart line qty / remove
		w.on("click", ".ch-pos-qty-plus", function () {
			EventBus.emit("cart:qty_plus", $(this).data("idx"));
		});
		w.on("click", ".ch-pos-qty-minus", function () {
			EventBus.emit("cart:qty_minus", $(this).data("idx"));
		});
		w.on("click", ".ch-pos-cart-remove", function () {
			EventBus.emit("cart:remove", $(this).data("idx"));
		});

		// Coupon / Voucher
		w.on("click", ".ch-pos-coupon-apply", () => {
			const code = w.find(".ch-pos-coupon-input").val().trim();
			if (code) EventBus.emit("coupon:apply", code);
		});

		EventBus.on("coupon:applied", (data) => {
			const msg_el = w.find(".ch-pos-coupon-msg");
			if (data.is_voucher) {
				msg_el.html(`<span style="color:var(--pos-success,green)">🎟️ ${__("Voucher applied")} — ₹${format_number(data.amount)} ${__("off")} (${__("Bal")}: ₹${format_number(data.balance)})</span>`).show();
			} else {
				msg_el.html(`<span style="color:var(--pos-success,green)">🏷️ ${__("Coupon applied")} — ₹${format_number(data.amount)} ${__("off")}</span>`).show();
			}
		});
		EventBus.on("coupon:invalid", (reason) => {
			w.find(".ch-pos-coupon-msg")
				.html(`<span style="color:var(--pos-danger,red)">${frappe.utils.escape_html(reason)}</span>`).show();
		});

		// Manual discount — enforced by executive permissions (only for manual-entry reasons)
		w.on("change", ".ch-pos-disc-pct", function () {
			const pct = parseFloat($(this).val()) || 0;
			if (!_check_discount_permission(pct)) {
				$(this).val("");
				return;
			}
			PosState.additional_discount_pct = pct;
			PosState.additional_discount_amt = 0;
			w.find(".ch-pos-disc-amt").val("");
			EventBus.emit("discount:changed");
		});
		w.on("change", ".ch-pos-disc-amt", function () {
			const amt = parseFloat($(this).val()) || 0;
			if (!_check_discount_permission(null, amt)) {
				$(this).val("");
				return;
			}
			PosState.additional_discount_amt = amt;
			PosState.additional_discount_pct = 0;
			w.find(".ch-pos-disc-pct").val("");
			EventBus.emit("discount:changed");
		});

		// Discount reason selection — drives the whole discount flow
		w.on("change", ".ch-pos-disc-reason", function () {
			const reason_name = $(this).val() || "";
			PosState.discount_reason = reason_name;

			if (!reason_name) {
				// Cleared — reset discount
				PosState.additional_discount_pct = 0;
				PosState.additional_discount_amt = 0;
				w.find(".ch-pos-manual-disc").hide();
				w.find(".ch-pos-disc-info").hide();
				w.find(".ch-pos-disc-pct, .ch-pos-disc-amt").val("");
				EventBus.emit("discount:changed");
				return;
			}

			const reason = (PosState._discount_reasons || []).find((r) => r.name === reason_name);
			if (!reason) return;

			if (reason.allow_manual_entry) {
				// Manual entry mode — show inputs, clear preset
				w.find(".ch-pos-manual-disc").show();
				w.find(".ch-pos-disc-info").text(
					reason.max_manual_percent
						? __("Enter discount (max {0}%)", [reason.max_manual_percent])
						: __("Enter discount within your role limits")
				).show();
				w.find(".ch-pos-disc-pct, .ch-pos-disc-amt").val("");
				PosState.additional_discount_pct = 0;
				PosState.additional_discount_amt = 0;
				EventBus.emit("discount:changed");
			} else {
				// Preset — auto-apply the fixed value
				w.find(".ch-pos-manual-disc").hide();
				if (reason.discount_type === "Percentage") {
					PosState.additional_discount_pct = flt(reason.discount_value);
					PosState.additional_discount_amt = 0;
					w.find(".ch-pos-disc-info").text(
						__("{0}: {1}% discount applied", [reason.reason_name, reason.discount_value])
					).show();
				} else {
					PosState.additional_discount_amt = flt(reason.discount_value);
					PosState.additional_discount_pct = 0;
					w.find(".ch-pos-disc-info").text(
						__("{0}: ₹{1} discount applied", [reason.reason_name, reason.discount_value])
					).show();
				}
				w.find(".ch-pos-disc-pct, .ch-pos-disc-amt").val("");
				EventBus.emit("discount:changed");
			}
		});

		// Load discount reasons from server
		this._load_discount_reasons(w);

		// Refresh on cart update
		EventBus.on("cart:updated", () => this.refresh());
		EventBus.on("exchange:applied", () => this.refresh());
		EventBus.on("product_exchange:applied", () => this.refresh());
		EventBus.on("customer:set", (cust) => {
			if (cust) {
				if (this.customer_field) this.customer_field.set_value(cust);
				this._commit_customer(cust);   // explicit — don't rely on change callback
			}
		});
		EventBus.on("state:transaction_reset", () => {
			this.refresh();
			// Reset customer field
			if (this.customer_field) this.customer_field.set_value("");
			const tag = this.wrapper.find(".ch-pos-customer-tag");
			tag.removeClass("existing loyalty").addClass("walk-in")
				.html(`<i class="fa fa-user-o" style="font-size:10px"></i> ${__("Walk-in")}`);
			this.wrapper.find(".ch-pos-customer-info").hide().find(".ch-pos-cust-badges").empty();
			this.wrapper.find(".ch-pos-credit-warning").hide();
			// Reset sale type to default
			this._render_sale_type_bar();
			// Reset discount controls
			this.wrapper.find(".ch-pos-disc-reason").val("");
			this.wrapper.find(".ch-pos-disc-pct, .ch-pos-disc-amt").val("");
			this.wrapper.find(".ch-pos-manual-disc").hide();
			this.wrapper.find(".ch-pos-disc-info").hide();
		});

		// Re-render executive bar when access data arrives
		EventBus.on("executive_access:loaded", () => this._render_executive_bar());
	}

	_load_discount_reasons(w) {
		PosState._discount_reasons = [];
		frappe.xcall("ch_pos.api.pos_api.get_discount_reasons", {
			company: PosState.company,
		}).then((reasons) => {
			PosState._discount_reasons = reasons || [];
			const sel = w.find(".ch-pos-disc-reason");
			sel.find("option:not(:first)").remove();
			(reasons || []).forEach((r) => {
				const label = r.allow_manual_entry
					? r.reason_name
					: `${r.reason_name} (${r.discount_type === "Percentage" ? r.discount_value + "%" : "₹" + r.discount_value})`;
				sel.append(`<option value="${frappe.utils.escape_html(r.name)}">${frappe.utils.escape_html(label)}</option>`);
			});
		});
	}

	refresh() {
		const items_el = this.wrapper.find(".ch-pos-cart-items");
		const cart = PosState.cart;

		if (!cart.length) {
			items_el.html(`
				<div class="ch-pos-cart-empty">
					<div class="empty-cart-icon"><i class="fa fa-shopping-bag"></i></div>
					<span>${__("Cart is empty")}</span>
					<span class="text-muted">${__("Scan or tap products to add")}</span>
				</div>
			`);
		} else {
			let lines = cart.map((item, idx) => this._cart_line_html(item, idx)).join("");
			// Display exchange credit as visual line in cart
			if (PosState.exchange_amount > 0) {
				lines += `<div class="ch-pos-cart-line is-exchange-line">
					<div class="cart-line-top">
						<span class="cart-item-name">
							<i class="fa fa-exchange" style="color:var(--pos-warning);margin-right:4px"></i>
							${__("Exchange Credit")}
							<span class="cart-offer-tag">${PosState.exchange_assessment || ""}</span>
						</span>
						<span class="cart-item-amount" style="color:var(--pos-success)">-₹${format_number(PosState.exchange_amount)}</span>
					</div>
				</div>`;
			}
			// Display product exchange (swap) credit
			if (PosState.product_exchange_credit > 0) {
				lines += `<div class="ch-pos-cart-line is-exchange-line">
					<div class="cart-line-top">
						<span class="cart-item-name">
							<i class="fa fa-retweet" style="color:var(--pos-info);margin-right:4px"></i>
							${__("Swap Credit")}
							<span class="cart-offer-tag">${PosState.product_exchange_invoice || ""}</span>
						</span>
						<span class="cart-item-amount" style="color:var(--pos-success)">-₹${format_number(PosState.product_exchange_credit)}</span>
					</div>
				</div>`;
			}
			items_el.html(lines);
		}

		this._update_summary();
	}

	_cart_line_html(item, idx) {
		const amount = flt(item.qty) * flt(item.rate);
		const offer_tag = item.offer_applied
			? `<span class="cart-offer-tag">${frappe.utils.escape_html(item.offer_applied)}</span>`
			: "";
		const serial_tag = item.serial_no
			? `<span class="cart-serial-tag">${frappe.utils.escape_html(item.serial_no)}</span>`
			: "";
		const margin_tag = (item.ch_item_type === "Refurbished" || item.ch_item_type === "Pre-Owned")
			? `<span class="cart-margin-tag">${frappe.utils.escape_html(item.ch_item_type)}</span>`
			: "";
		const special = item.is_warranty ? " is-warranty-line" : item.is_vas ? " is-vas-line" : "";

		return `
			<div class="ch-pos-cart-line${special}" data-idx="${idx}">
				<div class="cart-line-top">
					<span class="cart-item-name">
						${frappe.utils.escape_html(item.item_name)}
						${offer_tag}${serial_tag}${margin_tag}
					</span>
					<span class="cart-item-amount">₹${format_number(amount)}</span>
				</div>
				<div class="cart-line-bottom">
					<div class="cart-qty-controls">
						<button class="btn ch-pos-qty-minus" data-idx="${idx}">−</button>
						<span class="cart-qty-display">${item.qty}</span>
						<button class="btn ch-pos-qty-plus" data-idx="${idx}">+</button>
					</div>
					<span class="cart-item-rate">@ ₹${format_number(item.rate)}</span>
					<button class="btn btn-link text-danger ch-pos-cart-remove" data-idx="${idx}">
						<i class="fa fa-trash-o"></i>
					</button>
				</div>
			</div>`;
	}

	/** Render customer info badges (B2B/B2C, loyalty tier, credit) */
	_render_customer_info(info) {
		const row = this.wrapper.find(".ch-pos-customer-info");
		const badges = row.find(".ch-pos-cust-badges");
		const warning = this.wrapper.find(".ch-pos-credit-warning");
		let html = "";

		// B2B / B2C badge
		const type_cls = info.customer_type === "B2B" ? "badge-primary" : "badge-info";
		html += `<span class="ch-pos-badge ${type_cls}">${info.customer_type}</span>`;

		// Customer group
		if (info.customer_group) {
			html += `<span class="ch-pos-badge badge-muted">${frappe.utils.escape_html(info.customer_group)}</span>`;
		}

		// Price list applied
		if (info.price_list) {
			html += `<span class="ch-pos-badge badge-success"><i class="fa fa-tag" style="font-size:9px"></i> ${frappe.utils.escape_html(info.price_list)}</span>`;
		}

		// Loyalty
		if (info.loyalty && info.loyalty.points > 0) {
			const tier = info.loyalty.tier_name ? ` · ${info.loyalty.tier_name}` : "";
			html += `<span class="ch-pos-badge badge-warning"><i class="fa fa-star" style="font-size:9px"></i> ${format_number(info.loyalty.points)} pts${tier}</span>`;
		}

		// Credit info
		if (info.credit_limit > 0) {
			const avail = info.credit_available;
			const cls = avail > 0 ? "badge-success" : "badge-danger";
			html += `<span class="ch-pos-badge ${cls}">Credit: ₹${format_number(avail)} / ₹${format_number(info.credit_limit)}</span>`;
		}

		badges.html(html);
		row.show();

		// Credit warning if outstanding exceeds limit
		if (info.credit_limit > 0 && info.outstanding >= info.credit_limit) {
			warning.html(`<div class="alert alert-danger" style="margin:4px 0;padding:6px 10px;font-size:11px;border-radius:var(--pos-radius-sm)">
				<i class="fa fa-exclamation-triangle"></i> ${__("Credit limit exceeded")} — ${__("Outstanding")}: ₹${format_number(info.outstanding)}
			</div>`).show();
		} else {
			warning.hide();
		}

		// Update customer tag with type
		const tag = this.wrapper.find(".ch-pos-customer-tag");
		if (info.loyalty && info.loyalty.points > 0) {
			tag.addClass("loyalty");
		}
	}

	_update_summary() {
		const cart = PosState.cart;
		const s = this.wrapper.find(".ch-pos-cart-summary");

		let total_qty = 0, subtotal = 0, discount_total = 0;
		cart.forEach((item) => {
			total_qty += flt(item.qty);
			subtotal += flt(item.qty) * flt(item.rate);
			discount_total += flt(item.discount_amount || 0) * flt(item.qty);
		});

		let add_disc = 0;
		if (PosState.additional_discount_pct) {
			add_disc = subtotal * PosState.additional_discount_pct / 100;
		} else if (PosState.additional_discount_amt) {
			add_disc = PosState.additional_discount_amt;
		}

		const coupon_disc = flt(PosState.coupon_discount);
		const voucher_disc = flt(PosState.voucher_amount);
		const exchange_credit = flt(PosState.exchange_amount);
		const pe_credit = flt(PosState.product_exchange_credit);
		const grand_total = Math.max(0, subtotal - discount_total - add_disc - coupon_disc - voucher_disc - exchange_credit - pe_credit);

		s.find(".total-qty .value").text(total_qty);
		s.find(".discount-total .value").text(`₹${format_number(discount_total)}`);

		const add_row = s.find(".additional-disc");
		add_disc > 0 ? add_row.show().find(".value").text(`-₹${format_number(add_disc)}`) : add_row.hide();

		const coupon_row = s.find(".coupon-disc");
		coupon_disc > 0 ? coupon_row.show().find(".value").text(`-₹${format_number(coupon_disc)}`) : coupon_row.hide();

		const voucher_row = s.find(".voucher-disc");
		voucher_disc > 0 ? voucher_row.show().find(".value").text(`-₹${format_number(voucher_disc)}`) : voucher_row.hide();

		const ex_row = s.find(".exchange-credit");
		exchange_credit > 0 ? ex_row.show().find(".value").text(`-₹${format_number(exchange_credit)}`) : ex_row.hide();

		const pe_row = s.find(".product-exchange-credit");
		pe_credit > 0 ? pe_row.show().find(".value").text(`-₹${format_number(pe_credit)}`) : pe_row.hide();

		s.find(".grand-total .value").text(`₹${format_number(grand_total)}`);
	}
}

/**
 * Check if the current executive has permission to give a manual discount.
 * @param {number|null} pct - Discount percentage (if discount is %)
 * @param {number|null} amt - Discount amount (if discount is flat amount) — unused for cap check
 * @returns {boolean}
 */
function _check_discount_permission(pct = null, amt = null) {
	const access = PosState.executive_access;
	if (!access || !access.own_executive) return true; // no executive system → allow

	// Find the selected executive's permissions
	const company = PosState.active_company;
	const execs = (access.store_executives || {})[company] || [];
	const exec = execs.find((e) => e.name === PosState.sales_executive);

	if (!exec) return true; // fallback: allow

	if (!exec.can_give_discount) {
		frappe.show_alert({ message: __("You don't have permission to give discounts"), indicator: "orange" });
		return false;
	}

	if (pct !== null && exec.max_discount_pct > 0 && pct > exec.max_discount_pct) {
		frappe.show_alert({
			message: __("Maximum discount allowed: {0}%", [exec.max_discount_pct]),
			indicator: "orange",
		});
		return false;
	}

	return true;
}
