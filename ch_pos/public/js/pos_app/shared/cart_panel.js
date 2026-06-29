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
					<div class="ch-pos-customer-tag walk-in" title="${__("Click to edit customer")}">
						<i class="fa fa-user-o" style="font-size:10px"></i> ${__("Walk-in")}
					</div>
				</div>
			</div>
			<!-- A2. Customer Info Badges -->
			<div class="ch-pos-customer-info" style="display:none">
				<div class="ch-pos-cust-badges"></div>
				<div class="ch-pos-gstin-row" style="margin-top:4px;display:flex;align-items:center;gap:6px">
					<label style="font-size:10px;font-weight:600;color:var(--text-muted);white-space:nowrap;margin:0">${__("GST Invoice (GSTIN):")}</label>
					<input type="text"
						class="ch-pos-gstin-input form-control form-control-sm"
						placeholder="${__("Enter GSTIN for B2B")}"
						maxlength="15"
						style="font-size:11px;text-transform:uppercase;letter-spacing:0.5px;max-width:180px;padding:2px 6px;height:24px">
					<span class="ch-pos-gstin-status" style="font-size:10px"></span>
				</div>
			</div>

			<!-- B. Quick Retail Actions -->
			<div class="ch-pos-quick-actions">
				<div class="ch-pos-sales-order-banner" style="display:none"></div>
				<div class="ch-pos-exchange-banner" style="display:none"></div>
				<div class="ch-pos-product-exchange-banner" style="display:none"></div>
				<div class="ch-pos-exception-banner" style="display:none"></div>
				<div class="ch-pos-warranty-claim-banner" style="display:none"></div>
				<div class="ch-pos-combo-banner" style="display:none"></div>
				<div class="ch-pos-credit-warning" style="display:none"></div>
			</div>

			<!-- B2. Sale Type pills (#15 — relocated from payment dialog so the
			     cashier picks it up-front; persists on PosState.sale_type and is
			     re-read by payment_dialog.js) -->
			<div class="ch-pos-cart-saletype" id="ch-pos-cart-saletype" style="display:none">
				<label class="ch-pos-cart-saletype-label">${__("Sale Type")}</label>
				<div class="ch-pos-cart-saletype-pills" id="ch-pos-cart-saletype-pills"></div>
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
				<div class="summary-row sales-order-advance" style="display:none">
					<span><i class="fa fa-money"></i> ${__("Advance (Sales Order)")}</span>
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
				<button class="btn btn-xs btn-default ch-pos-btn-sell-vas" title="${__("Sell a Value Added Service — incl. for a phone bought elsewhere (external IMEI)")}">
					<i class="fa fa-shield"></i> ${__("Sell VAS")}
				</button>
				<button class="btn btn-xs btn-default ch-pos-btn-prebook" title="${__("Pre-Book the current cart — carries the items & customer into the Pre-Book screen")}">
					<i class="fa fa-bookmark"></i> ${__("Prebook")}
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
		this._render_sale_type_pills();
	}

	// ── #15 Sale Type pills (mirrored from payment_dialog) ──────────────
	_render_sale_type_pills() {
		const wrap = this.wrapper.find("#ch-pos-cart-saletype");
		const pills = this.wrapper.find("#ch-pos-cart-saletype-pills");
		if (!wrap.length || !pills.length) return;

		// Lazy-load the sale-type catalogue (cached on PosState).
		const company = PosState.active_company || PosState.company;
		const fetchTypes = PosState._sale_types_cache
			? Promise.resolve(PosState._sale_types_cache)
			: frappe.xcall("ch_pos.api.pos_api.get_sale_types", { company })
				.then((rows) => {
					PosState._sale_types_cache = rows || [];
					return PosState._sale_types_cache;
				}).catch(() => []);

		fetchTypes.then((rows) => {
			if (!rows || !rows.length) {
				wrap.hide();
				return;
			}
			wrap.show();
			pills.empty();
			for (const r of rows) {
				const code = r.code || r.sale_type_name;
				const isActive = (PosState.sale_type === r.sale_type_name) ? "active" : "";
				const pill = $(
					`<button type="button" class="btn btn-xs ch-pos-cart-saletype-btn ${isActive}"
						data-name="${frappe.utils.escape_html(r.sale_type_name)}"
						data-code="${frappe.utils.escape_html(code || "")}">
						${frappe.utils.escape_html(code || r.sale_type_name)}
					</button>`
				);
				pill.on("click", () => {
					PosState.sale_type = r.sale_type_name;
					pills.find(".ch-pos-cart-saletype-btn").removeClass("active");
					pill.addClass("active");
					EventBus.emit("sale_type:changed", { sale_type: r.sale_type_name });
				});
				pills.append(pill);
			}
		});
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
		const comp = PosState.active_company || PosState.company || (access.companies[0] && access.companies[0].company) || "";
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

		// Auto-select: prefer current selection, then own exec for this company, then cashier match, then first
		if (!PosState.sales_executive || !execs.find((e) => e.name === PosState.sales_executive)) {
			// Prefer own_by_company for the active company, fall back to own_executive
			const comp_exec = (access.own_by_company || {})[company];
			const own = comp_exec || access.own_executive;
			if (own && execs.find((e) => e.name === own.name)) {
				select.val(own.name);
				PosState.sales_executive = own.name;
				PosState.sales_executive_name = own.executive_name;
			} else {
				// Find exec matching current POS cashier for this company
				const cashier = PosState.pos_cashier || frappe.session.user;
				const user_exec = execs.find((e) => e.user === cashier);
				const fallback = user_exec || execs[0];
				if (fallback) {
					select.val(fallback.name);
					PosState.sales_executive = fallback.name;
					PosState.sales_executive_name = fallback.executive_name;
				}
			}
		}
	}

	_update_token_banner() {
		const banner = this.wrapper.find(".ch-pos-token-banner");
		if (PosState.kiosk_token) {
			const status = PosState.kiosk_token_status || "";
			// Map kiosk status → pill color (matches POS Kiosk Token status options).
			const _STATUS_COLORS = {
				"Waiting":     { bg: "#fef3c7", fg: "#92400e" },
				"Engaged":     { bg: "#dbeafe", fg: "#1e40af" },
				"In Progress": { bg: "#e0e7ff", fg: "#3730a3" },
				"Completed":   { bg: "#d1fae5", fg: "#065f46" },
				"Converted":   { bg: "#d1fae5", fg: "#065f46" },
				"Cancelled":   { bg: "#fee2e2", fg: "#991b1b" },
				"Dropped":     { bg: "#fee2e2", fg: "#991b1b" },
				"Expired":     { bg: "#f3f4f6", fg: "#374151" },
			};
			const sc = _STATUS_COLORS[status] || { bg: "#f3f4f6", fg: "#374151" };
			const status_pill = status
				? `<span class="ch-pos-token-status-pill"
						style="background:${sc.bg};color:${sc.fg};padding:2px 8px;border-radius:999px;
							font-size:11px;font-weight:700;letter-spacing:.2px;margin-left:4px;"
						title="${__("Kiosk Token Status")}">${frappe.utils.escape_html(status)}</span>`
				: "";
			banner.html(`
				<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;
					background:rgba(79,110,247,0.1);border-radius:var(--pos-radius-sm,6px);
					margin-bottom:6px;font-size:12px;font-weight:600;color:var(--pos-primary,#4f6ef7)">
					<i class="fa fa-ticket"></i>
					<span>${__("Token")}: ${frappe.utils.escape_html(PosState.kiosk_token)}</span>
					${status_pill}
					<button class="btn btn-link btn-xs ch-pos-unlink-token" style="margin-left:auto;padding:0;font-size:11px;color:var(--pos-text-muted)"
						title="${__("Unlink token")}">
						<i class="fa fa-times"></i>
					</button>
				</div>
			`).show();
			banner.find(".ch-pos-unlink-token").on("click", () => {
				const token_name = PosState.kiosk_token;
				const pos_profile = PosState.pos_profile;
				const done = () => {
					PosState.kiosk_token = null;
					PosState.kiosk_token_status = null;
					this._update_token_banner();
				};
				if (!token_name || !pos_profile) {
					done();
					return;
				}
				frappe.xcall("ch_pos.api.token_api.release_pos_billing", {
					token_name,
					pos_profile,
					revert_current: 1,
				}).then(done).catch(done);
			});
		} else {
			banner.hide().empty();
		}
	}

	_update_combo_banner(combos) {
		const banner = this.wrapper.find(".ch-pos-combo-banner");
		if (!banner.length) return;
		if (!combos || !combos.length) {
			banner.hide().empty();
			return;
		}
		const rows = combos.map((c) =>
			`<div style="display:flex;align-items:center;gap:8px;">
				<i class="fa fa-gift"></i>
				<span><strong>${frappe.utils.escape_html(c.offer_title)}</strong> —
				${__("Save")} <strong>₹${format_number(c.savings, null, 0)}</strong></span>
			</div>`
		).join("");
		banner.html(`
			<div style="padding:6px 10px;background:rgba(39,174,96,0.1);border-radius:var(--pos-radius-sm,6px);
				margin-bottom:6px;font-size:12px;color:#27ae60;">
				${rows}
			</div>
		`).show();
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

		// Phase 2 — F3 hotkey: focus this customer Link control
		EventBus.on("customer:focus", () => {
			if (this.customer_field && this.customer_field.$input) {
				this.customer_field.$input.focus().select();
			}
		});

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
		// #12: a pending buyback/exchange credit belongs to the PREVIOUS customer.
		// Clear it on a real customer change so it can never carry over to the
		// new customer's bill.
		if (PosState.exchange_amount || PosState.product_exchange_credit
			|| PosState.exchange_assessment || PosState.exchange_order) {
			PosState.exchange_assessment = null;
			PosState.exchange_amount = 0;
			PosState.exchange_order = null;
			PosState.product_exchange_credit = 0;
			PosState.product_exchange_invoice = null;
			this.wrapper.find(".ch-pos-exchange-banner, .ch-pos-product-exchange-banner").hide().empty();
			frappe.show_alert({
				message: __("Exchange credit removed — it was linked to the previous customer."),
				indicator: "orange",
			});
			EventBus.emit("cart:updated");
		}
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

	// _show_new_customer_dialog() {
	// 	window.ch_open_new_customer_dialog({
	// 		company: PosState.company,
	// 		on_success: (name, mobile) => {
	// 			this.customer_field.set_value(name);
	// 			this._commit_customer(name);
	// 		},
	// 		on_use_existing: (customer, cname) => {
	// 			this.customer_field.set_value(customer);
	// 			this._commit_customer(customer);
	// 		},
	// 	});
	// }
	

	// updated
	_show_new_customer_dialog() {
		window.ch_open_new_customer_dialog({
			company: PosState.company,
			on_success: (name, mobile, formData) => {
				// ⚡ After customer creation, sync ALL fields (especially address)
				// via our update_customer_complete API
				this._sync_new_customer_address(name, formData).then(() => {
					this.customer_field.set_value(name);
					this._commit_customer(name);
				});
			},
			on_use_existing: (customer, cname) => {
				this.customer_field.set_value(customer);
				this._commit_customer(customer);
			},
		});
	}

	_sync_new_customer_address(customer, formData) {
		// formData may not be passed; if not, skip
		if (!formData) {
			return Promise.resolve();
		}
		
		// Build payload from new customer dialog data
		const payload = {
			address_line1: formData.address_line1 || formData.address1 || "",
			address_line2: formData.address_line2 || formData.address2 || "",
			city: formData.city || "",
			state: formData.state || "",
			pincode: formData.pincode || formData.pin_code || "",
			gstin: formData.gstin || "",
			pan: formData.pan || formData.pan_number || "",
			area: formData.area || "",
		};
		
		// Only call if there's address data
		const hasData = Object.values(payload).some(v => v);
		if (!hasData) return Promise.resolve();
		
		return frappe.xcall("ch_pos.api.pos_api.update_customer_complete", {
			customer: customer,
			payload: payload,
		}).catch((err) => {
			console.error("Failed to sync new customer address:", err);
		});
	}

	_show_edit_customer_dialog() {
		const customer = PosState.customer;
		if (!customer) {
			frappe.show_alert({
				message: __("Select a customer first"),
				indicator: "orange"
			});
			return;
		}

		// ⚡ Force fresh fetch — bypass any cache
		frappe.xcall("ch_pos.api.pos_api.get_customer_full_details", { 
			customer,
			_ts: Date.now()   // cache buster
		})
			.then((doc) => {
				if (!doc || !doc.name) {
					frappe.msgprint(__("Customer not found"));
					return;
				}
				console.log("📥 Loaded customer data:", doc);  // debug
				this._open_edit_dialog(customer, doc);
			})
			.catch((err) => {
				console.error(err);
				frappe.msgprint(__("Could not load customer details"));
			});
	}

	_open_edit_dialog(customer, doc) {
		const me = this;

		const autofillFromPincode = (dialog, pin, prefix = "") => {
			if (!pin || !/^\d{6}$/.test(pin)) return;
			const stateField = prefix ? `${prefix}_state` : "state";
			const cityField  = prefix ? `${prefix}_city`  : "city";

			fetch(`https://api.postalpincode.in/pincode/${pin}`)
				.then(r => r.json())
				.then(data => {
					if (!data?.[0]?.PostOffice?.length) {
						frappe.show_alert({ message: __("Invalid pincode {0}", [pin]), indicator: "red" });
						return;
					}
					const po = data[0].PostOffice[0];
					dialog.set_value(stateField, po.State);
					dialog.set_value(cityField, po.District);
				})
				.catch(() => {});
		};

		const d = new frappe.ui.Dialog({
			title: __("Edit Customer"),
			size: "large",
			fields: [
				{ fieldname: "customer_name", fieldtype: "Data", label: __("Customer Name"),
				reqd: 1, read_only: 1, default: doc.customer_name || "" },
				{ fieldtype: "Column Break" },
				{ fieldname: "email_id", fieldtype: "Data", label: __("Email"),
				options: "Email", default: doc.email_id || "" },

				{ fieldtype: "Section Break" },
				{ fieldname: "mobile_no", fieldtype: "Data", label: __("Mobile Number"),
				reqd: 1, read_only: 1, default: doc.mobile_no || "" },
				{ fieldtype: "Column Break" },
				{ fieldname: "customer_group", fieldtype: "Link", options: "Customer Group",
				label: __("Customer Group"), default: doc.customer_group || "Individual" },

				{ fieldtype: "Section Break", label: __("Additional Contact") },
				{ fieldname: "alternate_no", fieldtype: "Data", label: __("Alternate Number"),
				default: doc.alternate_no || "" },
				{ fieldtype: "Column Break" },
				{ fieldname: "whatsapp_no", fieldtype: "Data", label: __("WhatsApp Number"),
				read_only: 1, default: doc.whatsapp_no || doc.mobile_no || "" },

				{ fieldtype: "Section Break", label: __("Billing Address") },
				{ fieldname: "address_line1", fieldtype: "Data", label: __("Address Line 1"),
				reqd: 1, default: doc.address_line1 || "" },
				{ fieldtype: "Column Break" },
				{ fieldname: "address_line2", fieldtype: "Data", label: __("Address Line 2"),
				default: doc.address_line2 || "" },

				{ fieldtype: "Section Break" },
				{
					fieldname: "state",
					fieldtype: "Autocomplete",
					label: __("State"),
					reqd: 1,
					default: doc.state || "",
					options: [],
					onchange: async function () {
						const newState = d.get_value("state");
						if (!newState) return;
						
						// Reload city autocomplete with cities of this state
						const cities = await frappe.db.get_list("CH City", {
							filters: { state: newState },
							fields: ["name"],
							limit: 0,
						});
						const cityNames = cities.map(c => c.name);
						if (d.fields_dict.city) {
							d.fields_dict.city.set_data(cityNames);
						}
						
						// Clear city if it doesn't belong to new state
						const currentCity = d.get_value("city");
						if (currentCity && !cityNames.includes(currentCity)) {
							d.set_value("city", "");
						}
						
					},
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "city",
					fieldtype: "Autocomplete",
					label: __("City"),
					reqd: 1,
					default: doc.city || "",
					options: [],
					onchange: async function () {
						const cityVal = d.get_value("city");
						if (!cityVal) return;
						
						// Auto-fill state from city's stored state
						const r = await frappe.db.get_value("CH City", cityVal, "state");
						const cityState = r?.message?.state;
						if (cityState && !d.get_value("state")) {
							d.set_value("state", cityState);
						}
					},
				},
				{ fieldtype: "Section Break" },
				{
					fieldname: "pincode",
					fieldtype: "Data",
					label: __("Pincode"),
					default: doc.pincode || "",
					description: __("Enter pincode — state & city auto-fill"),
					onchange: function () {
						autofillFromPincode(d, d.get_value("pincode"));
					},
				},
				{ fieldtype: "Column Break" },
				{ fieldname: "gstin", fieldtype: "Data", label: __("GSTIN"),
				default: doc.gstin || "" },

				{ fieldtype: "Section Break" },
				{ fieldname: "area", fieldtype: "Data", label: __("Area / Locality"),
				default: doc.area || "" },
				{ fieldtype: "Column Break" },
				{ fieldname: "pan", fieldtype: "Data", label: __("PAN Number"),
				default: doc.pan || "",
				description: __("10-character PAN (AAAAA9999A)") },

				{ fieldtype: "Section Break", label: __("Shipping") },
				{
					fieldname: "ship_to_same_as_billing",
					fieldtype: "Check",
					label: __("Ship to same as billing address"),
					default: doc.ship_to_same_as_billing ?? 1,
				},

				{ fieldtype: "Section Break" },
				{
					fieldname: "shipping_pincode",
					fieldtype: "Data",
					label: __("Shipping Pincode"),
					default: doc.shipping_pincode || "",
					depends_on: "eval:!doc.ship_to_same_as_billing",
					onchange: function () {
						autofillFromPincode(d, d.get_value("shipping_pincode"), "shipping");
					},
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "shipping_address_line1",
					fieldtype: "Data",
					label: __("Shipping Address Line 1"),
					default: doc.shipping_address_line1 || "",
					depends_on: "eval:!doc.ship_to_same_as_billing",
				},

				{ fieldtype: "Section Break" },
				{
					fieldname: "shipping_state",
					fieldtype: "Autocomplete",
					label: __("Shipping State"),
					default: doc.shipping_state || "",
					options: [],
					depends_on: "eval:!doc.ship_to_same_as_billing",
					onchange: async function () {
						const newState = d.get_value("shipping_state");
						if (!newState) return;
						const cities = await frappe.db.get_list("CH City", {
							filters: { state: newState },
							fields: ["name"],
							limit: 0,
						});
						const cityNames = cities.map(c => c.name);
						if (d.fields_dict.shipping_city) {
							d.fields_dict.shipping_city.set_data(cityNames);
						}
						const currentCity = d.get_value("shipping_city");
						if (currentCity && !cityNames.includes(currentCity)) {
							d.set_value("shipping_city", "");
						}
					},
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "shipping_city",
					fieldtype: "Autocomplete",
					label: __("Shipping City"),
					default: doc.shipping_city || "",
					options: [],
					depends_on: "eval:!doc.ship_to_same_as_billing",
					onchange: async function () {
						const cityVal = d.get_value("shipping_city");
						if (!cityVal) return;
						const r = await frappe.db.get_value("CH City", cityVal, "state");
						const cityState = r?.message?.state;
						if (cityState && !d.get_value("shipping_state")) {
							d.set_value("shipping_state", cityState);
						}
					},
				},
			],

			primary_action_label: __("Update"),
			primary_action: async (values) => {
				if (values.pan) {
					values.pan = values.pan.trim().toUpperCase();
					if (!/^[A-Z]{5}[0-9]{4}[A-Z]$/.test(values.pan)) {
						frappe.msgprint(__("Invalid PAN format (AAAAA9999A)"));
						return;
					}
				}
				if (values.gstin) values.gstin = values.gstin.trim().toUpperCase();
				if (values.pincode && values.pincode.trim() && !/^\d{6}$/.test(values.pincode.trim())) {
					frappe.msgprint(__("Billing pincode must be 6 digits"));
					return;
				}
				if (!values.ship_to_same_as_billing && values.shipping_pincode 
					&& values.shipping_pincode.trim() 
					&& !/^\d{6}$/.test(values.shipping_pincode.trim())) {
					frappe.msgprint(__("Shipping pincode must be 6 digits"));
					return;
				}

				frappe.xcall("ch_pos.api.pos_api.update_customer_complete", {
					customer: customer,
					payload: values,
				})
				.then(() => {
					frappe.show_alert({ message: __("Customer updated successfully"), indicator: "green" });
					d.hide();
					
					// Clear all caches
					PosState.customer_info = null;
					
					if (frappe.model && frappe.model.clear_doc) {
						frappe.model.clear_doc("Customer", customer);
					}
					
					// 🔥 NUCLEAR: Reset the customer Link field's awesomplete completely
					if (me.customer_field) {
						const $input = me.customer_field.$input;
						if ($input) {
							// Clear awesomplete cached suggestions
							const aw = $input.data("awesomplete");
							if (aw) {
								aw._list = [];
								aw.list = [];
								if (aw.ul) aw.ul.innerHTML = "";
							}
							// Trigger awesomplete to refetch
							$input.trigger("input");
						}
					}
					
					// 🔥 Clear Frappe's link search cache for Customer doctype
					try {
						// Clear any cached link queries
						if (frappe.utils && frappe.utils.cached_link_queries) {
							delete frappe.utils.cached_link_queries[`Customer:${customer}`];
						}
						if (frappe.boot && frappe.boot.link_title_doctypes) {
							delete frappe.boot.link_title_doctypes["Customer"];
						}
						// Clear search index cache
						if (frappe._link_search_cache) {
							frappe._link_search_cache = {};
						}
					} catch (e) {
						console.warn("Cache clear warn:", e);
					}
					
					// Re-commit with fresh data
					setTimeout(() => {
						me._commit_customer(customer);
					}, 300);
				})
				.catch((err) => {
					const msg = err.message
						|| (err._server_messages ? JSON.parse(err._server_messages)[0] : null)
						|| __("Could not update customer");
					frappe.msgprint({ title: __("Update Failed"), message: msg, indicator: "red" });
				});
			},
		});

		d.show();

	setTimeout(async () => {
		// Load all states for state field
		const states = await frappe.db.get_list("CH State", { fields: ["name"], limit: 0 });
		const stateNames = states.map(s => s.name);
		if (d.fields_dict.state) d.fields_dict.state.set_data(stateNames);
		if (d.fields_dict.shipping_state) d.fields_dict.shipping_state.set_data(stateNames);

		// Load cities FILTERED by billing state
		if (doc.state) {
			const cities = await frappe.db.get_list("CH City", {
				filters: { state: doc.state },
				fields: ["name"],
				limit: 0,
			});
			const cityNames = cities.map(c => c.name);
			if (d.fields_dict.city) d.fields_dict.city.set_data(cityNames);
		}

		// Load cities FILTERED by shipping state
		if (doc.shipping_state) {
			const cities = await frappe.db.get_list("CH City", {
				filters: { state: doc.shipping_state },
				fields: ["name"],
				limit: 0,
			});
			const cityNames = cities.map(c => c.name);
			if (d.fields_dict.shipping_city) d.fields_dict.shipping_city.set_data(cityNames);
		}
	}, 200);
	}

	bind() {
		const w = this.wrapper;

		// New customer quick-create
		w.on("click", ".ch-pos-btn-new-customer", () => this._show_new_customer_dialog());
		EventBus.on("customer:new", () => this._show_new_customer_dialog());

		// Edit existing customer — click on the customer tag (only when not walk-in)
		w.on("click", ".ch-pos-customer-tag.existing, .ch-pos-customer-tag.loyalty", () => {
			if (PosState.customer) {
				this._show_edit_customer_dialog();
			}
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

		// GSTIN input — live B2B/B2C toggle
		w.on("input", ".ch-pos-gstin-input", (e) => {
			const raw = $(e.currentTarget).val().trim().toUpperCase();
			PosState.billing_gstin = raw;
			this._update_gstin_badge(raw);
		});
		// Force uppercase on blur and trim
		w.on("blur", ".ch-pos-gstin-input", (e) => {
			const cleaned = $(e.currentTarget).val().trim().toUpperCase();
			$(e.currentTarget).val(cleaned);
			PosState.billing_gstin = cleaned;
		});

		// Cart actions
		w.on("click", ".ch-pos-btn-cancel", () => EventBus.emit("cart:cancel"));
		w.on("click", ".ch-pos-btn-hold", () => EventBus.emit("cart:hold"));
		w.on("click", ".ch-pos-btn-pay", () => EventBus.emit("cart:pay"));

		// Quick-link: held bills + reprint (in cart panel)
		w.on("click", ".ch-pos-btn-held-bills", () => EventBus.emit("held_bills:open"));
		w.on("click", ".ch-pos-btn-reprint-cart", () => EventBus.emit("reprint:open"));

		// Quick-link: standalone VAS sale (no device line required). Opens the
		// VAS dialog with no for_item so the cashier can sell a Value Added
		// Service against a customer's own phone (external IMEI) — a device not
		// purchased from us. Plans flagged allow_external_device drive this.
		w.on("click", ".ch-pos-btn-sell-vas", () => EventBus.emit("vas:open", {}));

		// #7: jump to the Pre-Book screen carrying the current cart + customer.
		// The cart lives on PosState, which persists across modes, so the
		// prebook workspace renders it pre-filled (no re-entry).
		w.on("click", ".ch-pos-btn-prebook", () => EventBus.emit("mode:switch", "prebook"));

		// Keep held-bills badge current whenever any held bill changes
		EventBus.on("held_bills:updated", () => this._refresh_held_count(w));

		// Initial badge count
		this._refresh_held_count(w);

		// Cart line qty / remove
		w.on("click", ".ch-pos-qty-plus", function () {
			EventBus.emit("cart:qty_plus", $(this).data("idx"));
		});
		w.on("click", ".ch-pos-qty-minus", function () {
			EventBus.emit("cart:qty_minus", $(this).data("idx"));
		});
		w.on("change", ".ch-pos-qty-input", function () {
			EventBus.emit("cart:qty_set", { idx: $(this).data("idx"), qty: $(this).val() });
		});
		w.on("keydown", ".ch-pos-qty-input", function (e) {
			if (e.key === "Enter") {
				e.preventDefault();
				$(this).trigger("change");
			}
		});
		w.on("click", ".ch-pos-cart-remove", function () {
			EventBus.emit("cart:remove", $(this).data("idx"));
		});

		// Line discount dialog removed — use cart-level discount only

		// Inline VAS button
		w.on("click", ".ch-pos-line-vas", function () {
			const idx = $(this).data("idx");
			const item = PosState.cart[idx];
			if (item) {
				PosState.last_vas_target = item;
				EventBus.emit("vas:open", { for_item: item });
			}
		});

		// Inline Exception button (line-level).
		// Allowed for serialized goods AND for non-serial lines such as
		// VAS / Protection Plans (Item.is_vas) where the exception is a
		// price override identified by item_code, not serial. Multi-exception
		// per bill is allowed — each cart line may carry its own request.
		w.on("click", ".ch-pos-line-exception", function () {
			const idx = $(this).data("idx");
			const item = PosState.cart[idx];
			if (!item) return;
			const item_code = item.item_code || item.item || item.code || item.name || "";
			// Require either a serial (serialized goods) OR an item_code
			// (VAS / accessories / plan rows). Without either we cannot
			// anchor the exception to anything meaningful.
			if (!item.serial_no && !item_code) return;
			if (item.exception_request) {
				frappe.show_alert({
					message: __("Exception {0} already linked to this line. Remove it before creating a new one.", [item.exception_request]),
					indicator: "orange",
				});
				return;
			}
			const customer = PosState.customer || PosState.default_customer || "";

			EventBus.emit("exception:open", {
				source: "cart_line",
				cart_idx: idx,
				item_code,
				item_name: item.item_name || "",
				serial_no: item.serial_no || "",
				customer,
				lock_serial: !!item.serial_no,
				lock_customer: true,
			});

			PosState.active_mode = "exceptions";
			EventBus.emit("mode:set", "exceptions");
			EventBus.emit("mode:switch", "exceptions");
		});
		w.on("click", ".ch-pos-line-remove-exception", function () {
			const idx = $(this).data("idx");
			EventBus.emit("cart:exception_remove", idx);
		});


		// Refresh on cart update
		EventBus.on("cart:updated", () => this.refresh());
		EventBus.on("exchange:applied", () => this.refresh());
		EventBus.on("product_exchange:applied", () => this.refresh());

		// Token banner — show when billing from queue
		EventBus.on("mode:switch", () => this._update_token_banner());
		EventBus.on("state:transaction_reset", () => this._update_token_banner());
		this._update_token_banner();

		// H-11: Combo offer banner
		EventBus.on("combo_offers:detected", (combos) => this._update_combo_banner(combos));

		// Exception & Warranty banners
		w.on("click", ".ch-pos-unlink-exception", () => {
			EventBus.emit("cart:exception_unlink_all");
		});
		w.on("click", ".ch-pos-unlink-warranty-claim", () => {
			PosState.warranty_claim = null;
			this._update_warranty_claim_banner();
		});
		EventBus.on("state:transaction_reset", () => {
			this._update_exception_banner();
			this._update_warranty_claim_banner();
		});
		// Exception applied from Exceptions workspace → refresh banner immediately
		// once cart is mounted (handles the "Apply & Bill" flow).
		EventBus.on("exception:applied", () => this._update_exception_banner());
		EventBus.on("mode:switch", () => this._update_exception_banner());

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
			this.wrapper.find(".ch-pos-gstin-input").val("");
			this.wrapper.find(".ch-pos-gstin-status").html("");
			PosState.billing_gstin = "";
			this.wrapper.find(".ch-pos-credit-warning").hide();
			// Reset sale type to default
			PosState.sale_type = null;
		});

		// Re-render executive bar when access data arrives or company changes
		EventBus.on("executive_access:loaded", () => this._render_executive_bar());
		EventBus.on("company:switched", () => this._render_executive_bar());
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
		this._update_sales_order_banner();
	}

	_cart_line_html(item, idx) {
		const price_list_rate = flt(item.price_list_rate || item.mrp || item.rate);
		const final_unit_rate = flt(item.rate);
		const discount_amt = Math.max(0, flt(item.discount_amount || 0));
		const final_amount = flt(item.qty) * final_unit_rate;
		const base_amount = flt(item.qty) * price_list_rate;
		const computed_discount_amt = Math.max(discount_amt, Math.max(0, price_list_rate - final_unit_rate));
		const discount_pct = price_list_rate > 0 && computed_discount_amt > 0
			? (computed_discount_amt / price_list_rate) * 100
			: 0;
		const offer_tag = item.offer_applied
			? `<span class="cart-offer-tag">${frappe.utils.escape_html(item.offer_applied)}</span>`
			: "";
		const serial_tag = item.serial_no
			? `<span class="cart-serial-tag">${frappe.utils.escape_html(item.serial_no)}</span>`
			: "";
		const margin_tag = (item.ch_item_type === "Refurbished" || item.ch_item_type === "Pre-Owned")
			? `<span class="cart-margin-tag">${frappe.utils.escape_html(item.ch_item_type)}</span>`
			: "";
		const exception_status = (item.exception_request_status || "").trim();
		let exception_status_icon = "";
		if (exception_status === "Pending") {
			exception_status_icon = `<i class="fa fa-spinner fa-spin" style="margin-right:4px;color:#d97706"></i>`;
		} else if (exception_status === "Approved" || exception_status === "Auto-Approved") {
			exception_status_icon = `<i class="fa fa-check-circle" style="margin-right:4px;color:#16a34a"></i>`;
		} else if (exception_status === "Rejected" || exception_status === "Expired") {
			exception_status_icon = `<i class="fa fa-times-circle" style="margin-right:4px;color:#dc2626"></i>`;
		}
		const exception_tag = item.exception_request
			? `<span class="cart-offer-tag" title="${__("Exception Request")}">${frappe.utils.escape_html(item.exception_request)}${exception_status ? ` • ${exception_status_icon}${frappe.utils.escape_html(exception_status)}` : ""}</span>`
			: "";
		const special = item.is_warranty ? " is-warranty-line" : item.is_vas ? " is-vas-line" : "";
		// Free-gift badge: shown when rate is zero but item is explicitly allowed (ch_allow_zero_rate)
		const free_gift_tag = (cint(item.ch_allow_zero_rate) && !flt(item.rate))
			? `<span class="cart-offer-tag" style="background:#dcfce7;color:#166534;" title="${__("Free gift / zero-rate item")}">
				<i class="fa fa-gift" style="margin-right:3px;"></i>${__("Free")}
			</span>`
			: "";
		const fixed_qty = cint(item.has_serial_no || item.is_warranty || item.is_vas);
		const whole_uom = cint(item.must_be_whole_number || fixed_qty);
		const qty_display = whole_uom
			? `${Math.round(flt(item.qty))}`
			: `${Math.round(flt(item.qty) * 1000) / 1000}`;
		const qty_controls = fixed_qty
			? `<div class="cart-qty-controls">
				<span class="cart-qty-display">${qty_display}</span>
			</div>`
			: whole_uom
			? `<div class="cart-qty-controls">
				<button class="btn ch-pos-qty-minus" data-idx="${idx}">−</button>
				<span class="cart-qty-display">${qty_display}</span>
				<button class="btn ch-pos-qty-plus" data-idx="${idx}">+</button>
			</div>`
			: `<div class="cart-qty-controls">
				<input type="number" class="form-control input-sm ch-pos-qty-input"
					data-idx="${idx}" min="0.001" step="0.001" value="${qty_display}">
			</div>`;
		const uom_tag = item.uom
			? `<span class="cart-offer-tag">${frappe.utils.escape_html(item.uom)}</span>`
			: "";

		// Show auto-applied offer discount as read-only label
		const disc_label = discount_amt > 0
			? `<span class="cart-disc-label has-disc" title="${__("Commercial discount")}">-${discount_pct ? format_number(discount_pct) + "% / " : ""}₹${format_number(computed_discount_amt)}</span>`
			: "";
		const price_breakdown = computed_discount_amt > 0
			? `<div class="cart-item-rate" style="font-size:11px;color:var(--text-muted)">
				${__("Actual")}: ₹${format_number(price_list_rate)} • ${__("Discount")}: ${format_number(discount_pct)}% • ${__("Final")}: ₹${format_number(final_unit_rate)}
			</div>`
			: "";

		// Inline action buttons.
		// Exception button is allowed on:
		//   • Serialized goods (price overrides anchored to IMEI)
		//   • Non-serial sellable lines: VAS / Protection Plans, accessories
		//     priced ad-hoc (anchored to item_code).
		// Warranty rows (own-warranty fee lines) stay locked — pricing is
		// derived from the device's plan, not exception-overridable here.
		let inline_actions = "";
		const _can_exception = !item.is_warranty && (item.serial_no || item.item_code);
		if (item.serial_no && !item.is_warranty && !item.is_vas) {
			// Serialized device: keep both VAS-attach and Exception buttons.
			const exception_action = item.exception_request
				? `<button class="btn btn-xs cart-line-action ch-pos-line-action-exception ch-pos-line-remove-exception" data-idx="${idx}" title="${__("Remove Exception")}">
					<i class="fa fa-times-circle"></i>
				</button>`
				: `<button class="btn btn-xs cart-line-action ch-pos-line-action-exception ch-pos-line-exception" data-idx="${idx}" title="${__("Exception")}">
					<i class="fa fa-exclamation-triangle"></i>
				</button>`;
			inline_actions = `
				<button class="btn btn-xs cart-line-action ch-pos-line-action-vas ch-pos-line-vas" data-idx="${idx}" title="${__("Add VAS")}">
					<i class="fa fa-shield"></i>
				</button>
				${exception_action}`;
		} else if (_can_exception) {
			// VAS / Protection Plan / non-serial line: exception button only.
			const exception_action = item.exception_request
				? `<button class="btn btn-xs cart-line-action ch-pos-line-action-exception ch-pos-line-remove-exception" data-idx="${idx}" title="${__("Remove Exception")}">
					<i class="fa fa-times-circle"></i>
				</button>`
				: `<button class="btn btn-xs cart-line-action ch-pos-line-action-exception ch-pos-line-exception" data-idx="${idx}" title="${__("Exception")}">
					<i class="fa fa-exclamation-triangle"></i>
				</button>`;
			inline_actions = exception_action;
		}

		return `
			<div class="ch-pos-cart-line${special}" data-idx="${idx}">
				<div class="cart-line-top">
					<span class="cart-item-name">
						${frappe.utils.escape_html(item.item_name)}
						${offer_tag}${free_gift_tag}${uom_tag}${serial_tag}${margin_tag}${exception_tag}
					</span>
					<span class="cart-item-amount">
						${computed_discount_amt > 0 ? `<span style="text-decoration:line-through;color:var(--text-muted);margin-right:6px">₹${format_number(base_amount)}</span>` : ""}
						₹${format_number(final_amount)}
					</span>
				</div>
				<div class="cart-line-bottom">
					${qty_controls}
					<span class="cart-item-rate">@ ₹${format_number(price_list_rate)}</span>
					${disc_label}
					${inline_actions}
					<button class="btn btn-link text-danger ch-pos-cart-remove" data-idx="${idx}">
						<i class="fa fa-trash-o"></i>
					</button>
				</div>
				${price_breakdown}
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

		// Pre-fill GSTIN input from customer's saved GSTIN (or retain billing-time entry)
		const gstin_input = row.find(".ch-pos-gstin-input");
		if (!PosState.billing_gstin && info.customer_gstin) {
			PosState.billing_gstin = info.customer_gstin;
		}
		gstin_input.val(PosState.billing_gstin || "");
		this._update_gstin_badge(PosState.billing_gstin || "");

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

	/** Update the GSTIN status badge (valid/invalid/empty) next to the input */
	_update_gstin_badge(gstin) {
		const status = this.wrapper.find(".ch-pos-gstin-status");
		const GSTIN_RE = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
		if (!gstin) {
			status.html(`<span style="color:var(--text-muted)">${__("B2C")}</span>`);
		} else if (GSTIN_RE.test(gstin)) {
			status.html(`<span style="color:var(--green)"><i class="fa fa-check"></i> ${__("B2B")}</span>`);
		} else if (gstin.length === 15) {
			status.html(`<span style="color:var(--red)"><i class="fa fa-times"></i> ${__("Invalid")}</span>`);
		} else {
			status.html(`<span style="color:var(--text-muted)">${gstin.length}/15</span>`);
		}
	}

	_update_summary() {
		const cart = PosState.cart;
		const s = this.wrapper.find(".ch-pos-cart-summary");

		let total_qty = 0, subtotal = 0, disc_total = 0;
		cart.forEach((item) => {
			const qty = flt(item.qty);
			const base_rate = flt(item.price_list_rate || item.mrp || item.rate || 0);
			const final_rate = flt(item.rate || 0);
			const line_base = qty * base_rate;
			const line_final = qty * final_rate;
			const explicit_disc = flt(item.discount_amount || 0) * qty;
			const implicit_disc = Math.max(0, line_base - line_final);

			total_qty += qty;
			subtotal += line_base;
			disc_total += Math.max(explicit_disc, implicit_disc);
		});

		// Expose for payment dialog discount cap checks
		PosState._cart_total_for_disc = subtotal - disc_total;

		const net             = subtotal - disc_total;
		const exchange_credit = flt(PosState.exchange_amount);
		const pe_credit       = flt(PosState.product_exchange_credit);
		const so_advance      = PosState.sales_order_reference ? flt(PosState.sales_order_advance) : 0;
		const grand_total     = Math.max(0, net - exchange_credit - pe_credit - so_advance);

		s.find(".total-qty .value").text(Number.isInteger(total_qty) ? total_qty : format_number(total_qty));

		const ex_row = s.find(".exchange-credit");
		exchange_credit > 0 ? ex_row.show().find(".value").text(`-₹${format_number(exchange_credit)}`) : ex_row.hide();

		const pe_row = s.find(".product-exchange-credit");
		pe_credit > 0 ? pe_row.show().find(".value").text(`-₹${format_number(pe_credit)}`) : pe_row.hide();

		const so_row = s.find(".sales-order-advance");
		so_advance > 0 ? so_row.show().find(".value").text(`-₹${format_number(so_advance)}`) : so_row.hide();

		s.find(".grand-total .value").text(`₹${format_number(grand_total)}`);
	}

	_update_sales_order_banner() {
		const banner = this.wrapper.find(".ch-pos-sales-order-banner");
		const so = PosState.sales_order_reference;
		if (!so) {
			banner.hide().empty();
			return;
		}
		const advance = flt(PosState.sales_order_advance);
		const summary = PosState.sales_order_summary || {};
		const reserved_count = (summary.reserved_serials || []).length;
		const due = summary.delivery_date ? `· ${__("Due")}: ${summary.delivery_date}` : "";
		banner.html(`
			<div style="display:flex;align-items:center;gap:10px;padding:8px 12px;
				background:linear-gradient(90deg,#ecfeff,#f0f9ff);
				border:1px solid #67e8f9;border-radius:var(--pos-radius-sm,6px);
				margin-top:6px;font-size:12px;">
				<i class="fa fa-bookmark" style="color:#0e7490;font-size:14px"></i>
				<div style="flex:1;line-height:1.35">
					<div style="font-weight:600;color:#0e7490">
						${__("Billing Sales Order")}: ${frappe.utils.escape_html(so)}
						${reserved_count ? ` <span class="text-muted">· ${__("{0} reserved IMEI(s)", [reserved_count])}</span>` : ""}
					</div>
					<div class="text-muted" style="font-size:11px">
						${advance > 0
							? __("Advance ₹{0} will be auto-applied at PAY", [format_number(advance)])
							: __("No advance on this order")}
						${due}
					</div>
				</div>
				<button type="button" class="btn btn-xs btn-default ch-pos-so-clear"
					title="${__("Discard the loaded Sales Order")}">
					<i class="fa fa-times"></i>
				</button>
			</div>
		`).show();

		banner.off("click", ".ch-pos-so-clear").on("click", ".ch-pos-so-clear", () => {
			frappe.confirm(__("Discard Sales Order {0} from cart?", [so]), () => {
				PosState.reset_transaction();
				EventBus.emit("cart:updated");
			});
		});
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

	_update_exception_banner() {
		const banner = this.wrapper.find(".ch-pos-exception-banner");
		if (PosState.exception_request) {
			banner.html(`
				<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;
					background:rgba(255,193,7,0.12);border-radius:var(--pos-radius-sm,6px);
					margin-top:6px;font-size:12px;font-weight:600;color:#b45309">
					<i class="fa fa-exclamation-triangle"></i>
					<span>${__("Exception")}: ${frappe.utils.escape_html(PosState.exception_request)}</span>
					<button class="btn btn-link btn-xs ch-pos-unlink-exception" style="margin-left:auto;padding:0;font-size:11px;color:var(--pos-text-muted)"
						title="${__("Remove exception")}">
						<i class="fa fa-times"></i>
					</button>
				</div>
			`).show();
		} else {
			banner.hide().empty();
		}
	}

	// ── Warranty Claim ──────────────────────────────────────────────────

	_show_warranty_claim_dialog() {
		const d = new frappe.ui.Dialog({
			title: __("Apply Warranty Claim"),
			fields: [
				{
					fieldname: "warranty_claim",
					fieldtype: "Link",
					options: "CH Warranty Claim",
					label: __("Warranty Claim"),
					reqd: 1,
					get_query: () => ({
						filters: {
							docstatus: 1,
							claim_status: "Approved",
							processing_fee_invoice: ["in", ["", null]],
						},
					}),
				},
			],
			size: "small",
			primary_action_label: __("Apply"),
			primary_action: (values) => {
				frappe.xcall("frappe.client.get_value", {
					doctype: "CH Warranty Claim",
					filters: { name: values.warranty_claim },
					fieldname: ["processing_fee_status", "processing_fee_amount", "processing_fee_invoice",
					            "customer", "customer_name", "serial_no", "item_code"],
				}).then((wc) => {
					if (!wc) {
						frappe.msgprint(__("Warranty Claim {0} not found", [values.warranty_claim]));
						return;
					}
					if (wc.processing_fee_status !== "Pending") {
						frappe.msgprint(__("Processing fee is {0}, not Pending", [wc.processing_fee_status]));
						return;
					}
					if (wc.processing_fee_invoice) {
						frappe.msgprint(__("Processing fee already invoiced via {0}", [wc.processing_fee_invoice]));
						return;
					}
					PosState.warranty_claim = values.warranty_claim;
					d.hide();
					frappe.show_alert({
						message: __("Warranty Claim {0} applied — ₹{1} processing fee", [
							values.warranty_claim, wc.processing_fee_amount || 0,
						]),
						indicator: "green",
					});
					this._update_warranty_claim_banner();
				});
			},
		});
		d.show();
	}

	_update_warranty_claim_banner() {
		const banner = this.wrapper.find(".ch-pos-warranty-claim-banner");
		if (PosState.warranty_claim) {
			banner.html(`
				<div style="display:flex;align-items:center;gap:8px;padding:6px 10px;
					background:rgba(108,117,125,0.1);border-radius:var(--pos-radius-sm,6px);
					margin-top:6px;font-size:12px;font-weight:600;color:#495057">
					<i class="fa fa-wrench"></i>
					<span>${__("Warranty")}: ${frappe.utils.escape_html(PosState.warranty_claim)}</span>
					<button class="btn btn-link btn-xs ch-pos-unlink-warranty-claim" style="margin-left:auto;padding:0;font-size:11px;color:var(--pos-text-muted)"
						title="${__("Remove warranty claim")}">
						<i class="fa fa-times"></i>
					</button>
				</div>
			`).show();
		} else {
			banner.hide().empty();
		}
	}
}
