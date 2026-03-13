/**
 * CH POS — Central State & Event Bus
 *
 * Single source of truth for POS application state.
 * All modules read from and write to this state object.
 * The EventBus enables decoupled communication between modules.
 */

export const PosState = {
	// ── Profile & Config ────────────────────────────────
	pos_profile: null,
	company: null,
	warehouse: null,
	price_list: null,
	payment_modes: [],
	store_caps: {},
	pos_ext: {},

	// ── Active Mode ─────────────────────────────────────
	active_mode: "sell",

	// ── Cart ────────────────────────────────────────────
	cart: [],
	customer: null,
	customer_info: null, // { customer_type, price_list, credit_limit, outstanding, loyalty, ... }

	// ── Sale Type ───────────────────────────────────────
	sale_type: null,
	sale_sub_type: null,
	sale_reference: null,

	// ── Discount State ──────────────────────────────────
	additional_discount_pct: 0,
	additional_discount_amt: 0,
	discount_reason: "",
	coupon_code: null,
	coupon_discount: 0,
	voucher_code: null,
	voucher_amount: 0,
	voucher_name: null,
	voucher_balance: 0,

	// ── Exchange State ──────────────────────────────────
	exchange_assessment: null,
	exchange_amount: 0,
	exchange_order: null,

	// ── Return / Product Exchange State ──────────────────
	return_against: null,
	return_items: [],
	product_exchange_credit: 0,
	product_exchange_invoice: null,

	// ── Loyalty ─────────────────────────────────────────
	loyalty_points: 0,
	loyalty_program: null,
	conversion_factor: 0,

	// ── Item Search ─────────────────────────────────────
	search_term: "",
	item_group_filter: "",
	view_mode: "card",
	in_stock_only: false,
	item_page: 0,
	item_page_size: 20,
	total_items: 0,
	last_items: [],

	// ── Network ─────────────────────────────────────────
	is_online: navigator.onLine,

	// ── Executive / Access Control ──────────────────────
	executive_access: null,     // { companies, is_manager, store_executives, own_executive, stores }
	active_company: null,       // currently selected company for billing
	sales_executive: null,      // selected POS Executive name (for billing attribution)
	sales_executive_name: null, // display name

	/** Reset transaction-specific state (after submit/cancel) */
	reset_transaction() {
		this.cart = [];
		this.customer = null;
		this.customer_info = null;
		this.additional_discount_pct = 0;
		this.additional_discount_amt = 0;
		this.discount_reason = "";
		this.coupon_code = null;
		this.coupon_discount = 0;
		this.voucher_code = null;
		this.voucher_amount = 0;
		this.voucher_name = null;
		this.voucher_balance = 0;
		this.sale_type = null;
		this.sale_sub_type = null;
		this.sale_reference = null;
		this.exchange_assessment = null;
		this.exchange_amount = 0;
		this.exchange_order = null;
		this.return_against = null;
		this.return_items = [];
		this.product_exchange_credit = 0;
		this.product_exchange_invoice = null;
		this.loyalty_points = 0;
		// Keep executive and company selection across transactions
		EventBus.emit("state:transaction_reset");
	},
};

/**
 * Lightweight publish/subscribe event bus.
 * Enables decoupled communication between POS modules.
 *
 * Usage:
 *   EventBus.on("cart:updated", (cart) => { ... });
 *   EventBus.emit("cart:updated", state.cart);
 */
export const EventBus = {
	_handlers: {},

	on(event, handler) {
		if (!this._handlers[event]) {
			this._handlers[event] = [];
		}
		this._handlers[event].push(handler);
	},

	off(event, handler) {
		if (!this._handlers[event]) return;
		if (handler) {
			this._handlers[event] = this._handlers[event].filter((h) => h !== handler);
		} else {
			delete this._handlers[event];
		}
	},

	emit(event, data) {
		const handlers = this._handlers[event];
		if (!handlers) return;
		for (const handler of handlers) {
			try {
				handler(data);
			} catch (e) {
				console.error(`[POS EventBus] Error in handler for "${event}":`, e);
			}
		}
	},

	/** Remove all handlers (used in teardown) */
	clear() {
		this._handlers = {};
	},
};
