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
				<button class="btn btn-outline-warning ch-pos-btn-exception">
					<i class="fa fa-exclamation-triangle"></i> ${__("Exception")}
				</button>
				<div class="ch-pos-exchange-banner" style="display:none"></div>
				<div class="ch-pos-product-exchange-banner" style="display:none"></div>
				<div class="ch-pos-exception-banner" style="display:none"></div>
				<div class="ch-pos-warranty-claim-banner" style="display:none"></div>
				<div class="ch-pos-combo-banner" style="display:none"></div>
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
		let otp_verified_number = "";
		let last_auto_synced_mobile = "";
		let duplicate_check_timer = null;
		let last_duplicate_phone_checked = "";
		let whatsapp_manually_edited = false;
		let syncing_whatsapp = false;
		let $mobile_status_div = $();
		let autofill_watch_timer = null;
		let last_otp_trigger_at = 0;
		const status_html = (message, color = "#6b7280") =>
			`<div style="font-size:12px;color:${color};padding-top:4px">${frappe.utils.escape_html(message || "")}</div>`;
		const dialog_alert = (message, indicator = "red") => {
			const alert_class = indicator === "red" ? "danger" : indicator === "orange" ? "warning" : "info";
			if (d?.set_alert) d.set_alert(frappe.utils.escape_html(message), alert_class);
			frappe.show_alert({ message, indicator });
		};
		const input_value = (fieldname) => {
			const f = d.fields_dict[fieldname];
			let v = (f && f.$input && f.$input.val()) || "";
			if (!v) {
				const $i = d.$wrapper.find(`[data-fieldname="${fieldname}"] input`).first();
				v = ($i.length ? $i.val() : "") || "";
			}
			if (!v) v = d.get_value(fieldname) || "";
			return String(v).trim();
		};
		const validate_email_input = () => {
			const email = input_value("email_id");
			if (!email) return true;
			const valid = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
			if (!valid) {
				frappe.show_alert({ message: __("Enter a valid Email address"), indicator: "red" });
				d.fields_dict.email_id.$input.focus();
			}
			return valid;
		};
		const otp_error_message = (err, fallback) => {
			const msg = (err && (err.message || err.exc || err._server_messages)) || "";
			if (String(msg).includes("Purpose cannot be")) {
				return __("OTP setup is not configured for POS customer verification. Please contact administrator.");
			}
			return (err && err.message) || fallback;
		};

		const check_existing_customer = (phone_no) => {
			return frappe.xcall("ch_pos.api.pos_api.find_existing_customer_by_phone", { phone_no })
				.then((res) => res || { exists: false })
				.catch((err) => {
					console.error("POS customer duplicate check failed", err);
					return { exists: false, error: err };
				});
		};
		const paint_whatsapp_input = (mobile) => {
			const ctl = d.fields_dict.whatsapp_number;
			const selectors = [
				'[data-fieldname="whatsapp_number"] input',
				'[data-fieldname="whatsapp_number"] textarea',
			];
			selectors.forEach((selector) => {
				d.$wrapper.find(selector).each((_, el) => {
					el.value = mobile;
					el.setAttribute("value", mobile);
				});
			});
			if (ctl?.$input?.length) {
				ctl.$input.val(mobile).attr("value", mobile);
			}
			if (ctl) {
				ctl.value = mobile;
				ctl.last_value = mobile;
				ctl.set_input?.(mobile);
			}
		};

		// Synchronously write WhatsApp value: direct DOM + Frappe model + async d.set_value (triple-path).
		const force_set_whatsapp = (mobile) => {
			syncing_whatsapp = true;
			// 1. Direct DOM — fastest, shows value immediately
			paint_whatsapp_input(mobile);
			// 2. Frappe control internal state
			const ctl = d.fields_dict.whatsapp_number;
			if (ctl) { ctl.value = mobile; ctl.last_value = mobile; }
			// 3. Dialog doc model
			(d.doc || (d.doc = {})).whatsapp_number = mobile;
			last_auto_synced_mobile = mobile;
			whatsapp_manually_edited = false;
			otp_verified_number = "";
			// 4. Frappe's own set_value (async, authoritative) — ensures reqd validation clears
			d.set_value("whatsapp_number", mobile)
				.then(() => paint_whatsapp_input(mobile))
				.catch(() => paint_whatsapp_input(mobile));
			requestAnimationFrame(() => paint_whatsapp_input(mobile));
			setTimeout(() => paint_whatsapp_input(mobile), 25);
			setTimeout(() => { syncing_whatsapp = false; }, 100);
		};

		const sync_whatsapp_from_mobile = () => {
			const mobile = input_value("mobile_no");
			(d.doc || (d.doc = {})).mobile_no = mobile;
			if (!mobile) return;

			const mobile_digits = mobile.replace(/\D/g, "");
			if (mobile_digits.length < 10) {
				if (input_value("whatsapp_number") === last_auto_synced_mobile) {
					force_set_whatsapp("");
				}
				if (typeof $mobile_status_div !== "undefined") $mobile_status_div.empty();
				return;
			}

			const whatsapp = input_value("whatsapp_number");
			const whatsapp_digits = whatsapp.replace(/\D/g, "");
			const whatsapp_is_auto_prefix = whatsapp && mobile_digits.startsWith(whatsapp_digits) && whatsapp_digits.length < 10;
			if (!whatsapp_manually_edited || !whatsapp || whatsapp === last_auto_synced_mobile || whatsapp_is_auto_prefix) {
				force_set_whatsapp(mobile);
			}

			const mobile_suffix = mobile_digits.slice(-10);
			clearTimeout(duplicate_check_timer);
			duplicate_check_timer = setTimeout(async () => {
				if (last_duplicate_phone_checked === mobile_suffix) return;
				last_duplicate_phone_checked = mobile_suffix;
				const hit = await check_existing_customer(mobile);
				if (typeof $mobile_status_div === "undefined") return;
				if (hit && hit.exists) {
					show_existing_banner(hit);
				} else {
					$mobile_status_div.empty();
				}
			}, 350);
		};

		// Show existing-customer inline (not frappe.confirm — nested modal hides behind this dialog)
		const show_existing_banner = (hit) => {
			if (!hit || !hit.exists || !hit.customer) { $mobile_status_div.empty(); return; }
			const esc_name = frappe.utils.escape_html(hit.customer_name || hit.customer);
			const esc_id = frappe.utils.escape_html(hit.customer);
			$mobile_status_div.html(
				`<div style="font-size:12px;color:#b91c1c;padding-top:4px;display:flex;align-items:center">` +
				`<span>${frappe.utils.escape_html(__("Customer already exists: {0} ({1})", [hit.customer_name || hit.customer, hit.customer]))}</span>` +
				`<button class="btn btn-xs btn-warning ch-use-existing-btn" style="margin-left:8px" data-customer="${esc_id}" data-customer-name="${esc_name}">${__("Use it")}</button>` +
				`</div>`
			);
		};
		const offer_use_existing_customer = (hit, phone_label) => {
			if (!hit || !hit.exists || !hit.customer) return false;
			show_existing_banner(hit);
			const message = __("Customer already exists: {0}. Click 'Use it' to select.", [hit.customer_name || hit.customer]);
			d.fields_dict.otp_status?.$wrapper.html(status_html(message, "#b45309"));
			dialog_alert(message, "orange");
			return true;
		};

		const d = new frappe.ui.Dialog({
			title: __("New Customer"),
			fields: [
				// ── Basic Info ──
				{ fieldname: "customer_name", fieldtype: "Data", label: __("Customer Name"), reqd: 1 },
				{ fieldname: "mobile_no", fieldtype: "Data", label: __("Mobile Number"), reqd: 1,
				  onchange: () => sync_whatsapp_from_mobile() },
				{ fieldtype: "Column Break" },
				{ fieldname: "email_id", fieldtype: "Data", label: __("Email"), options: "Email" },
				{ fieldname: "customer_group", fieldtype: "Link", label: __("Customer Group"),
				  options: "Customer Group", default: "Individual" },

				// ── Additional Contact ──
				{ fieldtype: "Section Break", label: __("Additional Contact") },
				{ fieldname: "alternate_phone", fieldtype: "Data", label: __("Alternate Number") },
				{ fieldtype: "Column Break" },
				{ fieldname: "whatsapp_number", fieldtype: "Data", label: __("WhatsApp Number"), reqd: 1 },

				{ fieldtype: "Section Break", label: __("WhatsApp Verification") },
				{ fieldname: "otp_code", fieldtype: "Data", label: __("OTP Code") },
				{ fieldtype: "Column Break" },
				{ fieldname: "otp_actions", fieldtype: "HTML", options: `
					<div class="ch-customer-otp-actions" style="display:flex;flex-direction:column;align-items:flex-start;gap:8px;padding-top:2px">
						<button type="button" class="btn btn-default btn-xs ch-send-customer-otp">${__("Send OTP")}</button>
						<button type="button" class="btn btn-default btn-xs ch-verify-customer-otp">${__("Verify OTP")}</button>
					</div>` },
				{ fieldname: "otp_status", fieldtype: "HTML", options: "" },

				// ── Address ──
				{ fieldtype: "Section Break", label: __("Address") },
				{ fieldname: "address_line1", fieldtype: "Data", label: __("Address Line 1") },
				{ fieldname: "address_line2", fieldtype: "Data", label: __("Address Line 2") },
				{ fieldtype: "Column Break" },
				{ fieldname: "city", fieldtype: "Link", options: "CH City", label: __("City"),
				  get_query: () => {
					const filters = { disabled: 0 };
					if (PosState.company) filters.company = PosState.company;
					return { filters };
				  } },
				{ fieldname: "state", fieldtype: "Data", label: __("State"), reqd: 1 },
				{ fieldtype: "Section Break" },
				{ fieldname: "pincode", fieldtype: "Data", label: __("Pincode") },
				{ fieldname: "area", fieldtype: "Data", label: __("Area / Locality") },
				{ fieldtype: "Column Break" },
				{ fieldname: "gstin", fieldtype: "Data", label: __("GSTIN") },

				// ── Billing / Shipping ──
				{ fieldtype: "Section Break", label: __("Shipping") },
				{ fieldname: "same_as_billing", fieldtype: "Check", label: __("Ship to same as billing address"), default: 1 },
				{ fieldname: "shipping_address_line1", fieldtype: "Data", label: __("Shipping Address Line 1"),
				  depends_on: "eval:!doc.same_as_billing" },
				{ fieldname: "shipping_city", fieldtype: "Data", label: __("Shipping City"),
				  depends_on: "eval:!doc.same_as_billing" },
				{ fieldtype: "Column Break" },
				{ fieldname: "shipping_state", fieldtype: "Data", label: __("Shipping State"),
				  depends_on: "eval:!doc.same_as_billing" },
				{ fieldname: "shipping_pincode", fieldtype: "Data", label: __("Shipping Pincode"),
				  depends_on: "eval:!doc.same_as_billing" },
			],
			size: "large",
			primary_action_label: __("Create"),
			primary_action: async (values) => {
				const customer_name = input_value("customer_name");
				const phone = input_value("mobile_no");
				if (!phone) {
					frappe.show_alert({ message: __("Mobile Number is mandatory"), indicator: "red" });
					return;
				}
				if (!assert_india_phone(input_el("mobile_no"), phone)) return;
				const whatsapp = input_value("whatsapp_number");
				if (!whatsapp || !assert_india_phone(input_el("whatsapp_number"), whatsapp)) return;
				if (!validate_email_input()) return;
				const email = input_value("email_id");

				const existing_by_mobile = phone ? await check_existing_customer(phone) : { exists: false };
				if (offer_use_existing_customer(existing_by_mobile, __("Mobile Number"))) return;

				const existing_by_whatsapp = await check_existing_customer(whatsapp);
				if (
					offer_use_existing_customer(
						existing_by_whatsapp,
						__("WhatsApp Number")
					)
				) {
					return;
				}

				if (otp_verified_number !== whatsapp) {
					frappe.show_alert({ message: __("Verify WhatsApp OTP before creating customer"), indicator: "red" });
					return;
				}
				frappe.xcall("ch_pos.api.pos_api.quick_create_customer", {
					customer_name,
					mobile_no: phone,
					email_id: email,
					customer_group: values.customer_group || "Individual",
					company: PosState.company,
					alternate_phone: values.alternate_phone || "",
					whatsapp_number: whatsapp,
					address_line1: values.address_line1 || "",
					address_line2: values.address_line2 || "",
					city: values.city || "",
					state: values.state || "",
					pincode: values.pincode || "",
					area: values.area || "",
					gstin: values.gstin || "",
					same_as_billing: values.same_as_billing ? 1 : 0,
					shipping_address_line1: values.shipping_address_line1 || "",
					shipping_city: values.shipping_city || "",
					shipping_state: values.shipping_state || "",
					shipping_pincode: values.shipping_pincode || "",
				}).then((name) => {
					d.hide();
					frappe.show_alert({ message: __("Customer {0} created", [name]), indicator: "green" });
					this.customer_field.set_value(name);
					this._commit_customer(name);   // explicit — don't rely on change callback
				});
			},
		});
		d.doc = d.doc || {};
		d.show();
		d.fields_dict.otp_status.$wrapper.html(status_html(__("OTP not verified")));

		// Inject status div directly below mobile_no (more reliable than fields_dict HTML in columns).
		$mobile_status_div = $('<div class="ch-mobile-status" style="margin-top:4px"></div>');
		d.$wrapper.find('[data-fieldname="mobile_no"]').after($mobile_status_div);
		$mobile_status_div.on("click", ".ch-use-existing-btn", (e) => {
			const $btn = $(e.currentTarget);
			const customer = $btn.data("customer");
			const cname = $btn.data("customer-name");
			if (!customer) return;
			d.hide();
			this.customer_field.set_value(customer);
			this._commit_customer(customer);
			frappe.show_alert({ message: __("Selected existing customer {0}", [cname || customer]), indicator: "green" });
		});

		// Make the dialog body scrollable so long forms (with shipping section) are reachable.
		d.$wrapper.find(".modal-dialog").css({ "max-width": "900px" });
		d.$wrapper.find(".modal-body").css({
			"max-height": "calc(100vh - 180px)",
			"overflow-y": "auto",
		});

		// Prevent Enter key from auto-submitting customer creation.
		d.$wrapper.find("form").on("keydown", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
			}
		});

		// Phone controls (mobile_no, whatsapp_number) use ControlPhone which has
		// async make_input. Bind via delegation so handlers work even before $input exists.
		const $body = d.$wrapper.find(".modal-body");

		// Sync WhatsApp + check for existing customer on every keystroke AND on blur.
		$body.on("input keyup paste change", '[data-fieldname="mobile_no"] input', () => {
			sync_whatsapp_from_mobile();
		});
		// On blur: reset manual-edit flag so mobile always reflects into WhatsApp.
		$body.on("blur", '[data-fieldname="mobile_no"] input', () => {
			whatsapp_manually_edited = false;
			sync_whatsapp_from_mobile();
		});

		$body.on("input", '[data-fieldname="whatsapp_number"] input', () => {
			if (syncing_whatsapp) return;
			const val = input_value("whatsapp_number");
			(d.doc || (d.doc = {})).whatsapp_number = val;
			whatsapp_manually_edited = val !== last_auto_synced_mobile;
			otp_verified_number = "";
			d.fields_dict.otp_status.$wrapper.html(status_html(__("OTP not verified")));
		});

		$body.on("awesomplete-selectcomplete change blur", '[data-fieldname="city"] input', () => {
			const city = input_value("city");
			if (!city) return;
			frappe.xcall("frappe.client.get_value", {
				doctype: "CH City",
				filters: { name: city },
				fieldname: ["city_name", "state"],
			}).then((row) => {
				if (!row || !row.state) return;
				d.set_value("state", row.state);
				if (d.fields_dict.state.$input) {
					d.fields_dict.state.$input.val(row.state);
				}
				(d.doc || (d.doc = {})).state = row.state;
			});
		});

		$body.on("change blur", '[data-fieldname="email_id"] input', validate_email_input);

		const send_otp_handler = async () => {
			const now = Date.now();
			if (now - last_otp_trigger_at < 350) return;
			last_otp_trigger_at = now;
			sync_whatsapp_from_mobile();
			const phone = input_value("mobile_no");
			const mobile_digits = phone.replace(/\D/g, "");
			if (mobile_digits.length >= 10) force_set_whatsapp(phone);

			d.fields_dict.otp_status.$wrapper.html(status_html(__("Checking customer..."), "#2563eb"));
			if (!phone) {
				dialog_alert(__("Mobile Number is mandatory"), "red");
				return;
			}
			if (!assert_india_phone(input_el("mobile_no"), phone)) {
				dialog_alert(__("Enter a valid Indian mobile number"), "orange");
				return;
			}

			const whatsapp = input_value("whatsapp_number");
			if (!whatsapp) {
				dialog_alert(__("WhatsApp Number is required to send OTP"), "red");
				d.fields_dict.otp_status.$wrapper.html(status_html(__("WhatsApp Number is required to send OTP"), "#b91c1c"));
				return;
			}
			if (!assert_india_phone(input_el("whatsapp_number"), whatsapp)) {
				dialog_alert(__("Enter a valid WhatsApp Number before sending OTP"), "orange");
				d.fields_dict.otp_status.$wrapper.html(status_html(__("Enter a valid WhatsApp Number before sending OTP"), "#b91c1c"));
				return;
			}
			if (!validate_email_input()) return;

			const send_btn = d.$wrapper.find(".ch-send-customer-otp").get(0);
			if (send_btn) send_btn.disabled = true;
			try {
				const existing_by_mobile = await check_existing_customer(phone);
				if (existing_by_mobile.error) {
					dialog_alert(__("Could not check existing customer. Please try again."), "red");
					d.fields_dict.otp_status.$wrapper.html(status_html(__("Could not check existing customer"), "#b91c1c"));
					return;
				}
				if (offer_use_existing_customer(existing_by_mobile, __("Mobile Number"))) return;

				if (whatsapp !== phone) {
					const existing_by_whatsapp = await check_existing_customer(whatsapp);
					if (existing_by_whatsapp.error) {
						dialog_alert(__("Could not check existing customer. Please try again."), "red");
						d.fields_dict.otp_status.$wrapper.html(status_html(__("Could not check existing customer"), "#b91c1c"));
						return;
					}
					if (offer_use_existing_customer(existing_by_whatsapp, __("WhatsApp Number"))) return;
				}

				d.fields_dict.otp_status.$wrapper.html(status_html(__("Sending OTP..."), "#2563eb"));
				frappe.dom.freeze(__("Sending OTP..."));
				const res = await frappe.xcall("ch_pos.api.pos_api.request_customer_whatsapp_otp", {
					mobile_no: whatsapp,
					customer_name: input_value("customer_name") || "Customer",
					email_id: input_value("email_id"),
				});
				otp_verified_number = "";
				const channels = [];
				if (res && res.sent_whatsapp) channels.push(__("WhatsApp"));
				if (res && res.sent_email) channels.push(__("Email"));
				const channel_text = channels.length ? channels.join(" + ") : __("OTP log");
				d.fields_dict.otp_status.$wrapper.html(status_html(__("OTP generated via {0}", [channel_text]), "#15803d"));
				frappe.show_alert({ message: __("OTP generated via {0}", [channel_text]), indicator: "green" });
			} catch (err) {
				const message = otp_error_message(err, __("Failed to send OTP"));
				d.fields_dict.otp_status.$wrapper.html(status_html(message, "#b91c1c"));
				dialog_alert(message, "red");
			} finally {
				frappe.dom.unfreeze();
				if (send_btn) send_btn.disabled = false;
			}
		};

		const verify_otp_handler = () => {
			const whatsapp = input_value("whatsapp_number");
			const otp_code = input_value("otp_code");
			if (!whatsapp || !assert_india_phone(input_el("whatsapp_number"), whatsapp)) return;
			if (!otp_code) {
				frappe.show_alert({ message: __("Enter OTP code"), indicator: "orange" });
				return;
			}
			frappe.xcall("ch_pos.api.pos_api.verify_customer_whatsapp_otp", {
				mobile_no: whatsapp,
				otp_code,
			}).then((res) => {
				if (res && res.valid) {
					otp_verified_number = whatsapp;
					d.fields_dict.otp_status.$wrapper.html(status_html(__("WhatsApp verified"), "#15803d"));
					frappe.show_alert({ message: __("WhatsApp verified"), indicator: "green" });
				} else {
					otp_verified_number = "";
					d.fields_dict.otp_status.$wrapper.html(status_html((res && res.message) || __("Invalid OTP"), "#b91c1c"));
				}
			}).catch((err) => {
				otp_verified_number = "";
				d.fields_dict.otp_status.$wrapper.html(status_html(otp_error_message(err, __("OTP verification failed")), "#b91c1c"));
			});
		};

		const bind_send_otp_button = () => {
			const button = d.$wrapper.find(".ch-send-customer-otp").get(0);
			if (!button || button._ch_send_otp_bound) return;
			button._ch_send_otp_bound = true;
			const run = (event) => {
				event?.preventDefault?.();
				event?.stopImmediatePropagation?.();
				send_otp_handler();
				return false;
			};
			button.onclick = run;
			button.onmousedown = run;
			button.onpointerdown = run;
		};

		const bind_verify_otp_button = () => {
			const button = d.$wrapper.find(".ch-verify-customer-otp").get(0);
			if (!button || button._ch_verify_otp_bound) return;
			button._ch_verify_otp_bound = true;
			const run = (event) => {
				event?.preventDefault?.();
				event?.stopImmediatePropagation?.();
				verify_otp_handler();
				return false;
			};
			button.onclick = run;
			button.onmousedown = run;
			button.onpointerdown = run;
		};

		const input_el = (fieldname) => {
			const f = d.fields_dict[fieldname];
			if (f && f.$input && f.$input[0]) return f.$input[0];
			const $i = d.$wrapper.find(`[data-fieldname="${fieldname}"] input`).first();
			return $i[0] || null;
		};

		// Bind using NATIVE addEventListener — most reliable, bypasses jQuery/Frappe layers entirely.
		// Called once immediately (inputs exist in DOM after dialog creation) and once more on shown.bs.modal.
		const bind_native_inputs = () => {
			const m_el = d.$wrapper.find('[data-fieldname="mobile_no"] input').get(0);
			const w_el = d.$wrapper.find('[data-fieldname="whatsapp_number"] input').get(0);
			if (m_el && !m_el._ch_bound) {
				m_el._ch_bound = true;
				m_el.addEventListener("input", () => sync_whatsapp_from_mobile());
				m_el.addEventListener("keyup", () => sync_whatsapp_from_mobile());
				m_el.addEventListener("change", () => sync_whatsapp_from_mobile());
				m_el.addEventListener("blur", () => { whatsapp_manually_edited = false; sync_whatsapp_from_mobile(); });
			}
			if (w_el && !w_el._ch_bound) {
				w_el._ch_bound = true;
				w_el.addEventListener("input", () => {
					if (syncing_whatsapp) return;
					const val = (w_el.value || "").trim();
					(d.doc || (d.doc = {})).whatsapp_number = val;
					whatsapp_manually_edited = val !== last_auto_synced_mobile;
					otp_verified_number = "";
					d.fields_dict.otp_status.$wrapper.html(status_html(__("OTP not verified")));
				});
			}
		};
		setTimeout(bind_native_inputs, 50);
		d.$wrapper.one("shown.bs.modal", bind_native_inputs);
		setTimeout(bind_send_otp_button, 50);
		setTimeout(bind_verify_otp_button, 50);
		d.$wrapper.one("shown.bs.modal", () => {
			bind_send_otp_button();
			bind_verify_otp_button();
		});

		// Final fallback: some browser autofill / Frappe hydration paths do not emit input/change.
		// While the dialog is open, keep WhatsApp equal to Mobile until the user manually edits WhatsApp.
		autofill_watch_timer = setInterval(() => {
			if (!d.display) return;
			const mobile = input_value("mobile_no");
			const whatsapp = input_value("whatsapp_number");
			const mobile_digits = mobile.replace(/\D/g, "");
			const whatsapp_digits = whatsapp.replace(/\D/g, "");
			const whatsapp_is_auto_prefix = whatsapp && mobile_digits.startsWith(whatsapp_digits) && whatsapp_digits.length < 10;
			if (mobile_digits.length >= 10 && (!whatsapp || whatsapp === last_auto_synced_mobile || whatsapp_is_auto_prefix || !whatsapp_manually_edited)) {
				force_set_whatsapp(mobile);
			}
		}, 250);
		d.$wrapper.one("hidden.bs.modal", () => {
			if (autofill_watch_timer) clearInterval(autofill_watch_timer);
		});

		bind_send_otp_button();
		bind_verify_otp_button();

		$(document)
			.off("click.ch_pos_customer_otp mousedown.ch_pos_customer_otp pointerdown.ch_pos_customer_otp")
			.on("pointerdown.ch_pos_customer_otp mousedown.ch_pos_customer_otp click.ch_pos_customer_otp", ".ch-send-customer-otp", (event) => {
				if (!$.contains(d.$wrapper.get(0), event.currentTarget)) return;
				event.preventDefault();
				event.stopImmediatePropagation();
				d.fields_dict.otp_status.$wrapper.html(status_html(__("Checking customer..."), "#2563eb"));
				send_otp_handler();
				return false;
			})
			.on("pointerdown.ch_pos_customer_otp mousedown.ch_pos_customer_otp click.ch_pos_customer_otp", ".ch-verify-customer-otp", (event) => {
				if (!$.contains(d.$wrapper.get(0), event.currentTarget)) return;
				event.preventDefault();
				event.stopImmediatePropagation();
				verify_otp_handler();
				return false;
			});
		d.$wrapper.one("hidden.bs.modal", () => {
			$(document).off("click.ch_pos_customer_otp");
		});
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

		// Keep held-bills badge current whenever any held bill changes
		EventBus.on("held_bills:updated", () => this._refresh_held_count(w));

		// Initial badge count
		this._refresh_held_count(w);

		// Quick actions
		w.on("click", ".ch-pos-btn-exception", () => this._show_exception_dialog());

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

		// H-11: Combo offer banner
		EventBus.on("combo_offers:detected", (combos) => this._update_combo_banner(combos));

		// Exception & Warranty banners
		w.on("click", ".ch-pos-unlink-exception", () => {
			PosState.exception_request = null;
			this._update_exception_banner();
		});
		w.on("click", ".ch-pos-unlink-warranty-claim", () => {
			PosState.warranty_claim = null;
			this._update_warranty_claim_banner();
		});
		EventBus.on("state:transaction_reset", () => {
			this._update_exception_banner();
			this._update_warranty_claim_banner();
		});

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
						${offer_tag}${uom_tag}${serial_tag}${margin_tag}
					</span>
					<span class="cart-item-amount">₹${format_number(amount)}</span>
				</div>
				<div class="cart-line-bottom">
					${qty_controls}
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

		s.find(".total-qty .value").text(Number.isInteger(total_qty) ? total_qty : format_number(total_qty));

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

	// ── Exception Request ───────────────────────────────────────────────

	_show_exception_dialog() {
		const d = new frappe.ui.Dialog({
			title: __("Apply Exception"),
			fields: [
				{
					fieldname: "exception_request",
					fieldtype: "Link",
					options: "CH Exception Request",
					label: __("Exception Request"),
					reqd: 1,
					get_query: () => ({
						filters: {
							status: "Approved",
							docstatus: 1,
							pos_invoice: ["in", ["", null]],
							company: PosState.company || undefined,
						},
					}),
				},
			],
			size: "small",
			primary_action_label: __("Apply"),
			primary_action: (values) => {
				frappe.xcall(
					"ch_item_master.ch_item_master.exception_api.check_exception_valid",
					{ exception_name: values.exception_request },
				).then((r) => {
					if (!r || !r.valid) {
						frappe.msgprint(__("Exception {0} is no longer valid (status: {1}). It may have expired.", [values.exception_request, r?.status || "Unknown"]));
						return;
					}
					PosState.exception_request = values.exception_request;
					d.hide();
					frappe.show_alert({ message: __("Exception {0} applied", [values.exception_request]), indicator: "green" });
					this._update_exception_banner();
				});
			},
		});
		d.show();
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
