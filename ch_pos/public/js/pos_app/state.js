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

	// ── Session & Cash Control ──────────────────────────
	session_name: null,
	business_date: null,
	store: null,
	device: null,
	session_status: null,  // Open, Locked, Pending Close, etc.
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

	// ── Payment Mode State ──────────────────────────────
	is_credit_sale: false,
	is_free_sale: false,
	free_sale_reason: "",
	free_sale_approved_by: "",

	// ── Loyalty ─────────────────────────────────────────
	loyalty_points: 0,
	loyalty_program: null,
	conversion_factor: 0,

	// ── Item Search ─────────────────────────────────────
	search_term: "",
	view_mode: "list",
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

	// ── Service Mode ────────────────────────────────────
	active_service_job: null,   // current service job being worked on
	service_job_items: [],      // items linked to active service job

	// ── Kiosk Token ────────────────────────────────────
	default_customer: null,     // POS Profile default customer (Walk-in Customer)
	kiosk_token: null,          // linked POS Kiosk Token name (for billing from queue)
	kiosk_token_status: null,   // status of linked kiosk token — surfaced as a pill on the cart banner
	guided_session: null,       // linked POS Guided Session name (for assisted sell flow)

	// ── Exception & Warranty ───────────────────────────
	exception_request: null,    // linked CH Exception Request name (approved, within validity)
	exception_request_data: null, // cached approved exception details for line pricing
	warranty_claim: null,       // linked CH Warranty Claim name (approved, processing fee billing)

	// ── B2B/B2C ─────────────────────────────────────────
	billing_gstin: "",          // GSTIN entered at billing time (overrides customer's saved GSTIN)

	// ── Sales Order Pickup ─────────────────────────────
	// When a pre-booking (Sales Order) is loaded into the cart for pickup
	// billing, ``sales_order_reference`` is the SO name. Items in the cart
	// carry per-line ``sales_order`` + ``so_detail`` so the backend can map
	// them back to the SO and pull the advance via ``set_advances()``.
	sales_order_reference: null,       // Sales Order being billed at pickup
	sales_order_advance: 0,            // Advance already paid on the SO (auto-applied at PAY)
	sales_order_grand_total: 0,        // SO grand total — used to render the cart banner
	sales_order_summary: null,         // { name, customer_name, due_date, reserved_serials }

	// ── Proforma → Sale Conversion ─────────────────────
	// When a Quotation (Proforma) is converted to a Sale via Prebook
	// workspace "Convert → Sale", the source quotation name is stamped here
	// so downstream code (audit log, print headers) can reference the
	// originating proforma. Cleared on transaction reset.
	source_quotation: null,            // Quotation name (proforma) being billed
	source_quotation_total: 0,         // Proforma grand total for reference

	// ── Customer Summary (enriched) ─────────────────────
	customer_summary: null,     // { order_count, active_warranties, active_service_jobs }

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
		this.is_credit_sale = false;
		this.is_free_sale = false;
		this.free_sale_reason = "";
		this.free_sale_approved_by = "";
		this._payment_state = null;
		this.loyalty_points = 0;
		this.active_service_job = null;
		this.service_job_items = [];
		this.customer_summary = null;
		this.kiosk_token = null;
		this.kiosk_token_status = null;
		this.guided_session = null;
		this.exception_request = null;
		this.exception_request_data = null;
		this.warranty_claim = null;
		this.billing_gstin = "";
		this.sales_order_reference = null;
		this.sales_order_advance = 0;
		this.sales_order_grand_total = 0;
		this.sales_order_summary = null;
		// Proforma → Sale conversion linkage (cleared per transaction)
		this.source_quotation = null;
		this.source_quotation_total = 0;
		// POS-10 fix: Clear persisted cart on transaction reset
		try { localStorage.removeItem("ch_pos_active_cart"); } catch (e) {}
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
