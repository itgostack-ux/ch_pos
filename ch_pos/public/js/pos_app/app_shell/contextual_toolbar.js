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
						<i class="fa fa-barcode ch-pos-imei-icon"></i>
						<input type="text" class="form-control ch-pos-imei-input"
							placeholder="${__("Scan IMEI / Serial...")}"
							autocomplete="off">
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
			<div class="ch-pos-category-chips">
				<button class="ch-pos-category-chip active" data-group="">${__("All")}</button>
			</div>
		`);

		this._bind_sell_events();
		this._load_item_groups();
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
			panel.find(".ch-pos-category-chip").removeClass("active").first().addClass("active");
		});

		// Category chip click
		panel.on("click", ".ch-pos-category-chip", function () {
			panel.find(".ch-pos-category-chip").removeClass("active");
			$(this).addClass("active");
			PosState.item_group_filter = $(this).data("group") || "";
			PosState.item_page = 0;
			EventBus.emit("items:reload");
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

	_load_item_groups() {
		frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Item Group",
				filters: { is_group: 0 },
				fields: ["name"],
				order_by: "name asc",
				limit_page_length: 0,
			},
			callback: (r) => {
				const chips = this.panel.find(".ch-pos-category-chips");
				(r.message || []).forEach((g) => {
					chips.append(
						`<button class="ch-pos-category-chip" data-group="${frappe.utils.escape_html(g.name)}">${frappe.utils.escape_html(g.name)}</button>`
					);
				});
			},
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
					EventBus.emit("cart:add_item", r.message);
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
}
