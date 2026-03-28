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
import { format_number, assert_india_phone } from "./helpers.js";

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
				<div class="ch-pos-token-banner" style="display:none"></div>
				<div class="ch-pos-customer-select"></div>
				<div class="ch-pos-customer-row">
					<div class="ch-pos-customer-tag walk-in">
						<i class="fa fa-user-o" style="font-size:10px"></i> ${__("Walk-in")}
					</div>
				</div>
			</div>
			<!-- A2. Customer Info Badges -->
			<div class="ch-pos-customer-info" style="display:none">
				<div class="ch-pos-cust-badges"></div>
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


			<!-- E. Totals -->
			<div class="ch-pos-cart-summary">
				<div class="summary-row total-qty">
					<span>${__("Items")}</span>
					<span class="value">0</span>
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
			<div class="ch-pos-cart-quick-links">
				<button class="btn btn-xs btn-default ch-pos-btn-held-bills">
					<i class="fa fa-pause-circle"></i> ${__("Held Bills")}
					<span class="ch-held-count" style="display:none"></span>
				</button>
				<button class="btn btn-xs btn-default ch-pos-btn-reprint-cart" title="${__("Reprint today\'s invoices")}">
					<i class="fa fa-print"></i> ${__("Reprint")}
				</button>
			</div>
			<div class="ch-pos-cart-actions">
				<button class="btn btn-outline-danger ch-pos-btn-cancel">${__("Cancel")}</button>
				<button class="btn btn-outline-secondary ch-pos-btn-hold">
					<i class="fa fa-pause-circle-o"></i> ${__("Hold")}
				<span class="ch-pos-kbd-hint">F5</span>
			</button>
			<button class="btn btn-primary btn-lg ch-pos-btn-pay">
				<i class="fa fa-credit-card" style="margin-right:6px"></i>${__("PAY")}
				<span class="ch-pos-kbd-hint">F8</span>
				</button>
			</div>
		`);

		this._render_customer_selector();
		this._render_executive_bar();
	}

	/** Populate the executive / company bar from PosState.executive_access */
	_render_executive_bar() {
		const bar = this.wrapper.find(".ch-pos-executive-bar");
		const access = PosState.executive_access;
		if (!access || !access.companies || !access.companies.length) {
			bar.hide();
			return;
		}

		// Always show current company as badge (no toggle — companies are separate POS Profiles)
		const switcher = bar.find(".ch-pos-company-switcher");
		const comp = PosState.company || (access.companies[0] && access.companies[0].company) || "";
		const short = comp.replace(/ Pvt Ltd| Private Limited| Ltd/gi, "").trim();
		switcher.html(`<span class="ch-pos-company-single">${frappe.utils.escape_html(short)}</span>`).show();

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

	_update_token_banner() {
		const banner = this.wrapper.find(".ch-pos-token-banner");
		if (PosState.kiosk_token) {
			banner.html(`
				<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;
					background:rgba(79,110,247,0.1);border-radius:var(--pos-radius-sm,6px);
					margin-bottom:6px;font-size:12px;font-weight:600;color:var(--pos-primary,#4f6ef7)">
					<i class="fa fa-ticket"></i>
					<span>${__("Token")}: ${frappe.utils.escape_html(PosState.kiosk_token)}</span>
					<button class="btn btn-link btn-xs ch-pos-unlink-token" style="margin-left:auto;padding:0;font-size:11px;color:var(--pos-text-muted)"
						title="${__("Unlink token")}">
						<i class="fa fa-times"></i>
					</button>
				</div>
			`).show();
			banner.find(".ch-pos-unlink-token").on("click", () => {
				PosState.kiosk_token = null;
				this._update_token_banner();
			});
		} else {
			banner.hide().empty();
		}
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

		// Quick-create customer button — appended into the actions row beside the tag
		const btn_wrap = $(`<button class="btn btn-xs btn-link ch-pos-btn-new-customer" style="padding:0 4px;font-size:11px">
			<i class="fa fa-user-plus"></i> ${__("New Customer")}
		</button>`);
		this.wrapper.find(".ch-pos-customer-row").append(btn_wrap);
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
				const phone = (values.mobile_no || "").trim();
				if (phone && !assert_india_phone(d.fields_dict.mobile_no.$input[0], phone)) return;
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

		// Cart actions
		w.on("click", ".ch-pos-btn-cancel", () => EventBus.emit("cart:cancel"));
		w.on("click", ".ch-pos-btn-hold", () => EventBus.emit("cart:hold"));
		w.on("click", ".ch-pos-btn-pay", () => EventBus.emit("cart:pay"));

		// Quick-link: held bills + reprint (in cart panel)
		w.on("click", ".ch-pos-btn-held-bills", () => EventBus.emit("held_bills:open"));
		w.on("click", ".ch-pos-btn-reprint-cart", () => EventBus.emit("reprint:open"));

		// Keep held-bills badge current whenever any held bill changes
		EventBus.on("held_bills:updated", () => this._refresh_held_count(w));

		// Initial badge count
		this._refresh_held_count(w);

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

		// Line discount dialog removed — use cart-level discount only

		// Inline VAS button
		w.on("click", ".ch-pos-line-vas", function () {
			const idx = $(this).data("idx");
			const item = PosState.cart[idx];
			if (item) EventBus.emit("vas:open", { for_item: item });
		});


		// Refresh on cart update
		EventBus.on("cart:updated", () => this.refresh());
		EventBus.on("exchange:applied", () => this.refresh());
		EventBus.on("product_exchange:applied", () => this.refresh());

		// Token banner — show when billing from queue
		EventBus.on("mode:switch", () => this._update_token_banner());
		EventBus.on("state:transaction_reset", () => this._update_token_banner());
		this._update_token_banner();

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
			PosState.sale_type = null;
		});

		// Re-render executive bar when access data arrives
		EventBus.on("executive_access:loaded", () => this._render_executive_bar());
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
		const discount_amt = flt(item.discount_amount || 0);
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

		// Show auto-applied offer discount as read-only label
		const disc_label = discount_amt > 0
			? `<span class="cart-disc-label has-disc" title="${__("Offer discount")}">-₹${format_number(discount_amt)}</span>`
			: "";

		// Inline action buttons for serialized items
		let inline_actions = "";
		if (item.serial_no && !item.is_warranty && !item.is_vas) {
			inline_actions = `
				<button class="btn btn-xs cart-line-action ch-pos-line-vas" data-idx="${idx}" title="${__("Add VAS")}">
					<i class="fa fa-shield"></i>
				</button>`;
		}

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
					${disc_label}
					${inline_actions}
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

		// Order count
		if (info.order_count > 0) {
			html += `<span class="ch-pos-badge badge-muted"><i class="fa fa-shopping-bag" style="font-size:9px"></i> ${info.order_count} ${__("orders")}</span>`;
		}

		// Active warranties
		if (info.active_warranties > 0) {
			html += `<span class="ch-pos-badge badge-success"><i class="fa fa-shield" style="font-size:9px"></i> ${info.active_warranties} ${__("active")}</span>`;
		}

		// Active service jobs
		if (info.active_service_jobs > 0) {
			html += `<span class="ch-pos-badge badge-info"><i class="fa fa-wrench" style="font-size:9px"></i> ${info.active_service_jobs} ${__("jobs")}</span>`;
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

		let total_qty = 0, subtotal = 0, disc_total = 0;
		cart.forEach((item) => {
			total_qty  += flt(item.qty);
			subtotal   += flt(item.qty) * flt(item.rate);
			disc_total += flt(item.discount_amount || 0) * flt(item.qty);
		});

		// Expose for payment dialog discount cap checks
		PosState._cart_total_for_disc = subtotal - disc_total;

		const net             = subtotal - disc_total;
		const exchange_credit = flt(PosState.exchange_amount);
		const pe_credit       = flt(PosState.product_exchange_credit);
		const grand_total     = Math.max(0, net - exchange_credit - pe_credit);

		s.find(".total-qty .value").text(total_qty);

		const ex_row = s.find(".exchange-credit");
		exchange_credit > 0 ? ex_row.show().find(".value").text(`-₹${format_number(exchange_credit)}`) : ex_row.hide();

		const pe_row = s.find(".product-exchange-credit");
		pe_credit > 0 ? pe_row.show().find(".value").text(`-₹${format_number(pe_credit)}`) : pe_row.hide();

		s.find(".grand-total .value").text(`₹${format_number(grand_total)}`);
	}

	_refresh_held_count(w) {
		let count = 0;
		for (let i = 0; i < localStorage.length; i++) {
			if (localStorage.key(i).startsWith("ch_pos_held_")) count++;
		}
		const badge = (w || this.wrapper).find(".ch-held-count");
		if (count > 0) {
			badge.text(count).show();
		} else {
			badge.hide();
		}
	}
}
