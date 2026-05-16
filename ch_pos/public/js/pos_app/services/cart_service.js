/**
 * CH POS — Cart Service
 *
 * Business logic for cart operations: add, remove, qty change,
 * best offer application, warranty prompts, VAS, coupon validation,
 * exchange and product-exchange flows, hold invoice.
 */
import { PosState, EventBus } from "../state.js";
import { format_number } from "../shared/helpers.js";
import { print_invoice_pdf } from "../shared/print_helper.js";

export class CartService {
	constructor() {
		this._exception_status_inflight = new Set();
		this._exception_status_seen = {};
		this._exception_status_poll_ms = 8000;
		this._exception_status_timer = null;
		this._bind_events();
		// POS-10 fix: Restore active cart from localStorage on page load
		this._restore_active_cart();
		this._start_exception_status_poll();
	}

	// POS-10 fix: Auto-persist active cart state to localStorage on every change
	_persist_active_cart() {
		try {
			if (!PosState.cart || !PosState.cart.length) {
				localStorage.removeItem("ch_pos_active_cart");
				return;
			}
			const data = {
				customer: PosState.customer,
				cart: JSON.parse(JSON.stringify(PosState.cart)),
				additional_discount_pct: PosState.additional_discount_pct,
				additional_discount_amt: PosState.additional_discount_amt,
				coupon_code: PosState.coupon_code,
				coupon_discount: PosState.coupon_discount,
				voucher_code: PosState.voucher_code,
				voucher_amount: PosState.voucher_amount,
				exchange_assessment: PosState.exchange_assessment,
				exchange_amount: PosState.exchange_amount,
				sale_type: PosState.sale_type,
				is_credit_sale: PosState.is_credit_sale || false,
				is_free_sale: PosState.is_free_sale || false,
				exception_request: PosState.exception_request,
				exception_request_data: PosState.exception_request_data,
				timestamp: frappe.datetime.now_datetime(),
			};
			localStorage.setItem("ch_pos_active_cart", JSON.stringify(data));
		} catch (e) {
			// Storage full or unavailable — non-critical
		}
	}

	_restore_active_cart() {
		try {
			const raw = localStorage.getItem("ch_pos_active_cart");
			if (!raw) return;
			const data = JSON.parse(raw);
			// Only restore if saved within last 12 hours
			if (data.timestamp) {
				const saved = new Date(data.timestamp);
				const hours = (Date.now() - saved.getTime()) / 3600000;
				if (hours > 12) {
					localStorage.removeItem("ch_pos_active_cart");
					return;
				}
			}
			if (data.cart && data.cart.length) {
				PosState.cart = data.cart;
				if (data.customer) PosState.customer = data.customer;
				if (data.additional_discount_pct) PosState.additional_discount_pct = data.additional_discount_pct;
				if (data.additional_discount_amt) PosState.additional_discount_amt = data.additional_discount_amt;
				if (data.coupon_code) PosState.coupon_code = data.coupon_code;
				if (data.coupon_discount) PosState.coupon_discount = data.coupon_discount;
				if (data.voucher_code) PosState.voucher_code = data.voucher_code;
				if (data.voucher_amount) PosState.voucher_amount = data.voucher_amount;
				if (data.exchange_assessment) PosState.exchange_assessment = data.exchange_assessment;
				if (data.exchange_amount) PosState.exchange_amount = data.exchange_amount;
				if (data.sale_type) PosState.sale_type = data.sale_type;
				PosState.is_credit_sale = data.is_credit_sale || false;
				PosState.is_free_sale = data.is_free_sale || false;
				PosState.exception_request = data.exception_request || null;
				PosState.exception_request_data = data.exception_request_data || null;
				if (PosState.exception_request_data) {
					this._apply_exception_pricing_to_cart(PosState.exception_request_data);
				}
				EventBus.emit("cart:updated");
				frappe.show_alert({ message: __("Previous cart restored"), indicator: "blue" });
			}
		} catch (e) {
			localStorage.removeItem("ch_pos_active_cart");
		}
	}

	_bind_events() {
		EventBus.on("cart:add_item", (item_data) => this.add_to_cart(item_data));

		EventBus.on("company:switched", () => {
			if (PosState.cart.length) {
				PosState.reset_transaction();
				localStorage.removeItem("ch_pos_active_cart");
				EventBus.emit("cart:updated");
				frappe.show_alert({ message: __("Cart cleared — company changed"), indicator: "orange" });
			}
		});

		EventBus.on("cart:qty_plus", (idx) => {
			const item = PosState.cart[idx];
			if (item.has_serial_no) {
				frappe.show_alert({
					message: __("Scan another IMEI to add more units of {0}", [item.item_name]),
					indicator: "orange",
				});
				return;
			}
			const next_qty = this._normalize_qty(flt(item.qty) + 1, item.must_be_whole_number);
			if (!this._can_set_qty(item, next_qty)) {
				return;
			}
			item.qty = next_qty;
			this._apply_best_offer(item);
			EventBus.emit("cart:updated");
		});

		EventBus.on("cart:qty_minus", (idx) => {
			const item = PosState.cart[idx];
			if (!item) return;
			if (flt(item.qty) > 1) {
				item.qty = this._normalize_qty(flt(item.qty) - 1, item.must_be_whole_number);
				this._apply_best_offer(item);
			} else {
				PosState.cart.splice(idx, 1);
			}
			EventBus.emit("cart:updated");
		});

		EventBus.on("cart:qty_set", ({ idx, qty }) => this.set_cart_qty(idx, qty));

		EventBus.on("cart:remove", (idx) => {
			PosState.cart.splice(idx, 1);
			EventBus.emit("cart:updated");
		});

		EventBus.on("cart:exception_remove", (idx) => {
			const item = PosState.cart[idx];
			if (!item || !item.exception_request) return;
			this._remove_exception_from_item(item);
			EventBus.emit("cart:updated");
		});

		EventBus.on("cart:exception_unlink_all", () => {
			let changed = false;
			PosState.cart.forEach((item) => {
				if (!item || !item.exception_request) return;
				this._remove_exception_from_item(item);
				changed = true;
			});
			if (changed) EventBus.emit("cart:updated");
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
		EventBus.on("customer:changed", () => {
			if (PosState.exception_request_data) {
				this._apply_exception_pricing_to_cart(PosState.exception_request_data);
				EventBus.emit("cart:updated");
			}
		});
		EventBus.on("customer:info_loaded", () => {
			if (PosState.exception_request_data) {
				this._apply_exception_pricing_to_cart(PosState.exception_request_data);
				EventBus.emit("cart:updated");
			}
		});
		EventBus.on("exception:applied", (payload) => {
			const data = payload && payload.data ? payload.data : PosState.exception_request_data;
			if (!data) return;
			this._apply_exception_pricing_to_cart(data);
			EventBus.emit("cart:updated");
		});

		EventBus.on("state:transaction_reset", () => {
			this._exception_status_seen = {};
		});

		// POS-10 fix: Auto-persist cart to localStorage on every update
		EventBus.on("cart:updated", () => this._persist_active_cart());
		EventBus.on("cart:updated", () => this._sync_pending_exception_statuses());

		// H-11: Combo offer detection — runs after any item is added or qty changed
		EventBus.on("cart:item_added", () => this._check_combo_offers());
		EventBus.on("cart:qty_set", () => this._check_combo_offers());
		EventBus.on("cart:qty_plus", () => this._check_combo_offers());

		EventBus.on("exchange:open", () => this._show_exchange_dialog());
		EventBus.on("vas:open", (opts) => this._show_vas_dialog(opts));
		EventBus.on("product_exchange:open", () => this._show_product_exchange_dialog());
		EventBus.on("manager:request_approval", (opts) => this._show_manager_approval_dialog(opts));

		// Direct serial add: scanned the FIFO-oldest (Sell First) serial from main screen.
		EventBus.on("cart:scan_serial", (item_data) => this._add_to_cart_direct_serial(item_data));
	}

	_start_exception_status_poll() {
		if (this._exception_status_timer) {
			clearInterval(this._exception_status_timer);
		}
		this._exception_status_timer = setInterval(
			() => this._sync_pending_exception_statuses(),
			this._exception_status_poll_ms
		);
	}

	_collect_pending_exception_requests() {
		const names = new Set();
		(PosState.cart || []).forEach((item) => {
			const req = (item.exception_request || "").trim();
			if (!req) return;
			const status = ((item.exception_request_status || "") + "").trim();
			if (status === "Approved" || status === "Auto-Approved" || status === "Rejected" || status === "Expired") return;
			names.add(req);
		});
		return Array.from(names);
	}

	_sync_pending_exception_statuses() {
		if (!PosState.cart || !PosState.cart.length) return;
		const pending_names = this._collect_pending_exception_requests();
		if (!pending_names.length) return;

		pending_names.forEach((exception_name) => {
			if (!exception_name || this._exception_status_inflight.has(exception_name)) return;
			this._exception_status_inflight.add(exception_name);
			frappe.xcall(
				"ch_item_master.ch_item_master.exception_api.check_exception_valid",
				{ exception_name }
			).then((resp) => {
				this._apply_exception_status_to_cart(exception_name, resp || null);
			}).catch(() => {
				// Ignore transient API/network errors; next poll will retry.
			}).finally(() => {
				this._exception_status_inflight.delete(exception_name);
			});
		});
	}

	_apply_exception_status_to_cart(exception_name, data) {
		if (!exception_name || !data) return;
		const status = (data.status || "Pending").trim();
		let changed = false;
		let just_approved = false;

		PosState.cart.forEach((item) => {
			if ((item.exception_request || "") !== exception_name) return;
			const prev_status = ((item.exception_request_status || "") + "").trim() || "Pending";
			if (prev_status !== status) {
				item.exception_request_status = status;
				changed = true;
				if ((status === "Approved" || status === "Auto-Approved") && (prev_status !== "Approved" && prev_status !== "Auto-Approved")) {
					just_approved = true;
				}
			}

			if ((status === "Approved" || status === "Auto-Approved") && data.valid) {
				item.exception_request_data = data;
				this._apply_exception_pricing_to_item(item, data);
				changed = true;
			}
		});

		if (status === "Approved" || status === "Auto-Approved") {
			PosState.exception_request = exception_name;
			PosState.exception_request_data = data;
		}

		if (!changed) return;

		EventBus.emit("cart:updated");
		if ((status === "Approved" || status === "Auto-Approved") && data.valid) {
			EventBus.emit("exception:applied", { name: exception_name, data });
		}

		if (just_approved && this._exception_status_seen[exception_name] !== status) {
			this._exception_status_seen[exception_name] = status;
			frappe.show_alert({
				message: __("Exception {0} approved and applied to cart item", [exception_name]),
				indicator: "green",
			});
		}
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
		// Block items with no selling price (unless allow_zero_rate is set)
		if (!flt(item_data.selling_price) && !flt(item_data.mrp) && !cint(item_data.ch_allow_zero_rate)) {
			frappe.show_alert({ message: __("Item {0} has no selling price. Cannot add to cart.", [item_data.item_name]), indicator: "red" });
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
					frappe.show_alert({
						message: __("FIFO restricted. Sell oldest serial first: {0}", [res.oldest_serial || "-"]),
						indicator: "red",
					});
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

		// Block items with no selling price (unless allow_zero_rate is set)
		if (!flt(item_data.selling_price) && !flt(item_data.mrp) && !cint(item_data.ch_allow_zero_rate)) {
			frappe.show_alert({
				message: __("Item {0} has no selling price. Cannot add to cart.", [item_data.item_name]),
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
			const next_qty = this._normalize_qty(flt(existing.qty) + 1, existing.must_be_whole_number);
			if (!this._can_set_qty(existing, next_qty)) {
				return;
			}
			existing.qty = next_qty;
			this._apply_best_offer(existing);
			EventBus.emit("cart:updated");
		} else {
			this._add_new_cart_item(item_data);
		}
	}

	set_cart_qty(idx, qty) {
		const item = PosState.cart[idx];
		if (!item || item.has_serial_no || item.is_warranty || item.is_vas) return;

		const next_qty = this._normalize_qty(qty, item.must_be_whole_number);
		if (!(next_qty > 0)) {
			frappe.show_alert({ message: __("Quantity must be greater than zero"), indicator: "orange" });
			return;
		}
		if (!this._can_set_qty(item, next_qty)) {
			return;
		}

		item.qty = next_qty;
		this._apply_best_offer(item);
		EventBus.emit("cart:updated");
	}

	_normalize_qty(qty, must_be_whole_number) {
		const value = flt(qty);
		if (!(value > 0)) return 0;
		if (cint(must_be_whole_number)) {
			return Math.max(1, Math.round(value));
		}
		return Math.max(0.001, Math.round(value * 1000) / 1000);
	}

	_can_set_qty(item, qty) {
		const stock_qty = flt(item.stock_qty);
		if (!item.has_serial_no && !item.is_warranty && !item.is_vas && stock_qty > 0 && qty > stock_qty) {
			frappe.show_alert({
				message: __("Only {0} {1} available for {2}", [format_number(stock_qty), item.uom || __("units"), item.item_name]),
				indicator: "orange",
			});
			return false;
		}
		return true;
	}

	_add_new_cart_item(item_data, serial_no) {
		const price_list_rate = flt(item_data.price_list_rate || item_data.mrp || item_data.selling_price || 0);
		const selling_rate = flt(item_data.selling_price || price_list_rate || 0);
		const commercial_discount_amount = Math.max(0, price_list_rate - selling_rate);
		const commercial_discount_percentage = price_list_rate > 0 && commercial_discount_amount > 0
			? flt(commercial_discount_amount / price_list_rate * 100)
			: 0;
		const cart_item = {
			item_code: item_data.item_code,
			item_name: item_data.item_name,
			qty: 1,
			rate: selling_rate,
			price_list_rate,
			mrp: item_data.mrp || 0,
			uom: item_data.stock_uom || "Nos",
			discount_percentage: commercial_discount_percentage,
			discount_amount: commercial_discount_amount,
			offers: item_data.offers || [],
			applied_offer: null,
			warranty_plan: null,
			is_warranty: false,
			is_vas: false,
			has_serial_no: cint(item_data.has_serial_no),
			serial_no: serial_no || "",
			ch_item_type: item_data.ch_item_type || "",
			ch_allow_zero_rate: cint(item_data.ch_allow_zero_rate),
			stock_qty: flt(item_data.stock_qty || 0),
			must_be_whole_number: cint(item_data.must_be_whole_number),
		};
		if (!cart_item.discount_percentage && flt(item_data.discount_percentage) > 0) {
			cart_item.discount_percentage = flt(item_data.discount_percentage);
		}
		if (!cart_item.discount_amount && flt(item_data.discount_amount) > 0) {
			cart_item.discount_amount = flt(item_data.discount_amount);
		}
		this._apply_best_offer(cart_item);
		this._apply_exception_pricing_to_item(cart_item, PosState.exception_request_data);
		PosState.cart.push(cart_item);
		EventBus.emit("cart:updated");
		EventBus.emit("cart:item_added", { item_data, cart_item });
		this._prompt_warranty(item_data, cart_item);
		this._prompt_bundle_items(item_data);
	}

	/** Show a popup to add free bundle items (accessories) when a main device is added */
	_prompt_bundle_items(item_data) {
		frappe.xcall("ch_pos.api.pos_api.get_bundle_items", {
			item_code: item_data.item_code,
			warehouse: PosState.warehouse,
		}).then((items) => {
			if (!items || !items.length) return;

			const fields = [
				{
					fieldtype: "HTML",
					fieldname: "bundle_info",
					options: `<p class="text-muted" style="margin-bottom:12px">
						${__("The following accessories are available with {0}. Select items to add for free.", [frappe.utils.escape_html(item_data.item_name)])}
					</p>`,
				},
			];

			items.forEach((bi, i) => {
				const in_stock = flt(bi.stock_qty) > 0;
				fields.push({
					fieldname: `bundle_item_${i}`,
					fieldtype: "Check",
					label: `${bi.item_name} ${in_stock ? "" : "(" + __("Out of stock") + ")"}`,
					default: in_stock ? 1 : 0,
					read_only: !in_stock ? 1 : 0,
					description: bi.selling_price ? `₹${bi.selling_price} → ₹0 (Free)` : __("Free"),
				});
			});

			const dlg = new frappe.ui.Dialog({
				title: __("Free Accessories — {0}", [item_data.item_name]),
				fields: fields,
				size: "small",
				primary_action_label: __("Add Selected"),
				primary_action: (values) => {
					items.forEach((bi, i) => {
						if (values[`bundle_item_${i}`]) {
							const free_item = {
								...bi,
								selling_price: 0,
								mrp: bi.mrp || 0,
								ch_allow_zero_rate: 1,
								is_free_bundle_item: 1,
							};
							this._add_new_cart_item(free_item);
							// Set rate to 0 for the just-added free item
							const last = PosState.cart[PosState.cart.length - 1];
							if (last && last.item_code === bi.item_code) {
								last.rate = 0;
								last.is_free_bundle_item = true;
								last.bundle_parent = item_data.item_code;
							}
						}
					});
					EventBus.emit("cart:updated");
					dlg.hide();
				},
				secondary_action_label: __("Skip"),
				secondary_action: () => dlg.hide(),
			});
			dlg.show();
		});
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
							dlg.enable_primary_action();
							frappe.show_alert({
								message: __("FIFO restricted. Please select oldest serial: {0}", [res.oldest_serial || "-"]),
								indicator: "red",
							});
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
		if (cart_item.exception_request || cart_item.exception_request_data) {
			this._apply_exception_pricing_to_item(cart_item, cart_item.exception_request_data || PosState.exception_request_data);
			return;
		}
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

	_apply_exception_pricing_to_item(cart_item, exception_data) {
		const data = exception_data || PosState.exception_request_data;
		if (!cart_item || !data) return;
		if (!data.item_code || cart_item.item_code !== data.item_code) return;
		const customer_name_confirmed = data.customer && PosState.customer && data.customer === PosState.customer;
		if (data.customer && PosState.customer && data.customer !== PosState.customer) return;
		// Phone guard is a fallback identity check for when customer name is inconclusive
		// (one side null/empty). Skip it when the customer name already confirmed a match
		// to avoid a race condition where PosState.customer_info hasn't loaded yet.
		if (data.customer_phone && !customer_name_confirmed) {
			const current_phone = ((PosState.customer_info?.mobile_no || PosState.customer_info?.mobile || PosState.customer_info?.customer_phone || "") + "")
				.replace(/\D/g, "")
				.slice(-10);
			const allowed_phone = ((data.customer_phone || "") + "").replace(/\D/g, "").slice(-10);
			if (!current_phone || !allowed_phone || current_phone !== allowed_phone) return;
		}
		if (data.serial_no) {
			const current_serial = (cart_item.serial_no || "").trim();
			if (!current_serial || current_serial !== (data.serial_no || "").trim()) return;
		}

		const original_rate = flt(data.original_value || cart_item.price_list_rate || cart_item.mrp || cart_item.rate || 0);
		const resolved_rate = flt(data.resolution_value || data.requested_value || cart_item.rate || 0);
		if (original_rate <= 0 || resolved_rate < 0) return;
		if (cart_item.pre_exception_rate == null) {
			cart_item.pre_exception_rate = flt(cart_item.rate || 0);
		}
		if (cart_item.pre_exception_price_list_rate == null) {
			cart_item.pre_exception_price_list_rate = flt(cart_item.price_list_rate || cart_item.mrp || cart_item.rate || 0);
		}
		if (cart_item.pre_exception_discount_amount == null) {
			cart_item.pre_exception_discount_amount = flt(cart_item.discount_amount || 0);
		}
		if (cart_item.pre_exception_discount_percentage == null) {
			cart_item.pre_exception_discount_percentage = flt(cart_item.discount_percentage || 0);
		}

		cart_item.exception_request = data.name || PosState.exception_request || null;
		cart_item.exception_request_status = data.status || cart_item.exception_request_status || "Pending";
		cart_item.exception_request_data = data;
		cart_item.exception_original_rate = original_rate;
		cart_item.exception_final_rate = resolved_rate;
		cart_item.price_list_rate = original_rate;
		cart_item.rate = resolved_rate;
		cart_item.discount_amount = Math.max(0, original_rate - resolved_rate);
		cart_item.discount_percentage = original_rate > 0 ? flt(cart_item.discount_amount / original_rate * 100) : 0;
		cart_item.applied_offer = null;
		cart_item.offers = [];
	}

	_apply_exception_pricing_to_cart(exception_data) {
		const data = exception_data || PosState.exception_request_data;
		if (!data) return;
		PosState.exception_request_data = data;
		PosState.cart.forEach((cart_item) => this._apply_exception_pricing_to_item(cart_item, data));
	}

	_remove_exception_from_item(cart_item) {
		if (!cart_item) return;
		if (cart_item.pre_exception_price_list_rate != null) {
			cart_item.price_list_rate = flt(cart_item.pre_exception_price_list_rate);
		}
		if (cart_item.pre_exception_rate != null) {
			cart_item.rate = flt(cart_item.pre_exception_rate);
		}
		if (cart_item.pre_exception_discount_amount != null) {
			cart_item.discount_amount = flt(cart_item.pre_exception_discount_amount);
		}
		if (cart_item.pre_exception_discount_percentage != null) {
			cart_item.discount_percentage = flt(cart_item.pre_exception_discount_percentage);
		}

		delete cart_item.exception_request;
		delete cart_item.exception_request_status;
		delete cart_item.exception_request_data;
		delete cart_item.exception_original_rate;
		delete cart_item.exception_final_rate;
		delete cart_item.pre_exception_rate;
		delete cart_item.pre_exception_price_list_rate;
		delete cart_item.pre_exception_discount_amount;
		delete cart_item.pre_exception_discount_percentage;

		const remaining_exception = (PosState.cart || []).find((row) => !!row.exception_request);
		if (!remaining_exception) {
			PosState.exception_request = null;
			PosState.exception_request_data = null;
		}
	}

	// ── Combo Offer Detection (H-11) ────────────────────
	_check_combo_offers() {
		if (!PosState.cart.length) {
			EventBus.emit("combo_offers:detected", []);
			return;
		}
		const cart_items = PosState.cart.map((i) => ({
			item_code: i.item_code,
			qty: i.qty,
			rate: i.rate,
			amount: flt(i.qty * i.rate),
		}));
		frappe.call({
			method: "ch_pos.api.offers.check_combo_offers",
			args: {
				cart_items: JSON.stringify(cart_items),
				company: PosState.company,
			},
			callback: (r) => {
				const combos = r.message || [];
				EventBus.emit("combo_offers:detected", combos);
				if (combos.length) {
					combos.forEach((combo) => {
						frappe.show_alert({
							message: __("Combo offer available: {0} — Save ₹{1}", [
								combo.offer_title,
								format_number(combo.savings, null, 0),
							]),
							indicator: "green",
						});
					});
				}
			},
		});
	}

	// ── Warranty Prompt ─────────────────────────────────
	_prompt_warranty(item_data, cart_item) {
		frappe.call({
			method: "ch_pos.api.attach_api.get_attach_offers",
			args: {
				item_code: item_data.item_code,
				pos_profile: PosState.pos_profile,
			},
			callback: (r) => {
				const data = r.message || {};
				const plans = data.warranty_plans || [];
				const rules = data.attach_rules || [];
				if (!plans.length && !rules.length) return;
				this._show_attach_panel(plans, rules, item_data, cart_item);
			},
		});
	}

	_show_attach_panel(plans, rules, item_data, cart_item) {
		const vas_rules = rules.filter(r => r.attach_type === "VAS");
		const acc_rules = rules.filter(r => r.attach_type === "Accessory");
		const item_name = frappe.utils.escape_html(cart_item.item_name);

		// Build sections
		let sections_html = `<p class="text-muted" style="margin-bottom:12px">${__("Offers for")} <b>${item_name}</b></p>`;

		// Warranty Plans
		if (plans.length) {
			sections_html += `<div class="ch-attach-section">
				<h6 style="margin:0 0 8px 0;font-weight:600">🛡 ${__("Warranty Plans")}</h6>`;
			plans.forEach((p, i) => {
				const label = frappe.utils.escape_html(`${p.plan_name} (${p.duration_months}m) — ₹${format_number(p.price)}`);
				sections_html += `<div class="ch-attach-row" data-type="Warranty" data-idx="${i}">
					<span class="ch-attach-name">${label}</span>
					<span class="ch-attach-actions">
						<button class="btn btn-xs btn-success ch-attach-accept" data-type="Warranty" data-idx="${i}">${__("Add")}</button>
						<button class="btn btn-xs btn-default ch-attach-skip" data-type="Warranty" data-idx="${i}">${__("Skip")}</button>
					</span>
				</div>`;
			});
			sections_html += `</div>`;
		}

		// VAS Rules
		if (vas_rules.length) {
			sections_html += `<div class="ch-attach-section">
				<h6 style="margin:0 0 8px 0;font-weight:600">✨ ${__("Value Added Services")}</h6>`;
			vas_rules.forEach((r, i) => {
				(r.attach_items || []).forEach((ai, j) => {
					const name = frappe.utils.escape_html(ai.item_name || ai.item_code);
					const mandatory = ai.is_mandatory_offer ? `<span class="badge badge-warning" style="font-size:10px;margin-left:4px">${__("Recommended")}</span>` : "";
					sections_html += `<div class="ch-attach-row" data-type="VAS" data-rule="${frappe.utils.escape_html(r.name)}" data-item="${frappe.utils.escape_html(ai.item_code)}">
						<span class="ch-attach-name">${name}${mandatory}</span>
						<span class="ch-attach-actions">
							<button class="btn btn-xs btn-success ch-attach-accept" data-type="VAS" data-rule="${frappe.utils.escape_html(r.name)}" data-item="${frappe.utils.escape_html(ai.item_code)}">${__("Add")}</button>
							<button class="btn btn-xs btn-default ch-attach-skip" data-type="VAS" data-rule="${frappe.utils.escape_html(r.name)}" data-item="${frappe.utils.escape_html(ai.item_code)}" ${r.skip_reason_required ? 'data-reason-required="1"' : ""}>${__("Skip")}</button>
						</span>
					</div>`;
				});
			});
			sections_html += `</div>`;
		}

		// Accessory Rules
		if (acc_rules.length) {
			sections_html += `<div class="ch-attach-section">
				<h6 style="margin:0 0 8px 0;font-weight:600">🔌 ${__("Accessories")}</h6>`;
			acc_rules.forEach((r, i) => {
				(r.attach_items || []).forEach((ai, j) => {
					const name = frappe.utils.escape_html(ai.item_name || ai.item_code);
					const mandatory = ai.is_mandatory_offer ? `<span class="badge badge-warning" style="font-size:10px;margin-left:4px">${__("Recommended")}</span>` : "";
					sections_html += `<div class="ch-attach-row" data-type="Accessory" data-rule="${frappe.utils.escape_html(r.name)}" data-item="${frappe.utils.escape_html(ai.item_code)}">
						<span class="ch-attach-name">${name}${mandatory}</span>
						<span class="ch-attach-actions">
							<button class="btn btn-xs btn-success ch-attach-accept" data-type="Accessory" data-rule="${frappe.utils.escape_html(r.name)}" data-item="${frappe.utils.escape_html(ai.item_code)}">${__("Add")}</button>
							<button class="btn btn-xs btn-default ch-attach-skip" data-type="Accessory" data-rule="${frappe.utils.escape_html(r.name)}" data-item="${frappe.utils.escape_html(ai.item_code)}" ${r.skip_reason_required ? 'data-reason-required="1"' : ""}>${__("Skip")}</button>
						</span>
					</div>`;
				});
			});
			sections_html += `</div>`;
		}

		const dialog = new frappe.ui.Dialog({
			title: __("Attach Offers — {0}", [item_name]),
			fields: [
				{ fieldtype: "HTML", fieldname: "attach_panel", options: `<div class="ch-attach-panel">${sections_html}</div>` },
			],
			size: "large",
			primary_action_label: __("Done"),
			primary_action: () => dialog.hide(),
		});

		// Bind accept/skip events
		dialog.$wrapper.on("click", ".ch-attach-accept", (e) => {
			const $btn = $(e.currentTarget);
			const type = $btn.data("type");
			const $row = $btn.closest(".ch-attach-row");

			if (type === "Warranty") {
				const idx = $btn.data("idx");
				const plan = plans[idx];
				if (!plan) return;

				// Duplicate check
				const dup = PosState.cart.find(
					(c) => c.is_warranty && c.warranty_plan === plan.name
						&& c.for_item_code === cart_item.item_code
						&& (c.for_serial_no || "") === (cart_item.serial_no || "")
				);
				if (dup) {
					frappe.show_alert({ message: __("Already added"), indicator: "orange" });
					return;
				}

				PosState.cart.push({
					item_code: plan.service_item || plan.name,
					item_name: `🛡 ${plan.plan_name} (${plan.duration_months}m)`,
					qty: 1, rate: flt(plan.price), mrp: flt(plan.price),
					uom: "Nos", discount_percentage: 0, discount_amount: 0,
					offers: [], applied_offer: null, warranty_plan: plan.name,
					for_item_code: cart_item.item_code, for_serial_no: cart_item.serial_no || "",
					is_warranty: true, is_vas: false,
				});
				EventBus.emit("cart:updated");
				frappe.show_alert({ message: __("{0} added", [plan.plan_name]), indicator: "green" });
				$row.addClass("ch-attach-done");
				this._log_attach("Warranty", "Accepted", item_data.item_code, plan.name);
			} else {
				// VAS or Accessory — add item to cart
				const attach_item_code = $btn.data("item");
				const rule_name = $btn.data("rule");
				frappe.xcall("ch_pos.api.pos_api.get_item_details_for_pos", {
					item_code: attach_item_code, pos_profile: PosState.pos_profile,
				}).then((item_det) => {
					if (item_det) {
						this._add_new_cart_item({
							...item_det, item_code: attach_item_code,
							is_warranty: false, is_vas: type === "VAS",
						});
					}
					$row.addClass("ch-attach-done");
					this._log_attach(type, "Accepted", item_data.item_code, attach_item_code);
				});
			}
		});

		dialog.$wrapper.on("click", ".ch-attach-skip", (e) => {
			const $btn = $(e.currentTarget);
			const type = $btn.data("type");
			const $row = $btn.closest(".ch-attach-row");
			const reason_required = $btn.data("reason-required");
			const plan_code = type === "Warranty"
				? (plans[$btn.data("idx")] || {}).name
				: $btn.data("item");

			if (reason_required) {
				frappe.prompt(
					{ fieldname: "reason", fieldtype: "Small Text", label: __("Skip Reason"), reqd: 1 },
					(values) => {
						$row.addClass("ch-attach-skipped");
						this._log_attach(type, "Skipped", item_data.item_code, plan_code, values.reason);
					},
					__("Reason for Skipping"),
					__("Submit")
				);
			} else {
				$row.addClass("ch-attach-skipped");
				this._log_attach(type, "Skipped", item_data.item_code, plan_code);
			}
		});

		dialog.show();

		// Also log all offers as "Offered"
		plans.forEach(p => this._log_attach("Warranty", "Offered", item_data.item_code, p.name));
		[...vas_rules, ...acc_rules].forEach(r => {
			(r.attach_items || []).forEach(ai => {
				this._log_attach(r.attach_type, "Offered", item_data.item_code, ai.item_code);
			});
		});
	}

	_log_attach(attach_type, action, item_code, plan_code, skip_reason) {
		frappe.xcall("ch_pos.api.attach_api.log_attach_event", {
			pos_profile: PosState.pos_profile,
			item_code: item_code,
			attach_type: attach_type,
			action: action,
			plan_code: plan_code || "",
			skip_reason: skip_reason || "",
		}).catch(() => {});  // Non-blocking
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
		const dialog = new frappe.ui.Dialog({
			title: __("Buyback Exchange"),
			fields: [
				{
					fieldname: "lookup_mode",
					fieldtype: "Select",
					label: __("Find by"),
					options: "Assessment ID\nIMEI / Serial\nCustomer Mobile",
					default: "Assessment ID",
				},
				{
					fieldname: "assessment",
					fieldtype: "Link",
					label: __("Buyback Assessment"),
					options: "Buyback Assessment",
					depends_on: "eval:doc.lookup_mode === 'Assessment ID'",
					get_query: () => ({ filters: { status: ["in", ["Submitted", "Inspection Created"]] } }),
				},
				{
					fieldname: "imei_serial",
					fieldtype: "Data",
					label: __("IMEI / Serial No"),
					depends_on: "eval:doc.lookup_mode === 'IMEI / Serial'",
				},
				{
					fieldname: "mobile_no",
					fieldtype: "Data",
					label: __("Customer Mobile"),
					depends_on: "eval:doc.lookup_mode === 'Customer Mobile'",
				},
				{ fieldtype: "Section Break", label: __("Exchange Details") },
				{
					fieldname: "exchange_details_html",
					fieldtype: "HTML",
					options: `<p class="text-muted">${__("Search for an assessment above")}</p>`,
				},
			],
			primary_action_label: __("Apply Exchange"),
			primary_action: () => {
				if (!exchange_data) {
					frappe.show_alert({ message: __("No exchange data loaded"), indicator: "red" });
					return;
				}
				dialog.hide();
				this._apply_exchange(exchange_data);
			},
		});

		// Lookup handler
		const lookup = () => {
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
	_show_vas_dialog(opts = {}) {
		// Pass current cart items for device-dependency enforcement
		const cart_items = PosState.cart.map((c) => ({
			item_code: c.item_code,
			is_warranty: c.is_warranty,
			is_vas: c.is_vas,
		}));
		const selected_device = [opts.for_item, PosState.last_vas_target]
			.find((item) => item && !item.is_warranty && !item.is_vas) || null;
		frappe.xcall("ch_pos.api.pos_api.get_vas_plans_with_rules", {
			cart_items,
		}).then((plans) => {
			if (!plans || !plans.length) {
				frappe.show_alert({ message: __("No VAS plans available"), indicator: "orange" });
				return;
			}
			this._render_vas_selector(plans, selected_device);
			PosState.last_vas_target = null;
		});
	}

	_render_vas_selector(plans, selected_device = null) {
		const device_items = PosState.cart.filter((c) => !c.is_warranty && !c.is_vas);
		const selected_device_value = selected_device
			? selected_device.item_code + (selected_device.serial_no ? ` (${selected_device.serial_no})` : "")
			: "";
		const default_manual_imei = selected_device && selected_device.serial_no
			? selected_device.serial_no
			: "";
		const device_options = device_items
			.map((c) => c.item_code + (c.serial_no ? ` (${c.serial_no})` : ""))
			.filter((value, index, list) => value && list.indexOf(value) === index);
		const prioritized_device_options = selected_device_value
			? [selected_device_value, ...device_options.filter((value) => value !== selected_device_value)]
			: device_options;
		const for_item_options = prioritized_device_options.length
			? [...prioritized_device_options, "── Enter IMEI manually ──"]
			: ["", "── Enter IMEI manually ──"];

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
					default: selected_device_value,
					options: for_item_options.join("\n"),
					description: __("Select a device from the cart, or enter IMEI for external / previously sold device"),
				},
				{
					fieldname: "manual_imei",
					fieldtype: "Data",
					label: __("IMEI / Serial Number"),
					default: default_manual_imei,
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
		const applySelectedDevice = () => {
			if (dialog.fields_dict.for_item) {
				const select = dialog.fields_dict.for_item.$input && dialog.fields_dict.for_item.$input.get(0);
				const fallback_value = select
					? Array.from(select.options).map((option) => option.value).find((value) => value && value !== "── Enter IMEI manually ──")
					: "";
				const target_value = selected_device_value || fallback_value;
				if (target_value) {
					dialog.fields_dict.for_item.set_value(target_value);
					if (select) {
						select.value = target_value;
						select.dispatchEvent(new Event("change", { bubbles: true }));
					}
					dialog.fields_dict.for_item.$input.trigger("change");
				}
			}
			if (default_manual_imei && dialog.fields_dict.manual_imei) {
				dialog.fields_dict.manual_imei.set_value(default_manual_imei);
			}
		};
		applySelectedDevice();
		setTimeout(applySelectedDevice, 100);
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
			price_list_rate: flt(plan.price),
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

		const build_invoice_list = (date, phone, invoice_no, container) => {
			container.html(`<div class="text-center text-muted" style="padding:20px">
				<i class="fa fa-spinner fa-spin"></i> ${__("Loading...")}
			</div>`);
			const args = { pos_profile: PosState.pos_profile };
			if (invoice_no) args.invoice_no = invoice_no;
			else if (phone) args.phone = phone;
			else args.date = date;
			frappe.xcall("ch_pos.api.pos_api.get_todays_invoices", args).then((invoices) => {
				if (!invoices || !invoices.length) {
					const msg = invoice_no
						? __("No invoices found for this invoice number")
						: (phone ? __("No invoices found for this phone number") : __("No invoices for this date"));
					container.html(`<div class="text-center text-muted" style="padding:20px">${msg}</div>`);
					return;
				}
				const rows = invoices.map(inv => {
					const is_ret = inv.is_return ? `<span class="badge badge-warning">${__("Return")}</span>` : "";
					const sign = inv.is_return ? "-" : "";
					const sale_type = frappe.utils.escape_html(inv.custom_ch_sale_type || "");
					const mop = frappe.utils.escape_html(inv.mode_of_payment || "");
					const customer = frappe.utils.escape_html(inv.customer_name || inv.customer || "");
					return `<div class="ch-reprint-row" style="display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--border-color)">
						<div style="flex:1">
							<div style="font-weight:600">${frappe.utils.escape_html(inv.name)} ${is_ret}</div>
							<div class="text-muted" style="font-size:12px">${customer} · ${sign}₹${format_number(flt(inv.grand_total))} · ${phone || invoice_no ? frappe.utils.escape_html(inv.posting_date || "") + " " : ""}${frappe.utils.escape_html((inv.posting_time || "").substring(0,5))}</div>
							<div class="text-muted" style="font-size:11px">${__("Sale Type")}: ${sale_type || "-"} · ${__("MOP")}: ${mop || "-"}</div>
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
					options: "Date\nPhone Number\nInvoice Number",
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
					fieldname: "invoice_no",
					fieldtype: "Data",
					label: __("Invoice Number"),
					depends_on: "eval:doc.search_by==='Invoice Number'",
					description: __("Enter full or partial invoice number"),
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
		build_invoice_list(today, null, null, container);

		// Reload when date changes
		dlg.fields_dict.date.$input.on("change", () => {
			const d = dlg.get_value("date");
			if (d) build_invoice_list(d, null, null, container);
		});

		// Search when phone is entered (on Enter key or blur)
		const phone_input = dlg.fields_dict.phone.$input;
		phone_input.on("keydown", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				const p = dlg.get_value("phone");
				if (p && p.length >= 4) build_invoice_list(null, p, null, container);
			}
		});
		phone_input.on("blur", () => {
			const p = dlg.get_value("phone");
			if (p && p.length >= 4) build_invoice_list(null, p, null, container);
		});

		const invoice_input = dlg.fields_dict.invoice_no.$input;
		invoice_input.on("keydown", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				const inv = (dlg.get_value("invoice_no") || "").trim();
				if (inv) build_invoice_list(null, null, inv, container);
			}
		});
		invoice_input.on("blur", () => {
			const inv = (dlg.get_value("invoice_no") || "").trim();
			if (inv) build_invoice_list(null, null, inv, container);
		});

		// Clear results when switching search mode
		dlg.fields_dict.search_by.$input.on("change", () => {
			const mode = dlg.get_value("search_by");
			if (mode === "Date") {
				const d = dlg.get_value("date");
				if (d) build_invoice_list(d, null, null, container);
			} else if (mode === "Invoice Number") {
				container.html(`<div class="text-center text-muted" style="padding:20px">${__("Enter invoice number and press Enter")}</div>`);
			} else {
				container.html(`<div class="text-center text-muted" style="padding:20px">${__("Enter a phone number and press Enter")}</div>`);
			}
		});

		// Print button — server-rendered PDF (header on every page)
		dlg.$wrapper.on("click", ".ch-reprint-btn", (e) => {
			const name = $(e.currentTarget).data("name");
			const is_gofix = $(e.currentTarget).data("gofix");
			const fmt = is_gofix ? "GoFix Service Invoice" : "Custom Sales Invoice";
			print_invoice_pdf(name, fmt);
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
