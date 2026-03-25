/**
 * CH POS — Cart Service
 *
 * Business logic for cart operations: add, remove, qty change,
 * best offer application, warranty prompts, VAS, coupon validation,
 * exchange and product-exchange flows, hold invoice.
 */
import { PosState, EventBus } from "../state.js";
import { format_number } from "../shared/helpers.js";

export class CartService {
	constructor() {
		this._bind_events();
	}

	_bind_events() {
		EventBus.on("cart:add_item", (item_data) => this.add_to_cart(item_data));

		EventBus.on("cart:qty_plus", (idx) => {
			const item = PosState.cart[idx];
			if (item.has_serial_no) {
				frappe.show_alert({
					message: __("Scan another IMEI to add more units of {0}", [item.item_name]),
					indicator: "orange",
				});
				return;
			}
			item.qty += 1;
			this._apply_best_offer(item);
			EventBus.emit("cart:updated");
		});

		EventBus.on("cart:qty_minus", (idx) => {
			if (PosState.cart[idx].qty > 1) {
				PosState.cart[idx].qty -= 1;
				this._apply_best_offer(PosState.cart[idx]);
			} else {
				PosState.cart.splice(idx, 1);
			}
			EventBus.emit("cart:updated");
		});

		EventBus.on("cart:remove", (idx) => {
			PosState.cart.splice(idx, 1);
			EventBus.emit("cart:updated");
		});

		EventBus.on("cart:cancel", () => {
			if (!PosState.cart.length) return;
			frappe.confirm(__("Clear all items from cart?"), () => {
				PosState.reset_transaction();
			});
		});

		EventBus.on("cart:hold", () => this.hold_invoice());
		EventBus.on("cart:pay", () => {
			// Last-chance customer sync — Link control may not have fired change
			EventBus.emit("cart:pre_pay_sync");
			EventBus.emit("payment:open");
		});

		EventBus.on("held_bills:open", () => this._show_held_bills_dialog());
		EventBus.on("reprint:open", () => this._show_reprint_dialog());

		EventBus.on("coupon:apply", (code) => this._apply_coupon(code));
		EventBus.on("discount:changed", () => EventBus.emit("cart:updated"));

		EventBus.on("exchange:open", () => this._show_exchange_dialog());
		EventBus.on("vas:open", () => this._show_vas_dialog());
		EventBus.on("product_exchange:open", () => this._show_product_exchange_dialog());
		EventBus.on("manager:request_approval", (opts) => this._show_manager_approval_dialog(opts));

		// Direct serial add: scanned the FIFO-oldest (Sell First) serial from main screen.
		EventBus.on("cart:scan_serial", (item_data) => this._add_to_cart_direct_serial(item_data));
	}

	// ── Add to Cart ─────────────────────────────────────

	/**
	 * Direct-add path: called when the cashier scans the FIFO-oldest serial
	 * from the main screen.  Runs all the same pre-checks as add_to_cart /
	 * _prompt_serial_scan but skips the IMEI selection dialog.
	 */
	_add_to_cart_direct_serial(item_data) {
		const serial_no = (item_data.serial_no || "").trim();
		if (!serial_no) {
			EventBus.emit("cart:add_item", item_data);
			return;
		}

		// Pre-checks (mirrors add_to_cart)
		if (item_data.stock_qty <= 0) {
			frappe.show_alert({ message: __("Item {0} is out of stock", [item_data.item_name]), indicator: "red" });
			return;
		}
		if (PosState.voucher_code && !item_data.is_warranty && !item_data.is_vas) {
			const item_group = (item_data.item_group || "").toLowerCase();
			if (item_group !== "accessories") {
				frappe.show_alert({ message: __("A voucher is active. Only Accessories can be added to this cart."), indicator: "orange" }, 5);
				return;
			}
		}
		const dup = PosState.cart.find((c) => c.serial_no === serial_no);
		if (dup) {
			frappe.show_alert({ message: __("Serial {0} is already in the cart", [serial_no]), indicator: "orange" });
			return;
		}

		frappe.call({
			method: "ch_pos.api.pos_api.validate_serial_for_sale",
			args: { serial_no, item_code: item_data.item_code, warehouse: PosState.warehouse },
			callback: (r) => {
				const res = r.message || {};
				if (res.valid) {
					this._add_new_cart_item(item_data, serial_no);
					frappe.show_alert({ message: __("{0} added with IMEI {1}", [item_data.item_name, serial_no]), indicator: "green" });
				} else if (res.fifo_violation) {
					// Shouldn't happen for the oldest serial, but handle gracefully
					frappe.confirm(
						`<div style="line-height:1.7">
							<b style="color:#e67e22">&#9888; Older stock exists</b><br>
							<b>${frappe.utils.escape_html(res.oldest_serial)}</b>
							(received ${frappe.utils.escape_html(res.oldest_date || "")})
							should be sold first.<br><br>
							You selected <b>${frappe.utils.escape_html(serial_no)}</b>
							(received ${frappe.utils.escape_html(res.selected_date || "")}).<br><br>
							Do you still want to bill <b>${frappe.utils.escape_html(serial_no)}</b>?
							This exception will be recorded.
						</div>`,
						() => {
							frappe.call({
								method: "ch_pos.api.pos_api.log_fifo_override",
								args: { serial_no, item_code: item_data.item_code, warehouse: PosState.warehouse, oldest_serial: res.oldest_serial, oldest_date: res.oldest_date, pos_profile: PosState.pos_profile },
							});
							this._add_new_cart_item(item_data, serial_no);
							frappe.show_alert({ message: __("{0} added (FIFO override recorded)", [serial_no]), indicator: "orange" });
						}
					);
				} else {
					frappe.show_alert({ message: res.reason || __("Invalid serial number"), indicator: "red" });
				}
			},
		});
	}

	add_to_cart(item_data) {
		if (item_data.stock_qty <= 0) {
			frappe.show_alert({
				message: __("Item {0} is out of stock", [item_data.item_name]),
				indicator: "red",
			});
			return;
		}

		// Voucher restriction: if a voucher is active, only Accessories allowed
		if (PosState.voucher_code && !item_data.is_warranty && !item_data.is_vas) {
			const item_group = (item_data.item_group || "").toLowerCase();
			if (item_group !== "accessories") {
				frappe.show_alert({
					message: __("A voucher is active. Only Accessories can be added to this cart."),
					indicator: "orange",
				}, 5);
				return;
			}
		}

		// Serial/IMEI items: force scan before adding
		if (cint(item_data.has_serial_no)) {
			this._prompt_serial_scan(item_data);
			return;
		}

		const existing = PosState.cart.find(
			(c) => c.item_code === item_data.item_code && !c.is_warranty && !c.is_vas
		);

		if (existing) {
			existing.qty += 1;
			this._apply_best_offer(existing);
			EventBus.emit("cart:updated");
		} else {
			this._add_new_cart_item(item_data);
		}
	}

	_add_new_cart_item(item_data, serial_no) {
		const cart_item = {
			item_code: item_data.item_code,
			item_name: item_data.item_name,
			qty: 1,
			rate: item_data.selling_price || item_data.mrp || 0,
			mrp: item_data.mrp || 0,
			uom: item_data.stock_uom || "Nos",
			discount_percentage: 0,
			discount_amount: 0,
			offers: item_data.offers || [],
			applied_offer: null,
			warranty_plan: null,
			is_warranty: false,
			is_vas: false,
			has_serial_no: cint(item_data.has_serial_no),
			serial_no: serial_no || "",
			ch_item_type: item_data.ch_item_type || "",
		};
		this._apply_best_offer(cart_item);
		PosState.cart.push(cart_item);
		EventBus.emit("cart:updated");
		this._prompt_warranty(item_data, cart_item);
	}

	/** Prompt for IMEI/serial selection from available stock, with manual scan fallback */
	_prompt_serial_scan(item_data) {
		const dlg = new frappe.ui.Dialog({
			title: __("Select IMEI — {0}", [item_data.item_name]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "serials_area",
					options: `<div class="ch-imei-picker">
						<div style="text-align:center;padding:16px 0;">
							<i class="fa fa-spinner fa-spin" style="font-size:24px;opacity:0.4"></i>
							<p class="text-muted" style="margin-top:8px">${__("Loading available IMEIs...")}</p>
						</div>
					</div>`,
				},
				{ fieldtype: "Section Break", label: __("Or scan manually") },
				{
					fieldname: "serial_no",
					fieldtype: "Data",
					label: __("IMEI / Serial No"),
					description: __("Scan barcode or type IMEI if not in list above"),
				},
			],
			size: "large",
			primary_action_label: __("Add to Cart"),
			primary_action: (values) => {
				const serial = (values.serial_no || "").trim();
				// Check if one was selected from the picker
				const selected = dlg.$wrapper.find(".ch-imei-row.selected").data("serial");
				const final_serial = selected || serial;
				if (!final_serial) {
					frappe.show_alert({ message: __("Select or scan an IMEI"), indicator: "orange" });
					return;
				}

				// Check if this serial is already in the cart
				const dup = PosState.cart.find((c) => c.serial_no === final_serial);
				if (dup) {
					frappe.show_alert({ message: __("Serial {0} is already in the cart", [final_serial]), indicator: "orange" });
					return;
				}

				dlg.disable_primary_action();
				frappe.call({
					method: "ch_pos.api.pos_api.validate_serial_for_sale",
					args: {
						serial_no: final_serial,
						item_code: item_data.item_code,
						warehouse: PosState.warehouse,
					},
					callback: (r) => {
						const res = r.message || {};
						if (res.valid) {
							dlg.hide();
							this._add_new_cart_item(item_data, final_serial);
							frappe.show_alert({
								message: __("{0} added with IMEI {1}", [item_data.item_name, final_serial]),
								indicator: "green",
							});
						} else if (res.fifo_violation) {
							// Soft FIFO warning — ask for confirmation before proceeding
							dlg.enable_primary_action();
							frappe.confirm(
								`<div style="line-height:1.7">
									<b style="color:#e67e22">⚠ Older stock exists</b><br>
									<b>${frappe.utils.escape_html(res.oldest_serial)}</b>
									(received ${frappe.utils.escape_html(res.oldest_date || "")})
									should be sold first.<br><br>
									You selected <b>${frappe.utils.escape_html(final_serial)}</b>
									(received ${frappe.utils.escape_html(res.selected_date || "")}).<br><br>
									Do you still want to bill <b>${frappe.utils.escape_html(final_serial)}</b>?
									This exception will be recorded.
								</div>`,
								() => {
									// User confirmed override — log exception then add to cart
									frappe.call({
										method: "ch_pos.api.pos_api.log_fifo_override",
										args: {
											serial_no: final_serial,
											item_code: item_data.item_code,
											warehouse: PosState.warehouse,
											oldest_serial: res.oldest_serial,
											oldest_date: res.oldest_date,
											pos_profile: PosState.pos_profile,
										},
									});
									dlg.hide();
									this._add_new_cart_item(item_data, final_serial);
									frappe.show_alert({
										message: __("{0} added (FIFO override recorded)", [final_serial]),
										indicator: "orange",
									});
								},
								() => {
									// User cancelled — re-enable so they can pick the older serial
									frappe.show_alert({
										message: __("Please select {0} to follow FIFO", [res.oldest_serial]),
										indicator: "blue",
									});
								}
							);
						} else {
							dlg.enable_primary_action();
							frappe.show_alert({
								message: res.reason || __("Invalid serial number"),
								indicator: "red",
							});
						}
					},
				});
			},
		});
		dlg.show();

		// Load available serials
		frappe.xcall("ch_pos.api.search.get_available_serials", {
			item_code: item_data.item_code,
			warehouse: PosState.warehouse,
		}).then((serials) => {
			const area = dlg.$wrapper.find(".ch-imei-picker");
			if (!serials || !serials.length) {
				area.html(`<div class="text-muted text-center" style="padding:12px">
					${__("No IMEIs in stock at this warehouse. Use manual scan below.")}
				</div>`);
				setTimeout(() => dlg.fields_dict.serial_no.$input.focus(), 100);
				return;
			}

			// Filter out serials already in cart
			const cart_serials = new Set(PosState.cart.map(c => c.serial_no).filter(Boolean));
			const available = serials.filter(s => !cart_serials.has(s.serial_no));

			const rows = available.map((s, idx) => {
				const warranty_info = s.warranty_expiry_date
					? `<span class="text-muted" style="font-size:11px">WE: ${frappe.datetime.str_to_user(s.warranty_expiry_date)}</span>`
					: "";
				// FIFO badge: the first serial (oldest inward date) should be sold first
				const fifo_badge = s.is_oldest
					? `<span style="font-size:10px;background:#fff3cd;color:#856404;border:1px solid #ffc107;border-radius:4px;padding:1px 5px;margin-left:6px">${__("Sell First")}</span>`
					: "";
				const inward_info = s.inward_date && idx > 0
					? `<span class="text-muted" style="font-size:10px;margin-left:4px">In: ${frappe.datetime.str_to_user(s.inward_date)}</span>`
					: "";
				return `<div class="ch-imei-row" data-serial="${frappe.utils.escape_html(s.serial_no)}">
					<span class="ch-imei-serial">${frappe.utils.escape_html(s.serial_no)}</span>
					${fifo_badge}${inward_info}${warranty_info}
				</div>`;
			}).join("");

			area.html(`
				<div style="font-size:12px;color:var(--pos-text-muted);margin-bottom:6px">
					${__("{0} available", [available.length])}
				</div>
				<div class="ch-imei-list" style="max-height:240px;overflow-y:auto;border:1px solid var(--pos-border-light, #e5e7eb);border-radius:var(--pos-radius, 8px)">
					${rows}
				</div>
			`);

			// Click to select
			area.on("click", ".ch-imei-row", function () {
				area.find(".ch-imei-row").removeClass("selected");
				$(this).addClass("selected");
				dlg.set_value("serial_no", $(this).data("serial"));
			});
		});
	}

	// ── Offer Application ───────────────────────────────
	_apply_best_offer(cart_item) {
		const offers = cart_item.offers || [];
		if (!offers.length) {
			cart_item.applied_offer = null;
			cart_item.discount_amount = 0;
			cart_item.discount_percentage = 0;
			return;
		}
		const best = offers[0];
		cart_item.applied_offer = best;
		if (best.value_type === "Percentage") {
			cart_item.discount_percentage = flt(best.value);
			cart_item.discount_amount = flt(cart_item.rate * best.value / 100);
		} else if (best.value_type === "Amount") {
			cart_item.discount_amount = flt(best.value);
			cart_item.discount_percentage = cart_item.rate ? flt(best.value / cart_item.rate * 100) : 0;
		} else if (best.value_type === "Price Override") {
			cart_item.discount_amount = flt(cart_item.rate - best.value);
			cart_item.discount_percentage = cart_item.rate ? flt(cart_item.discount_amount / cart_item.rate * 100) : 0;
		}
	}

	// ── Warranty Prompt ─────────────────────────────────
	_prompt_warranty(item_data, cart_item) {
		frappe.call({
			method: "ch_pos.api.pos_api.get_warranty_plans",
			args: {
				item_code: item_data.item_code,
				item_group: item_data.item_group,
				brand: item_data.brand,
			},
			callback: (r) => {
				const plans = r.message || [];
				if (!plans.length) return;
				this._show_warranty_dialog(plans, cart_item);
			},
		});
	}

	_show_warranty_dialog(plans, cart_item) {
		const options = plans.map(
			(p) => `${p.name} — ${p.plan_name} (${p.duration_months}m) ₹${format_number(p.price)}`
		);
		const dialog = new frappe.ui.Dialog({
			title: __("Add Extended Warranty?"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p class="text-muted">${__("Warranty plans for")} <b>${frappe.utils.escape_html(cart_item.item_name)}</b></p>`,
				},
				{
					fieldname: "plan",
					fieldtype: "Select",
					label: __("Warranty Plan"),
					options: ["None", ...options].join("\n"),
					default: "None",
				},
			],
			primary_action_label: __("Add"),
			primary_action: (values) => {
				dialog.hide();
				if (values.plan === "None") return;
				const sel_name = values.plan.split(" — ")[0];
				const sel_plan = plans.find((p) => p.name === sel_name);
				if (!sel_plan) return;

				// Prevent duplicate: same warranty plan on same device/IMEI
				const dup = PosState.cart.find(
					(c) => c.is_warranty && c.warranty_plan === sel_plan.name
						&& c.for_item_code === cart_item.item_code
						&& (c.for_serial_no || "") === (cart_item.serial_no || "")
				);
				if (dup) {
					frappe.show_alert({ message: __("This warranty is already added for this device"), indicator: "orange" });
					return;
				}

				PosState.cart.push({
					item_code: sel_plan.service_item || sel_plan.name,
					item_name: `🛡 ${sel_plan.plan_name} (${sel_plan.duration_months}m)`,
					qty: 1,
					rate: flt(sel_plan.price),
					mrp: flt(sel_plan.price),
					uom: "Nos",
					discount_percentage: 0,
					discount_amount: 0,
					offers: [],
					applied_offer: null,
					warranty_plan: sel_plan.name,
					for_item_code: cart_item.item_code,
					for_serial_no: cart_item.serial_no || "",
					is_warranty: true,
					is_vas: false,
				});
				EventBus.emit("cart:updated");
				frappe.show_alert({ message: __("{0} added", [sel_plan.plan_name]), indicator: "green" });
			},
			secondary_action_label: __("Skip"),
			secondary_action: () => dialog.hide(),
		});
		dialog.show();
	}

	// ── Coupon / Voucher ────────────────────────────────
	_apply_coupon(code) {
		// Try coupon first, then voucher
		frappe.call({
			method: "ch_pos.api.pos_api.validate_coupon",
			args: {
				coupon_code: code,
				customer: PosState.customer,
				cart_total: this._get_subtotal(),
			},
			callback: (r) => {
				if (r.message && r.message.valid) {
					PosState.coupon_code = code;
					PosState.coupon_discount = flt(r.message.discount_amount);
					PosState.voucher_code = null;
					PosState.voucher_amount = 0;
					EventBus.emit("cart:updated");
					EventBus.emit("coupon:applied", { code, amount: PosState.coupon_discount });
				} else {
					// Not a valid coupon — try as voucher
					this._try_voucher(code);
				}
			},
		});
	}

	_try_voucher(code) {
		const cart_total = this._get_subtotal();
		frappe.call({
			method: "ch_item_master.ch_item_master.voucher_api.validate_voucher",
			args: {
				voucher_code: code,
				cart_total: cart_total,
				customer: PosState.customer,
				channel: null,
			},
			callback: (r) => {
				if (r.message && r.message.valid) {
					PosState.voucher_code = r.message.voucher_code;
					PosState.voucher_amount = flt(r.message.applicable_amount);
					PosState.voucher_name = r.message.voucher_name;
					PosState.voucher_balance = flt(r.message.balance);
					PosState.coupon_code = null;
					PosState.coupon_discount = 0;
					EventBus.emit("cart:updated");
					EventBus.emit("coupon:applied", {
						code,
						amount: PosState.voucher_amount,
						is_voucher: true,
						balance: PosState.voucher_balance,
					});
				} else {
					EventBus.emit("coupon:invalid",
						r.message?.reason || __("Invalid coupon or voucher code"));
				}
			},
		});
	}

	// ── Exchange Dialog ─────────────────────────────────
	_show_exchange_dialog() {
		let exchange_data = null;
		let active_tab = "lookup";
		const dialog = new frappe.ui.Dialog({
			title: __("Buyback Exchange"),
			fields: [
				{
					fieldname: "tab_bar_html",
					fieldtype: "HTML",
					options: `<div class="ch-pos-exchange-tabs" style="display:flex;gap:8px;margin-bottom:12px">
						<button class="btn btn-sm btn-primary ch-exc-tab" data-tab="lookup">${__("Find Existing")}</button>
						<button class="btn btn-sm btn-default ch-exc-tab" data-tab="new">${__("Add Old Device")}</button>
					</div>`,
				},
				// --- Lookup tab fields ---
				{
					fieldname: "lookup_mode",
					fieldtype: "Select",
					label: __("Find by"),
					options: "Assessment ID\nIMEI / Serial\nCustomer Mobile",
					default: "Assessment ID",
					depends_on: "eval:doc.__tab === 'lookup'",
				},
				{
					fieldname: "assessment",
					fieldtype: "Link",
					label: __("Buyback Assessment"),
					options: "Buyback Assessment",
					depends_on: "eval:doc.__tab === 'lookup' && doc.lookup_mode === 'Assessment ID'",
					get_query: () => ({ filters: { status: ["in", ["Submitted", "Inspection Created"]] } }),
				},
				{
					fieldname: "imei_serial",
					fieldtype: "Data",
					label: __("IMEI / Serial No"),
					depends_on: "eval:doc.__tab === 'lookup' && doc.lookup_mode === 'IMEI / Serial'",
				},
				{
					fieldname: "mobile_no",
					fieldtype: "Data",
					label: __("Customer Mobile"),
					depends_on: "eval:doc.__tab === 'lookup' && doc.lookup_mode === 'Customer Mobile'",
				},
				// --- New Device tab fields ---
				{
					fieldname: "new_item_code",
					fieldtype: "Link",
					label: __("Device Model"),
					options: "Item",
					depends_on: "eval:doc.__tab === 'new'",
					get_query: () => ({
						filters: { has_serial_no: 1, disabled: 0 },
					}),
				},
				{
					fieldname: "new_imei",
					fieldtype: "Data",
					label: __("IMEI / Serial"),
					depends_on: "eval:doc.__tab === 'new'",
				},
				{
					fieldname: "new_mobile",
					fieldtype: "Data",
					label: __("Customer Mobile"),
					depends_on: "eval:doc.__tab === 'new'",
				},
				{ fieldtype: "Section Break", label: __("Condition Checks"), depends_on: "eval:doc.__tab === 'new'" },
				{ fieldname: "chk_screen", fieldtype: "Check", label: __("Screen OK"), default: 1, depends_on: "eval:doc.__tab === 'new'", columns: 4 },
				{ fieldname: "chk_body", fieldtype: "Check", label: __("Body OK"), default: 1, depends_on: "eval:doc.__tab === 'new'", columns: 4 },
				{ fieldname: "chk_buttons", fieldtype: "Check", label: __("Buttons OK"), default: 1, depends_on: "eval:doc.__tab === 'new'", columns: 4 },
				{ fieldtype: "Column Break", depends_on: "eval:doc.__tab === 'new'" },
				{ fieldname: "chk_charging", fieldtype: "Check", label: __("Charging OK"), default: 1, depends_on: "eval:doc.__tab === 'new'", columns: 4 },
				{ fieldname: "chk_camera", fieldtype: "Check", label: __("Camera OK"), default: 1, depends_on: "eval:doc.__tab === 'new'", columns: 4 },
				{ fieldname: "chk_speaker_mic", fieldtype: "Check", label: __("Speaker/Mic OK"), default: 1, depends_on: "eval:doc.__tab === 'new'", columns: 4 },
				// --- Shared result section ---
				{ fieldtype: "Section Break", label: __("Exchange Details") },
				{
					fieldname: "exchange_details_html",
					fieldtype: "HTML",
					options: `<p class="text-muted">${__("Search for an assessment or add a new device above")}</p>`,
				},
			],
			size: "large",
			primary_action_label: __("Apply Exchange"),
			primary_action: () => {
				if (active_tab === "new" && !exchange_data) {
					// Create assessment then apply
					this._create_and_apply_exchange(dialog);
					return;
				}
				if (!exchange_data) {
					frappe.show_alert({ message: __("No exchange data loaded"), indicator: "red" });
					return;
				}
				dialog.hide();
				this._apply_exchange(exchange_data);
			},
		});

		// Tab switching
		dialog.set_value("__tab", "lookup");
		dialog.$wrapper.on("click", ".ch-exc-tab", function () {
			const tab = $(this).data("tab");
			active_tab = tab;
			exchange_data = null;
			dialog.set_value("__tab", tab);
			dialog.$wrapper.find(".ch-exc-tab").removeClass("btn-primary").addClass("btn-default");
			$(this).removeClass("btn-default").addClass("btn-primary");
			dialog.fields_dict.exchange_details_html.$wrapper.html(
				`<p class="text-muted">${tab === "lookup"
					? __("Search for an assessment above")
					: __("Fill device details, then click Evaluate")}</p>`
			);
			if (tab === "new") {
				dialog.set_df_property("primary_action_label", "label", __("Evaluate"));
				dialog.get_primary_btn().text(__("Evaluate"));
			} else {
				dialog.get_primary_btn().text(__("Apply Exchange"));
			}
		});

		// Live valuation when condition checks change (new device tab)
		const check_fields = ["chk_screen", "chk_body", "chk_buttons", "chk_charging", "chk_camera", "chk_speaker_mic"];
		const run_valuation = frappe.utils.debounce(() => {
			if (active_tab !== "new") return;
			const item_code = dialog.get_value("new_item_code");
			if (!item_code) return;
			const checks = {
				screen: !!dialog.get_value("chk_screen"),
				body: !!dialog.get_value("chk_body"),
				buttons: !!dialog.get_value("chk_buttons"),
				charging: !!dialog.get_value("chk_charging"),
				camera: !!dialog.get_value("chk_camera"),
				speaker_mic: !!dialog.get_value("chk_speaker_mic"),
			};
			frappe.xcall("ch_pos.api.pos_api.calculate_buyback_valuation", {
				item_code, condition_checks: checks,
			}).then((val) => {
				if (!val) return;
				const deductions_html = val.deductions.length
					? val.deductions.map(d =>
						`<tr><td class="text-danger">${frappe.utils.escape_html(d.label)}</td><td class="text-danger">−₹${format_number(d.amount)}</td></tr>`
					).join("")
					: `<tr><td colspan="2" class="text-success">${__("No deductions — perfect condition")}</td></tr>`;
				dialog.fields_dict.exchange_details_html.$wrapper.html(`
					<div class="ch-pos-exchange-preview">
						<table class="table table-sm">
							<tr><td><b>${__("Base Value")}</b></td><td>₹${format_number(val.base_price)}</td></tr>
							${deductions_html}
							<tr style="border-top:2px solid var(--border-color)"><td><b>${__("Exchange Value")}</b></td>
								<td class="text-success" style="font-size:1.2em"><b>₹${format_number(val.final_price)}</b></td></tr>
							<tr><td><b>${__("Grade")}</b></td><td><span class="badge badge-info">${val.grade}</span></td></tr>
						</table>
					</div>`);
			});
		}, 400);

		check_fields.forEach((f) => dialog.fields_dict[f].$input.on("change", run_valuation));
		dialog.fields_dict.new_item_code.$input.on("change", run_valuation);

		// Lookup (existing assessment) handler
		const lookup = () => {
			if (active_tab !== "lookup") return;
			const mode = dialog.get_value("lookup_mode");
			const args = {};
			if (mode === "Assessment ID") args.assessment = dialog.get_value("assessment");
			else if (mode === "IMEI / Serial") args.imei_serial = dialog.get_value("imei_serial");
			else args.mobile_no = dialog.get_value("mobile_no");

			if (!args.assessment && !args.imei_serial && !args.mobile_no) return;

			frappe.call({
				method: "ch_pos.api.pos_api.lookup_exchange",
				args: args,
				callback: (r) => {
					exchange_data = r.message;
					if (r.message) {
						const d = r.message;
						dialog.fields_dict.exchange_details_html.$wrapper.html(`
							<div class="ch-pos-exchange-preview">
								<table class="table table-sm">
									<tr><td><b>${__("Device")}</b></td><td>${frappe.utils.escape_html(d.item_name)} (${frappe.utils.escape_html(d.item_code)})</td></tr>
									<tr><td><b>${__("IMEI/Serial")}</b></td><td>${d.imei_serial || "—"}</td></tr>
									<tr><td><b>${__("Grade")}</b></td><td>${d.condition_grade || "—"}</td></tr>
									<tr><td><b>${__("Buyback Value")}</b></td>
										<td class="text-success"><b>₹${format_number(d.buyback_amount)}</b></td></tr>
									<tr><td><b>${__("Assessment")}</b></td><td>${d.assessment}</td></tr>
									${d.order ? `<tr><td><b>${__("Order")}</b></td><td>${d.order}</td></tr>` : ""}
								</table>
							</div>`);
					} else {
						dialog.fields_dict.exchange_details_html.$wrapper.html(
							`<p class="text-danger">${__("No eligible exchange found")}</p>`
						);
					}
				},
			});
		};

		dialog.fields_dict.assessment.$input.on("change", lookup);
		dialog.fields_dict.imei_serial.$input.on("change", lookup);
		dialog.fields_dict.mobile_no.$input.on("change", lookup);
		dialog.show();
	}

	/** Create a Buyback Assessment from the "Add Old Device" tab and apply exchange */
	_create_and_apply_exchange(dialog) {
		const item_code = dialog.get_value("new_item_code");
		const imei = (dialog.get_value("new_imei") || "").trim();
		const mobile_no = (dialog.get_value("new_mobile") || "").trim();

		if (!item_code) {
			frappe.show_alert({ message: __("Select a device model"), indicator: "orange" });
			return;
		}
		if (!mobile_no) {
			frappe.show_alert({ message: __("Enter customer mobile"), indicator: "orange" });
			return;
		}

		const checks = {
			screen: !!dialog.get_value("chk_screen"),
			body: !!dialog.get_value("chk_body"),
			buttons: !!dialog.get_value("chk_buttons"),
			charging: !!dialog.get_value("chk_charging"),
			camera: !!dialog.get_value("chk_camera"),
			speaker_mic: !!dialog.get_value("chk_speaker_mic"),
		};

		dialog.disable_primary_action();
		frappe.xcall("ch_pos.api.pos_api.create_buyback_assessment_with_grading", {
			mobile_no,
			item_code,
			imei_serial: imei || undefined,
			customer: PosState.customer || undefined,
			condition_checks: checks,
		}).then((result) => {
			if (!result || !result.name) {
				dialog.enable_primary_action();
				frappe.show_alert({ message: __("Failed to create assessment"), indicator: "red" });
				return;
			}
			dialog.hide();
			const item_name = frappe.boot.item_names?.[item_code] || item_code;
			this._apply_exchange({
				assessment: result.name,
				item_code,
				item_name,
				imei_serial: imei,
				condition_grade: result.grade,
				buyback_amount: result.estimated_price,
			});
		}).catch(() => {
			dialog.enable_primary_action();
		});
	}

	_apply_exchange(data) {
		PosState.exchange_assessment = data.assessment;
		PosState.exchange_order = data.order || null;
		PosState.exchange_amount = flt(data.buyback_amount);

		if (data.customer && !PosState.customer) {
			PosState.customer = data.customer;
			EventBus.emit("customer:set", data.customer);
		}

		EventBus.emit("exchange:applied", data);
		EventBus.emit("cart:updated");
		frappe.show_alert({
			message: __("Exchange credit ₹{0} applied", [format_number(data.buyback_amount)]),
			indicator: "green",
		});
	}

	// ── VAS Dialog ──────────────────────────────────────
	_show_vas_dialog() {
		// Pass current cart items for device-dependency enforcement
		const cart_items = PosState.cart.map((c) => ({
			item_code: c.item_code,
			is_warranty: c.is_warranty,
			is_vas: c.is_vas,
		}));
		frappe.xcall("ch_pos.api.pos_api.get_vas_plans_with_rules", {
			cart_items,
		}).then((plans) => {
			if (!plans || !plans.length) {
				frappe.show_alert({ message: __("No VAS plans available"), indicator: "orange" });
				return;
			}
			this._render_vas_selector(plans);
		});
	}

	_render_vas_selector(plans) {
		const device_items = PosState.cart.filter((c) => !c.is_warranty && !c.is_vas);

		// Build plan cards HTML
		const plan_cards = plans.map((p) => {
			const blocked = p.blocked;
			const type_badge = p.plan_type === "Protection Plan"
				? `<span class="badge badge-warning" style="font-size:10px">🛡 Protection</span>`
				: `<span class="badge badge-info" style="font-size:10px">✦ VAS</span>`;
			const brand_badge = p.brand
				? `<span class="badge badge-light" style="font-size:10px">${frappe.utils.escape_html(p.brand)}</span>`
				: "";
			const blocked_html = blocked
				? `<div class="text-danger" style="font-size:11px;margin-top:4px"><i class="fa fa-ban"></i> ${frappe.utils.escape_html(p.blocked_reason)}</div>`
				: "";
			return `<div class="ch-vas-card ${blocked ? 'ch-vas-blocked' : ''}" data-plan="${frappe.utils.escape_html(p.name)}">
				<div class="ch-vas-card-body">
					<div class="ch-vas-card-check"><i class="fa fa-check"></i></div>
					<div style="flex:1;min-width:0">
						<div style="display:flex;justify-content:space-between;align-items:center">
							<div>
								<b>${frappe.utils.escape_html(p.plan_name)}</b>
								<span style="margin-left:6px">${type_badge} ${brand_badge}</span>
							</div>
							<span style="font-weight:700;color:var(--primary)">₹${format_number(p.price)}</span>
						</div>
						<div class="text-muted" style="font-size:12px">${p.duration_months || 0} months${p.coverage_description ? ' — ' + frappe.utils.escape_html(p.coverage_description) : ''}</div>
						${blocked_html}
					</div>
				</div>
			</div>`;
		}).join("");

		const dialog = new frappe.ui.Dialog({
			title: __("Add Value Added Service"),
			fields: [
				{
					fieldname: "plans_html",
					fieldtype: "HTML",
					options: `<div class="ch-vas-plans">${plan_cards}</div>`,
				},
				{
					fieldname: "selected_plan",
					fieldtype: "Data",
					hidden: 1,
				},
				{
					fieldname: "for_item",
					fieldtype: "Select",
					label: __("Apply to Device"),
					options: [
						"",
						...device_items.map((c) => c.item_code + (c.serial_no ? ` (${c.serial_no})` : "")),
						"── Enter IMEI manually ──",
					].join("\n"),
					description: __("Select a device from the cart, or enter IMEI for external / previously sold device"),
				},
				{
					fieldname: "manual_imei",
					fieldtype: "Data",
					label: __("IMEI / Serial Number"),
					depends_on: "eval:doc.for_item === '── Enter IMEI manually ──' || (doc.for_item && !doc.for_item.includes('('))",
					mandatory_depends_on: "eval:doc.for_item === '── Enter IMEI manually ──' || (doc.for_item && !doc.for_item.includes('('))",
					description: __("Enter IMEI or serial number for the device"),
				},
			],
			size: "large",
			primary_action_label: __("Add to Cart"),
			primary_action: (values) => {
				const sel = values.selected_plan;
				if (!sel) {
					frappe.show_alert({ message: __("Select a plan first"), indicator: "orange" });
					return;
				}
				const plan = plans.find((p) => p.name === sel);
				if (!plan || plan.blocked) return;

				const is_manual = values.for_item === "── Enter IMEI manually ──";
				let for_item_code = null;
				let for_serial_no = "";

				if (is_manual) {
					const imei = (values.manual_imei || "").trim();
					if (!imei) {
						frappe.show_alert({ message: __("Enter IMEI / Serial Number"), indicator: "orange" });
						return;
					}
					for_serial_no = imei;
					for_item_code = null; // external device — no item in cart

					// Validate category for manual IMEI if plan has category restriction
					if (plan.applicable_categories && plan.applicable_categories.length) {
						frappe.xcall(
							"ch_item_master.ch_item_master.warranty_api.validate_vas_category",
							{ serial_no: imei, warranty_plan: plan.name }
						).then((res) => {
							if (!res.valid) {
								frappe.show_alert({ message: res.message, indicator: "red" });
								return;
							}
							if (res.item_code) for_item_code = res.item_code;
							this._add_vas_to_cart(dialog, plan, for_item_code, for_serial_no);
						});
						return; // async — don't fall through
					}
				} else {
					const for_raw = values.for_item || "";
					for_item_code = for_raw.split(" (")[0] || null;
					const serial_match = for_raw.match(/\(([^)]+)\)/);
					for_serial_no = serial_match ? serial_match[1] : "";

					// Device in cart has no serial — use manual IMEI input
					if (!for_serial_no) {
						const imei = (values.manual_imei || "").trim();
						if (!imei) {
							frappe.show_alert({ message: __("Enter IMEI / Serial Number for this device"), indicator: "orange" });
							return;
						}
						for_serial_no = imei;
					}
				}

				this._add_vas_to_cart(dialog, plan, for_item_code, for_serial_no);
			},
		});

		// Click to select plan card
		dialog.$wrapper.on("click", ".ch-vas-card:not(.ch-vas-blocked)", function () {
			dialog.$wrapper.find(".ch-vas-card").removeClass("ch-vas-selected");
			$(this).addClass("ch-vas-selected");
			dialog.set_value("selected_plan", $(this).data("plan"));
		});

		dialog.show();
	}

	_add_vas_to_cart(dialog, plan, for_item_code, for_serial_no) {
		// Prevent duplicate: same plan on same device/IMEI
		const dup = PosState.cart.find(
			(c) => c.is_vas && c.warranty_plan === plan.name
				&& c.for_item_code === for_item_code
				&& (c.for_serial_no || "") === for_serial_no
		);
		if (dup) {
			frappe.show_alert({ message: __("This plan is already added for this device"), indicator: "orange" });
			return;
		}

		dialog.hide();
		PosState.cart.push({
			item_code: plan.service_item || plan.name,
			item_name: `✦ ${plan.plan_name}`,
			qty: 1,
			rate: flt(plan.price),
			mrp: flt(plan.price),
			uom: "Nos",
			discount_percentage: 0,
			discount_amount: 0,
			offers: [],
			applied_offer: null,
			warranty_plan: plan.name,
			for_item_code,
			for_serial_no,
			is_warranty: false,
			is_vas: true,
		});
		EventBus.emit("cart:updated");
		frappe.show_alert({ message: __("{0} added", [plan.plan_name]), indicator: "green" });
	}

	// ── Product Exchange (Swap) Dialog ──────────────────
	_show_product_exchange_dialog() {
		const dialog = new frappe.ui.Dialog({
			title: __("Product Exchange / Swap"),
			fields: [
				{
					fieldname: "invoice",
					fieldtype: "Link",
					label: __("Original Invoice"),
					options: "Sales Invoice",
					reqd: 1,
					get_query: () => ({
						filters: { is_return: 0, docstatus: 1, status: ["!=", "Credit Note Issued"] },
					}),
				},
				{
					fieldname: "eligibility_html",
					fieldtype: "HTML",
					options: `<p class="text-muted">${__("Select an invoice to check swap eligibility")}</p>`,
				},
			],
			size: "small",
			primary_action_label: __("Select Items to Return"),
			primary_action: (values) => {
				if (!dialog._swap_eligible) {
					frappe.show_alert({ message: __("Invoice not eligible for swap"), indicator: "red" });
					return;
				}
				dialog.hide();
				EventBus.emit("returns:pick_items", {
					invoice: values.invoice,
					action: "exchange",
				});
			},
		});

		dialog._swap_eligible = false;

		// Check eligibility when invoice changes
		dialog.fields_dict.invoice.$input.on("change", () => {
			const inv = dialog.get_value("invoice");
			if (!inv) {
				dialog._swap_eligible = false;
				dialog.fields_dict.eligibility_html.$wrapper.html(
					`<p class="text-muted">${__("Select an invoice to check swap eligibility")}</p>`
				);
				return;
			}

			dialog.fields_dict.eligibility_html.$wrapper.html(
				`<p class="text-muted"><i class="fa fa-spinner fa-spin"></i> ${__("Checking eligibility...")}</p>`
			);

			frappe.xcall("ch_pos.api.pos_api.validate_swap_eligibility", {
				invoice_name: inv,
			}).then((res) => {
				if (!res) return;
				if (res.eligible) {
					dialog._swap_eligible = true;
					dialog.fields_dict.eligibility_html.$wrapper.html(`
						<div class="alert alert-success" style="margin-top:8px;padding:10px 14px">
							<div style="display:flex;justify-content:space-between;align-items:center">
								<div>
									<i class="fa fa-check-circle"></i> <b>${__("Eligible for Swap")}</b>
								</div>
								<span class="badge badge-success">${res.days_remaining} ${__("days left")}</span>
							</div>
							<div class="text-muted" style="font-size:12px;margin-top:6px">
								${__("Customer")}: <b>${frappe.utils.escape_html(res.customer_name || res.customer)}</b> ·
								${__("Purchased")}: ${frappe.datetime.str_to_user(res.posting_date)} ·
								${__("Total")}: ₹${format_number(res.grand_total)}
							</div>
						</div>`);
				} else {
					dialog._swap_eligible = false;
					dialog.fields_dict.eligibility_html.$wrapper.html(`
						<div class="alert alert-danger" style="margin-top:8px;padding:10px 14px">
							<i class="fa fa-times-circle"></i> <b>${__("Not Eligible")}</b>
							<div class="text-muted" style="font-size:12px;margin-top:4px">
								${frappe.utils.escape_html(res.reason)}
							</div>
						</div>`);
				}
			});
		});

		dialog.show();
	}

	// ── Hold Invoice ────────────────────────────────────
	hold_invoice() {
		if (!PosState.cart.length) return;

		const dlg = new frappe.ui.Dialog({
			title: __("Hold Invoice"),
			fields: [
				{
					fieldname: "hold_note",
					fieldtype: "Data",
					label: __("Note / Customer Name (optional)"),
					placeholder: __("e.g. Waiting for customer, table 3..."),
				},
			],
			size: "small",
			primary_action_label: __("Hold"),
			primary_action: (values) => {
				const note = (values.hold_note || "").trim();
				const key = `ch_pos_held_${Date.now()}`;
				const data = {
					note: note || (PosState.customer || __("Held Bill")),
					customer: PosState.customer,
					cart: JSON.parse(JSON.stringify(PosState.cart)),
					additional_discount_pct: PosState.additional_discount_pct,
					additional_discount_amt: PosState.additional_discount_amt,
					discount_reason: PosState.discount_reason,
					coupon_code: PosState.coupon_code,
					coupon_discount: PosState.coupon_discount,
					voucher_code: PosState.voucher_code,
					voucher_amount: PosState.voucher_amount,
					exchange_assessment: PosState.exchange_assessment,
					exchange_amount: PosState.exchange_amount,
					sale_type: PosState.sale_type,
					// Payment state — persisted across hold/restore
					is_credit_sale: PosState.is_credit_sale || false,
					is_free_sale: PosState.is_free_sale || false,
					free_sale_reason: PosState.free_sale_reason || "",
					free_sale_approved_by: PosState.free_sale_approved_by || "",
					_payment_state: PosState._payment_state || null,
					timestamp: frappe.datetime.now_datetime(),
				};
				localStorage.setItem(key, JSON.stringify(data));
				dlg.hide();
				frappe.show_alert({ message: __("Invoice held: {0}", [data.note]), indicator: "blue" });
				PosState.reset_transaction();
				EventBus.emit("held_bills:updated");
			},
			secondary_action_label: __("Cancel"),
			secondary_action: () => dlg.hide(),
		});
		dlg.show();
		setTimeout(() => dlg.fields_dict.hold_note.$input.focus(), 50);
	}

	// ── Held Bills Dialog ───────────────────────────────
	_show_held_bills_dialog() {
		// Collect all held bills from localStorage
		const bills = [];
		for (let i = 0; i < localStorage.length; i++) {
			const k = localStorage.key(i);
			if (!k.startsWith("ch_pos_held_")) continue;
			try {
				const d = JSON.parse(localStorage.getItem(k));
				bills.push({ key: k, ...d });
			} catch (_) { /* skip corrupt entries */ }
		}

		// Sort by timestamp descending (newest first)
		bills.sort((a, b) => (b.timestamp > a.timestamp ? 1 : -1));

		if (!bills.length) {
			frappe.show_alert({ message: __("No held bills"), indicator: "blue" });
			return;
		}

		const rows_html = bills.map((b, idx) => {
			const items_count = (b.cart || []).length;
			const total = (b.cart || []).reduce((s, c) => s + flt(c.qty) * flt(c.rate), 0);
			const time_str = b.timestamp ? b.timestamp.split(" ")[1]?.substring(0, 5) : "";
			return `
			<div class="ch-held-row" data-idx="${idx}" style="display:flex;align-items:center;gap:10px;padding:10px 12px;border-bottom:1px solid var(--border-color);cursor:pointer;transition:background .15s">
				<div style="flex:1">
					<div style="font-weight:600">${frappe.utils.escape_html(b.note || b.customer || __("Held Bill"))}</div>
					<div class="text-muted" style="font-size:12px">${items_count} ${__("item(s)")} · ₹${format_number(total)} · ${frappe.utils.escape_html(time_str)}</div>
				</div>
				<button class="btn btn-xs btn-success ch-held-retrieve" data-idx="${idx}">
					<i class="fa fa-play-circle"></i> ${__("Retrieve")}
				</button>
				<button class="btn btn-xs btn-default ch-held-discard" data-idx="${idx}" title="${__("Discard")}">
					<i class="fa fa-trash"></i>
				</button>
			</div>`;
		}).join("");

		const dlg = new frappe.ui.Dialog({
			title: __("Held Bills ({0})", [bills.length]),
			fields: [
				{
					fieldname: "bills_html",
					fieldtype: "HTML",
					options: `<div class="ch-held-list" style="max-height:360px;overflow-y:auto;border:1px solid var(--border-color);border-radius:6px">${rows_html}</div>`,
				},
			],
			size: "large",
		});

		dlg.show();

		dlg.$wrapper.on("click", ".ch-held-retrieve", (e) => {
			e.stopPropagation();
			const idx = parseInt($(e.currentTarget).data("idx"));
			const bill = bills[idx];
			if (!bill) return;

			if (PosState.cart.length) {
				frappe.confirm(
					__("Current cart has items. Discard them and load held bill?"),
					() => this._restore_held_bill(bill, dlg)
				);
			} else {
				this._restore_held_bill(bill, dlg);
			}
		});

		dlg.$wrapper.on("click", ".ch-held-discard", (e) => {
			e.stopPropagation();
			const idx = parseInt($(e.currentTarget).data("idx"));
			const bill = bills[idx];
			if (!bill) return;
			frappe.confirm(__("Discard held bill \"{0}\"?", [bill.note || __("Held Bill")]), () => {
				localStorage.removeItem(bill.key);
				bills.splice(idx, 1);
				EventBus.emit("held_bills:updated");
				dlg.hide();
				if (bills.length) this._show_held_bills_dialog();
				else frappe.show_alert({ message: __("All held bills cleared"), indicator: "blue" });
			});
		});

		dlg.$wrapper.on("click", ".ch-held-row", (e) => {
			if ($(e.target).closest("button").length) return;
			const idx = parseInt($(e.currentTarget).data("idx"));
			$(e.currentTarget).closest(".ch-held-list").find(".ch-held-row").css("background", "");
			$(e.currentTarget).css("background", "var(--control-bg)");
		});
	}

	_restore_held_bill(bill, dlg) {
		dlg.hide();
		PosState.reset_transaction();

		// Restore cart items
		PosState.cart = bill.cart || [];
		PosState.customer = bill.customer || null;
		PosState.additional_discount_pct = bill.additional_discount_pct || 0;
		PosState.additional_discount_amt = bill.additional_discount_amt || 0;
		PosState.discount_reason = bill.discount_reason || "";
		PosState.coupon_code = bill.coupon_code || null;
		PosState.coupon_discount = bill.coupon_discount || 0;
		PosState.voucher_code = bill.voucher_code || null;
		PosState.voucher_amount = bill.voucher_amount || 0;
		PosState.exchange_assessment = bill.exchange_assessment || null;
		PosState.exchange_amount = bill.exchange_amount || 0;
		PosState.sale_type = bill.sale_type || null;
		// Restore payment state
		PosState.is_credit_sale = bill.is_credit_sale || false;
		PosState.is_free_sale = bill.is_free_sale || false;
		PosState.free_sale_reason = bill.free_sale_reason || "";
		PosState.free_sale_approved_by = bill.free_sale_approved_by || "";
		PosState._payment_state = bill._payment_state || null;

		// Remove from storage
		localStorage.removeItem(bill.key);
		EventBus.emit("held_bills:updated");

		// Notify UI to refresh
		if (PosState.customer) EventBus.emit("customer:changed", PosState.customer);
		EventBus.emit("cart:updated");
		frappe.show_alert({ message: __("Held bill restored: {0}", [bill.note || __("Bill")]), indicator: "green" });
	}

	// ── Reprint Dialog ──────────────────────────────────
	_show_reprint_dialog() {
		// Use input date; default today
		const today = frappe.datetime.get_today();

		const build_invoice_list = (date, phone, container) => {
			container.html(`<div class="text-center text-muted" style="padding:20px">
				<i class="fa fa-spinner fa-spin"></i> ${__("Loading...")}
			</div>`);
			const args = { pos_profile: PosState.pos_profile };
			if (phone) args.phone = phone;
			else args.date = date;
			frappe.xcall("ch_pos.api.pos_api.get_todays_invoices", args).then((invoices) => {
				if (!invoices || !invoices.length) {
					const msg = phone ? __("No invoices found for this phone number") : __("No invoices for this date");
					container.html(`<div class="text-center text-muted" style="padding:20px">${msg}</div>`);
					return;
				}
				const rows = invoices.map(inv => {
					const is_ret = inv.is_return ? `<span class="badge badge-warning">${__("Return")}</span>` : "";
					const sign = inv.is_return ? "-" : "";
					return `<div class="ch-reprint-row" style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--border-color)">
						<div style="flex:1">
							<div style="font-weight:600">${frappe.utils.escape_html(inv.name)} ${is_ret}</div>
							<div class="text-muted" style="font-size:12px">${frappe.utils.escape_html(inv.customer || "")} · ${sign}₹${format_number(flt(inv.grand_total))} · ${phone ? frappe.utils.escape_html(inv.posting_date || "") + " " : ""}${frappe.utils.escape_html((inv.posting_time || "").substring(0,5))}</div>
							${inv.items_summary ? `<div class="text-muted" style="font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:300px">${frappe.utils.escape_html(inv.items_summary)}</div>` : ""}
						</div>
						<button class="btn btn-xs btn-default ch-reprint-btn" data-name="${frappe.utils.escape_html(inv.name)}">
							<i class="fa fa-print"></i> ${__("Print")}
						</button>
					</div>`;
				}).join("");
				container.html(`<div class="ch-reprint-list" style="max-height:380px;overflow-y:auto;border:1px solid var(--border-color);border-radius:6px">${rows}</div>`);
			}).catch(() => {
				container.html(`<div class="text-danger text-center" style="padding:12px">${__("Failed to load invoices")}</div>`);
			});
		};

		const dlg = new frappe.ui.Dialog({
			title: __("Reprint Invoice"),
			fields: [
				{
					fieldname: "search_by",
					fieldtype: "Select",
					label: __("Search By"),
					options: "Date\nPhone Number",
					default: "Date",
				},
				{
					fieldname: "date",
					fieldtype: "Date",
					label: __("Date"),
					default: today,
					depends_on: "eval:doc.search_by==='Date'",
				},
				{
					fieldname: "phone",
					fieldtype: "Data",
					label: __("Customer Phone"),
					depends_on: "eval:doc.search_by==='Phone Number'",
					description: __("Enter phone number to find invoices"),
				},
				{
					fieldname: "invoices_html",
					fieldtype: "HTML",
					options: `<div class="ch-reprint-container"></div>`,
				},
			],
			size: "large",
		});

		dlg.show();

		// Initial load
		const container = dlg.$wrapper.find(".ch-reprint-container");
		build_invoice_list(today, null, container);

		// Reload when date changes
		dlg.fields_dict.date.$input.on("change", () => {
			const d = dlg.get_value("date");
			if (d) build_invoice_list(d, null, container);
		});

		// Search when phone is entered (on Enter key or blur)
		const phone_input = dlg.fields_dict.phone.$input;
		phone_input.on("keydown", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				const p = dlg.get_value("phone");
				if (p && p.length >= 4) build_invoice_list(null, p, container);
			}
		});
		phone_input.on("blur", () => {
			const p = dlg.get_value("phone");
			if (p && p.length >= 4) build_invoice_list(null, p, container);
		});

		// Clear results when switching search mode
		dlg.fields_dict.search_by.$input.on("change", () => {
			const mode = dlg.get_value("search_by");
			if (mode === "Date") {
				const d = dlg.get_value("date");
				if (d) build_invoice_list(d, null, container);
			} else {
				container.html(`<div class="text-center text-muted" style="padding:20px">${__("Enter a phone number and press Enter")}</div>`);
			}
		});

		// Print button
		dlg.$wrapper.on("click", ".ch-reprint-btn", (e) => {
			const name = $(e.currentTarget).data("name");
			const url = `/printview?doctype=Sales%20Invoice&name=${encodeURIComponent(name)}&format=Custom%20Sales%20Invoice&no_letterhead=1`;
			window.open(url, "_blank");
		});
	}

	// ── Manager Approval Dialog ─────────────────────────

	_show_manager_approval_dialog(opts = {}) {
		/**
		 * Show OTP-based manager approval dialog.
		 * opts: { cart_idx, purpose, reason, on_approved }
		 *   cart_idx  — index in PosState.cart to stamp approval on
		 *   purpose   — OTP purpose string (e.g. "Discount Override")
		 *   reason    — pre-filled reason text
		 *   on_approved — callback after successful verification
		 */
		let otp_sent = false;

		const dlg = new frappe.ui.Dialog({
			title: __("Manager Approval Required"),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "info_html",
					options: `<p class="text-muted">${frappe.utils.escape_html(opts.reason || __("This action requires manager approval."))}</p>`,
				},
				{ fieldtype: "Section Break", label: __("Manager Verification") },
				{
					fieldname: "manager_mobile",
					fieldtype: "Data",
					label: __("Manager Mobile"),
					reqd: 1,
					description: __("Enter the store manager's registered mobile number"),
				},
				{
					fieldname: "send_otp_btn",
					fieldtype: "Button",
					label: __("Send OTP"),
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "otp_code",
					fieldtype: "Data",
					label: __("OTP Code"),
					description: __("Enter the 6-digit code sent to the manager"),
					maxlength: 6,
				},
				{ fieldtype: "Section Break" },
				{
					fieldname: "override_reason",
					fieldtype: "Small Text",
					label: __("Override Reason"),
					reqd: 1,
					default: opts.reason || "",
				},
			],
			size: "small",
			primary_action_label: __("Verify & Approve"),
			primary_action: (values) => {
				if (!otp_sent) {
					frappe.show_alert({ message: __("Please send OTP first"), indicator: "orange" });
					return;
				}
				if (!values.otp_code || values.otp_code.length < 4) {
					frappe.show_alert({ message: __("Enter the OTP code"), indicator: "orange" });
					return;
				}

				dlg.disable_primary_action();

				frappe.xcall("ch_pos.api.pos_api.verify_manager_approval", {
					mobile_no: values.manager_mobile,
					purpose: opts.purpose || "Manager Override",
					otp_code: values.otp_code,
				}).then((result) => {
					if (result && result.valid) {
						// Stamp approval on the cart item
						if (opts.cart_idx !== undefined && PosState.cart[opts.cart_idx]) {
							PosState.cart[opts.cart_idx].manager_approved = true;
							PosState.cart[opts.cart_idx].manager_user = values.manager_mobile;
							PosState.cart[opts.cart_idx].override_reason = values.override_reason;
						}
						frappe.show_alert({ message: __("Manager approval granted"), indicator: "green" });
						EventBus.emit("cart:updated");
						dlg.hide();
						if (opts.on_approved) opts.on_approved(values);
					} else {
						frappe.show_alert({
							message: result.message || __("Invalid OTP"),
							indicator: "red",
						});
						dlg.enable_primary_action();
					}
				}).catch(() => {
					frappe.show_alert({ message: __("Verification failed"), indicator: "red" });
					dlg.enable_primary_action();
				});
			},
		});

		// Bind Send OTP button
		dlg.fields_dict.send_otp_btn.input.onclick = () => {
			const mobile = dlg.get_value("manager_mobile");
			if (!mobile || mobile.length < 10) {
				frappe.show_alert({ message: __("Enter a valid mobile number"), indicator: "orange" });
				return;
			}
			frappe.xcall("ch_pos.api.pos_api.request_manager_approval", {
				mobile_no: mobile,
				purpose: opts.purpose || "Manager Override",
			}).then((r) => {
				if (r && r.sent) {
					otp_sent = true;
					frappe.show_alert({
						message: __("OTP sent to {0}", [r.mobile]),
						indicator: "green",
					});
				}
			}).catch(() => {
				frappe.show_alert({ message: __("Failed to send OTP"), indicator: "red" });
			});
		};

		dlg.show();
	}

	// ── Helpers ─────────────────────────────────────────
	_get_subtotal() {
		return PosState.cart.reduce((sum, item) => {
			return sum + flt(item.qty) * flt(item.rate) - flt(item.discount_amount || 0) * flt(item.qty);
		}, 0);
	}
}
