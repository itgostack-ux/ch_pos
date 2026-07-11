/**
 * CH POS — Retail Contextual Toolbar
 *
 * Sell mode: scan-first search, category chips, stock toggle,
 * card/list view toggle, barcode scanner.
 */
import { PosState, EventBus } from "../state.js";
import { debounce } from "../shared/helpers.js";

export class ContextualToolbar {
	constructor(panel) {
		this.panel = panel;
		this._scan_buffer = "";
		this._scan_timer = null;
		this._bind_scanner();
	}

	render_sell_toolbar() {
		this.panel.prepend(`
			<div class="ch-pos-toolbar">
				<div class="ch-pos-toolbar-left">
					<div class="ch-pos-imei-wrap">
						<div class="ch-pos-imei-input-box">
							<i class="fa fa-barcode ch-pos-imei-icon"></i>
							<input type="text" class="form-control ch-pos-imei-input"
								placeholder="${__("Scan IMEI / Serial...")}"
								autocomplete="off">
						</div>
						<button class="btn btn-default ch-pos-cam-scan"
							title="${__("Scan with camera")}"
							aria-label="${__("Scan with camera")}">
							<i class="fa fa-camera"></i>
						</button>
					</div>
					<div class="ch-pos-search-wrap">
						<i class="fa fa-search ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-pos-search"
							placeholder="${__("Search products...")}"
							value="${frappe.utils.escape_html(PosState.search_term || "")}">
					</div>
				</div>
				<div class="ch-pos-toolbar-right">
					<label class="ch-pos-stock-toggle${PosState.in_stock_only ? " active" : ""}">
						<input type="checkbox" class="ch-pos-stock-check"
							${PosState.in_stock_only ? "checked" : ""}>
						<i class="fa fa-check-circle" style="font-size:11px"></i>
						<span>${__("In Stock")}</span>
					</label>
					<button class="btn btn-xs btn-default ch-pos-btn-reprint" title="${__("Reprint today\'s invoices")}">
						<i class="fa fa-print"></i> ${__("Reprint")}
					</button>
					<button class="btn btn-xs btn-default ch-pos-btn-redeem-gift" title="${__("Redeem a spin-wheel gift code")}">
						<i class="fa fa-gift"></i> ${__("Redeem Gift")}
					</button>
					<div class="btn-group ch-pos-view-toggle">
						<button class="btn btn-xs ch-pos-view-card
							${PosState.view_mode === "card" ? "btn-primary active" : "btn-default"}">
							<i class="fa fa-th-large"></i>
						</button>
						<button class="btn btn-xs ch-pos-view-list
							${PosState.view_mode === "list" ? "btn-primary active" : "btn-default"}">
							<i class="fa fa-list"></i>
						</button>
					</div>
				</div>
			</div>
		`);

		this._bind_sell_events();
	}

	_bind_sell_events() {
		const panel = this.panel;

		const do_search = debounce((val) => {
			PosState.search_term = val;
			PosState.item_page = 0;
			EventBus.emit("items:reload");
		}, 300);

		panel.on("input", ".ch-pos-search", function () {
			do_search($(this).val().trim());
		});

		// IMEI / Serial scan — Enter to scan immediately
		panel.on("keydown", ".ch-pos-imei-input", (e) => {
			if (e.key === "Enter") {
				e.preventDefault();
				const val = panel.find(".ch-pos-imei-input").val().trim();
				if (val.length >= 4) {
					this._handle_scan(val);
					panel.find(".ch-pos-imei-input").val("");
				}
			}
		});

		// Phase 2 — Camera scan button (lazy-loads ZXing on first click)
		panel.on("click", ".ch-pos-cam-scan", (e) => {
			e.preventDefault();
			if (!(window.ch_pos && window.ch_pos.open_camera_scan)) {
				frappe.show_alert({ message: __("Camera scanner not loaded yet — try again."), indicator: "orange" });
				return;
			}
			window.ch_pos.open_camera_scan((code) => {
				if (!code) return;
				const input = panel.find(".ch-pos-imei-input");
				input.val(code).focus();
				if (code.length >= 4) {
					this._handle_scan(code);
					input.val("");
				}
			});
		});

		// F2 / Escape → focus IMEI input first, then search
		EventBus.on("search:focus", () => {
			const imei = panel.find(".ch-pos-imei-input");
			if (imei.length && !imei.is(":focus")) {
				imei.focus().select();
			} else {
				panel.find(".ch-pos-search").focus().select();
			}
		});
		// Clear filters from empty-state button
		EventBus.on("search:cleared", () => {
			panel.find(".ch-pos-search").val("");
		});

		// Stock toggle
		panel.on("change", ".ch-pos-stock-check", function () {
			const checked = $(this).is(":checked");
			panel.find(".ch-pos-stock-toggle").toggleClass("active", checked);
			PosState.in_stock_only = checked;
			PosState.item_page = 0;
			EventBus.emit("items:reload");
		});

		// Reprint button
		panel.on("click", ".ch-pos-btn-reprint", () => EventBus.emit("reprint:open"));

		// Redeem Gift button — spin-wheel gift redemption flow.
		panel.on("click", ".ch-pos-btn-redeem-gift", () => this._open_redeem_gift_dialog());

		// View toggle
		panel.on("click", ".ch-pos-view-card", function () {
			PosState.view_mode = "card";
			$(this).addClass("btn-primary active").removeClass("btn-default");
			panel.find(".ch-pos-view-list").addClass("btn-default").removeClass("btn-primary active");
			EventBus.emit("items:rerender");
		});
		panel.on("click", ".ch-pos-view-list", function () {
			PosState.view_mode = "list";
			$(this).addClass("btn-primary active").removeClass("btn-default");
			panel.find(".ch-pos-view-card").addClass("btn-default").removeClass("btn-primary active");
			EventBus.emit("items:rerender");
		});
	}

	/** Barcode scanner — physical scanners send rapid keystrokes + Enter */
	_bind_scanner() {
		$(document).on("keydown.ch_pos_scanner", (e) => {
			const tag = (e.target.tagName || "").toLowerCase();
			if (tag === "input" || tag === "textarea" || tag === "select") return;

			if (e.key === "Enter" && this._scan_buffer.length >= 4) {
				e.preventDefault();
				this._handle_scan(this._scan_buffer);
				this._scan_buffer = "";
				return;
			}
			if (e.key.length === 1) {
				this._scan_buffer += e.key;
				clearTimeout(this._scan_timer);
				this._scan_timer = setTimeout(() => { this._scan_buffer = ""; }, 100);
			}
		});
	}

	_handle_scan(barcode) {
		frappe.call({
			method: "ch_pos.api.pos_api.scan_barcode",
			args: { barcode, pos_profile: PosState.pos_profile },
			callback: (r) => {
				if (r.message && r.message.item_code) {
					const data = r.message;
					if (data.serial_no) {
						// The barcode resolved to a specific serial number — the
						// cashier scanned or typed an exact IMEI.  Always take the
						// direct-add path: _add_to_cart_direct_serial validates the
						// serial (stock, warehouse, duplicate) and runs the FIFO check
						// internally.  Same-date serials → added silently; a serial
						// received later than the oldest → confirmation dialog.
						// Opening the IMEI selection popup would be confusing here
						// because the cashier already knows which unit they want.
						EventBus.emit("cart:scan_serial", data);
					} else {
						// Product barcode (not a serial) — open item flow normally.
						EventBus.emit("cart:add_item", data);
					}
				} else {
					frappe.show_alert({
						message: __("Barcode not found: {0}", [frappe.utils.escape_html(barcode)]),
						indicator: "red",
					});
				}
			},
		});
	}

	destroy() {
		$(document).off("keydown.ch_pos_scanner");
	}

	// ── Spin-wheel gift redemption ────────────────────────────────────
	//
	// Two-step flow:
	//   1. Cashier enters the customer's redemption code.
	//   2. Server returns metadata (customer, parent invoice, reward item,
	//      status). We show a confirmation modal — cashier hits "Redeem"
	//      and we call `redeem_gift_code` which creates a ₹0 Sales Invoice
	//      linked back to the original invoice.
	//
	// The button lives in the sell-mode toolbar; it does not require the
	// cart to be empty because the reward is booked as its own invoice.
	_open_redeem_gift_dialog() {
		if (!PosState.pos_profile) {
			frappe.show_alert({ message: __("No active POS profile."), indicator: "orange" });
			return;
		}

		const d = new frappe.ui.Dialog({
			title: __("Redeem Gift Code"),
			fields: [
				{
					fieldtype: "Data",
					fieldname: "code",
					label: __("Redemption Code"),
					reqd: 1,
					description: __("Enter the code the customer received (e.g. GIFT-A7X2)."),
				},
			],
			primary_action_label: __("Lookup"),
			primary_action: (values) => {
				const code = (values.code || "").trim().toUpperCase();
				if (!code) return;
				frappe.call({
					method: "ch_pos.api.gift_redemption.lookup_gift_code",
					args: { code },
					callback: (r) => {
						if (!r.message) return;
						d.hide();
						this._confirm_redeem_gift(r.message);
					},
				});
			},
		});
		d.show();
	}

	_confirm_redeem_gift(gift) {
		const html = `
			<div style="font-size:14px;line-height:1.7">
				<div><b>${__("Customer")}:</b> ${frappe.utils.escape_html(gift.customer_name || gift.customer || "-")}</div>
				<div><b>${__("Original Invoice")}:</b> ${frappe.utils.escape_html(gift.parent_sales_invoice || "-")}</div>
				<div><b>${__("Reward")}:</b> ${gift.reward_qty || 1} × ${frappe.utils.escape_html(gift.reward_item_name || gift.reward_item || "-")}</div>
				<div><b>${__("Status")}:</b> <span style="color:${gift.status === "Revealed" ? "#16a34a" : "#dc2626"}">${gift.status}</span></div>
				<div style="color:#6b7280;font-size:12px;margin-top:4px">
					${__("Expires")}: ${gift.expires_at || "-"}
				</div>
			</div>`;

		const can_redeem = gift.status === "Revealed";
		const dlg = new frappe.ui.Dialog({
			title: __("Confirm Gift Redemption"),
			fields: [{ fieldtype: "HTML", fieldname: "info", options: html }],
			primary_action_label: can_redeem ? __("Redeem & Create Invoice") : __("Close"),
			primary_action: () => {
				if (!can_redeem) { dlg.hide(); return; }
				frappe.call({
					method: "ch_pos.api.gift_redemption.redeem_gift_code",
					args: {
						code: gift.redemption_code,
						pos_profile: PosState.pos_profile,
					},
					freeze: true,
					freeze_message: __("Booking free-gift invoice..."),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						frappe.show_alert({
							message: __("Gift booked as invoice {0}", [r.message.redeemed_invoice]),
							indicator: "green",
						});
						EventBus.emit("invoice:submitted", r.message.redeemed_invoice);
					},
				});
			},
		});
		dlg.show();
	}
}
