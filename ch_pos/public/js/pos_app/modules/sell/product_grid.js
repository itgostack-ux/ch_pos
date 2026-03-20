/**
 * CH POS — Premium Product Grid
 *
 * Card-first retail product display with:
 * - Rich product cards (image, brand, variant, price, stock, quick-add)
 * - IMEI/serial badge
 * - Offer ribbon
 * - OOS overlay with nearby stock
 * - List view as secondary compact mode
 */
import { PosState, EventBus } from "../../state.js";
import { format_number, item_placeholder_color, show_skeleton } from "../../shared/helpers.js";

export class ProductGrid {
	constructor(panel) {
		this.panel = panel;
		// Keep named references so we can remove them later
		this._on_items_loaded = (data) => {
			this.render_items(data.items);
			this.render_pager();
		};
		this._on_items_rerender = () => {
			this.render_items(PosState.last_items || []);
		};
		this._bind_events();
	}

	destroy() {
		EventBus.off("items:loaded", this._on_items_loaded);
		EventBus.off("items:rerender", this._on_items_rerender);
	}

	_bind_events() {
		EventBus.on("items:loaded", this._on_items_loaded);

		EventBus.on("items:rerender", this._on_items_rerender);

		// Quick-add button click
		this.panel.on("click", ".ch-pos-item-add-btn", (e) => {
			e.stopPropagation();
			const code = $(e.currentTarget).closest("[data-item-code]").data("item-code");
			const item = (PosState.last_items || []).find((i) => i.item_code === code);
			if (item) EventBus.emit("cart:add_item", item);
		});

		// Empty-state actions
		this.panel.on("click", ".ch-pos-btn-check-other-store", () => {
			const term = PosState.search_term;
			if (term) {
				this._show_nearby_stock(term);
			} else {
				frappe.show_alert({ message: __("Enter a search term first"), indicator: "orange" });
			}
		});
		this.panel.on("click", ".ch-pos-btn-request-stock", () => {
			EventBus.emit("mode:switch", "material_request");
		});
		this.panel.on("click", ".ch-pos-btn-clear-filters", () => {
			PosState.search_term = "";
			PosState.item_group_filter = "";
			PosState.item_page = 0;
			EventBus.emit("items:reload");
			EventBus.emit("search:cleared");
		});

		// Card click → add to cart or show nearby stock
		this.panel.on("click", ".ch-pos-item-card, .ch-pos-item-row", (e) => {
			if ($(e.target).closest(".ch-pos-item-add-btn, .ch-pos-nearby-store").length) return;
			const $el = $(e.currentTarget);
			const code = $el.data("item-code");
			if ($el.hasClass("out-of-stock")) {
				this._show_nearby_stock(code);
				return;
			}
			const item = (PosState.last_items || []).find((i) => i.item_code === code);
			if (item) EventBus.emit("cart:add_item", item);
		});
	}

	render_items(items) {
		const grid = this.panel.find(".ch-pos-items-grid");
		grid.empty();

		if (!items.length) {
			grid.html(`
				<div class="ch-pos-empty-state">
					<div class="empty-icon"><i class="fa fa-search"></i></div>
					<div class="empty-title">${__("No products found")}</div>
					<div class="empty-subtitle">${__("Try a different search or category")}</div>
					<div class="ch-pos-empty-actions">
						<button class="btn btn-sm btn-outline-primary ch-pos-btn-check-other-store">
							<i class="fa fa-map-marker"></i> ${__("Check Other Stores")}
						</button>
						<button class="btn btn-sm btn-outline-secondary ch-pos-btn-request-stock">
							<i class="fa fa-paper-plane-o"></i> ${__("Request Stock")}
						</button>
						<button class="btn btn-sm btn-outline-default ch-pos-btn-clear-filters">
							<i class="fa fa-times"></i> ${__("Clear Filters")}
						</button>
					</div>
				</div>`);
			return;
		}

		if (PosState.view_mode === "card") {
			grid.addClass("card-view").removeClass("list-view")
				.html(items.map((item) => this._card_html(item)).join(""));
		} else {
			const header = `<div class="ch-pos-list-header">
				<span class="col-brand">${__("Brand")}</span>
				<span class="col-name">${__("Model / Product")}</span>
				<span class="col-specs">${__("Specs")}</span>
				<span class="col-price">${__("Price")}</span>
				<span class="col-stock">${__("Stock")}</span>
				<span class="col-condition">${__("Condition")}</span>
			</div>`;
			grid.addClass("list-view").removeClass("card-view")
				.html(header + items.map((item) => this._row_html(item)).join(""));
		}
	}

	_card_html(item) {
		const abbr = frappe.get_abbr(item.item_name);
		const colors = item_placeholder_color(item.item_code);
		const img = item.image
			? `<img src="${item.image}" alt="${frappe.utils.escape_html(item.item_name)}" loading="lazy">`
			: `<div class="ch-pos-item-placeholder" style="background:${colors.bg};color:${colors.text}">${abbr}</div>`;

		const sell_price = item.selling_price || item.mrp || 0;
		const has_offer = item.offers && item.offers.length;
		const show_mrp = has_offer && item.mrp && item.mrp > sell_price;
		const price_html = sell_price
			? `<span class="ch-pos-item-price${has_offer ? " has-offer" : ""}">₹${format_number(sell_price)}</span>
			   ${show_mrp ? `<span class="ch-pos-item-mrp">₹${format_number(item.mrp)}</span>` : ""}`
			: `<span class="ch-pos-item-price">—</span>`;

		const in_stock = item.stock_qty > 0;
		const low_stock = in_stock && item.stock_qty <= 3;

		// Stock badge
		let stock_badge = "";
		if (in_stock) {
			stock_badge = low_stock
				? `<span class="ch-pos-stock-badge low-stock">${Math.floor(item.stock_qty)} left</span>`
				: `<span class="ch-pos-stock-badge in-stock">${Math.floor(item.stock_qty)}</span>`;
		}

		// Offer ribbon
		const offer_ribbon = has_offer
			? `<div class="ch-pos-offer-ribbon">${frappe.utils.escape_html(item.offers[0].offer_name)}</div>`
			: "";

		// IMEI/serial required badge
		const serial_badge = item.has_serial_no
			? `<span class="ch-pos-serial-badge">IMEI</span>`
			: "";

		// Item type badge (New / Refurbished / Display / Pre-Owned)
		let type_badge = "";
		const item_type = item.ch_item_type || "";
		if (item_type === "Refurbished") {
			type_badge = `<span class="ch-pos-type-badge refurb">Refurb</span>`;
		} else if (item_type === "Display") {
			type_badge = `<span class="ch-pos-type-badge display">Display</span>`;
		} else if (item_type === "Pre-Owned") {
			type_badge = `<span class="ch-pos-type-badge preowned">Pre-Owned</span>`;
		}

		// Grade badge for refurb/pre-owned (Superb / Good / Fair)
		let grade_badge = "";
		if (item.condition_grade) {
			const g = item.condition_grade;
			const gcls = g === "Superb" || g === "Excellent" ? "grade-superb"
				: g === "Good" ? "grade-good" : "grade-fair";
			grade_badge = `<span class="ch-pos-grade-badge ${gcls}">${frappe.utils.escape_html(g)}</span>`;
		}

		// Warranty badge
		let warranty_badge = "";
		if (item.ch_default_warranty_months && item.ch_default_warranty_months > 0) {
			warranty_badge = `<span class="ch-pos-warranty-badge">${item.ch_default_warranty_months}M Warranty</span>`;
		}

		// Brand
		const brand_html = item.brand
			? `<div class="ch-pos-item-brand">${frappe.utils.escape_html(item.brand)}</div>`
			: "";

		// Attribute pills (Colour, Storage, RAM as distinct badges)
		let attr_pills_html = "";
		if (item.attributes && item.attributes.length) {
			const pills = item.attributes
				.filter(a => a.attribute_value)
				.map(a => {
					const cls = (a.attribute || "").toLowerCase().replace(/\s+/g, "-");
					return `<span class="ch-pos-attr-pill attr-${frappe.utils.escape_html(cls)}">${frappe.utils.escape_html(a.attribute_value)}</span>`;
				});
			if (pills.length) {
				attr_pills_html = `<div class="ch-pos-attr-pills">${pills.join("")}</div>`;
			}
		}

		// OOS label
		const oos_label = !in_stock ? `<div class="ch-pos-oos-label">${__("OUT OF STOCK")}</div>` : "";

		// Nearby store hint
		let nearby_html = "";
		if (!in_stock && item.nearby_stores && item.nearby_stores.length) {
			const ns = item.nearby_stores[0];
			nearby_html = `<span class="ch-pos-nearby-store">📍 ${frappe.utils.escape_html(ns.store_name)} (${Math.floor(ns.qty)})</span>`;
		}

		// Quick-add button (only for in-stock)
		const add_btn = in_stock
			? `<button class="ch-pos-item-add-btn" title="${__("Add to cart")}">+</button>`
			: "";

		return `
			<div class="ch-pos-item-card${!in_stock ? " out-of-stock" : ""}"
				data-item-code="${frappe.utils.escape_html(item.item_code)}">
				<div class="ch-pos-item-img">
					${img}
					${stock_badge}
					${offer_ribbon}
					${serial_badge}
					${type_badge}
					${oos_label}
				</div>
				<div class="ch-pos-item-info">
					${brand_html}
					<div class="ch-pos-item-name">${frappe.utils.escape_html(item.item_name)}</div>
					${attr_pills_html}
					<div class="ch-pos-item-badges">
						${grade_badge}${warranty_badge}
					</div>
					<div class="ch-pos-item-meta">
						<div>${price_html}</div>
						${add_btn}
					</div>
					${nearby_html}
				</div>
			</div>`;
	}

	_row_html(item) {
		const sell_price = item.selling_price || item.mrp || 0;
		const has_offer = item.offers && item.offers.length;
		const show_mrp = has_offer && item.mrp && item.mrp > sell_price;
		const price_html = sell_price
			? `<span class="row-price${has_offer ? " has-offer" : ""}">₹${format_number(sell_price)}</span>
			   ${show_mrp ? `<span class="row-mrp">₹${format_number(item.mrp)}</span>` : ""}`
			: `<span class="row-price">—</span>`;

		const in_stock = item.stock_qty > 0;
		const low_stock = in_stock && item.stock_qty <= 3;
		const stock_html = in_stock
			? (low_stock
				? `<span class="stock-badge low">${Math.floor(item.stock_qty)} left</span>`
				: `<span class="stock-badge in">${Math.floor(item.stock_qty)}</span>`)
			: `<span class="stock-badge out">OOS</span>`;

		// Specs: attributes as compact pills
		let specs_html = "";
		if (item.attributes && item.attributes.length) {
			specs_html = item.attributes
				.filter(a => a.attribute_value)
				.map(a => `<span class="row-spec-pill">${frappe.utils.escape_html(a.attribute_value)}</span>`)
				.join("");
		}
		// Serial badge
		if (item.has_serial_no) {
			specs_html += `<span class="row-spec-pill row-serial-pill">IMEI</span>`;
		}

		// Condition: type + grade
		let condition_html = "";
		const item_type = item.ch_item_type || "";
		if (item_type && item_type !== "New") {
			const type_cls = item_type === "Refurbished" ? "refurb"
				: item_type === "Pre-Owned" ? "preowned"
				: item_type === "Display" ? "display" : "";
			condition_html += `<span class="row-condition-badge ${type_cls}">${frappe.utils.escape_html(item_type)}</span>`;
		}
		if (item.condition_grade) {
			const g = item.condition_grade;
			const gcls = g === "Superb" || g === "Excellent" ? "grade-superb"
				: g === "Good" ? "grade-good" : "grade-fair";
			condition_html += `<span class="row-grade-badge ${gcls}">${frappe.utils.escape_html(g)}</span>`;
		}
		if (item.ch_default_warranty_months && item.ch_default_warranty_months > 0) {
			condition_html += `<span class="row-warranty-badge">${item.ch_default_warranty_months}M</span>`;
		}

		// Offer tag
		const offer_html = has_offer
			? `<span class="row-offer-tag">${frappe.utils.escape_html(item.offers[0].offer_name)}</span>`
			: "";

		return `
			<div class="ch-pos-item-row${!in_stock ? " out-of-stock" : ""}"
				data-item-code="${frappe.utils.escape_html(item.item_code)}">
				<span class="col-brand">${frappe.utils.escape_html(item.brand || "—")}</span>
				<span class="col-name">
					<span class="row-item-name">${frappe.utils.escape_html(item.item_name)}</span>
					${offer_html}
				</span>
				<span class="col-specs">${specs_html}</span>
				<span class="col-price">${price_html}</span>
				<span class="col-stock">${stock_html}</span>
				<span class="col-condition">${condition_html}</span>
			</div>`;
	}

	render_pager() {
		const pager = this.panel.find(".ch-pos-items-pager");
		pager.empty();
		const total_pages = Math.ceil(PosState.total_items / PosState.item_page_size);
		if (total_pages <= 1) return;

		let html = "";
		if (PosState.item_page > 0) {
			html += `<button class="btn btn-sm btn-default ch-pos-page-prev" style="border-radius:var(--pos-radius-sm)">&laquo; ${__("Prev")}</button>`;
		}
		html += `<span class="ch-pos-page-info">${PosState.item_page + 1} / ${total_pages}</span>`;
		if (PosState.item_page < total_pages - 1) {
			html += `<button class="btn btn-sm btn-default ch-pos-page-next" style="border-radius:var(--pos-radius-sm)">${__("Next")} &raquo;</button>`;
		}
		pager.html(html);

		pager.find(".ch-pos-page-prev").on("click", () => { PosState.item_page--; EventBus.emit("items:reload"); });
		pager.find(".ch-pos-page-next").on("click", () => { PosState.item_page++; EventBus.emit("items:reload"); });
	}

	_show_nearby_stock(item_code) {
		const item_data = (PosState.last_items || []).find((i) => i.item_code === item_code);
		const item_name = item_data ? item_data.item_name : item_code;

		frappe.call({
			method: "ch_pos.api.search.get_nearby_stock",
			args: { item_code, pos_profile: PosState.pos_profile },
			callback: (r) => {
				const stores = r.message || [];
				let body = "";
				if (stores.length) {
					const rows = stores.map((s) => {
						const cls = s.qty > 0 ? "stock-ok" : "stock-zero";
						return `<tr>
							<td>${frappe.utils.escape_html(s.store_name)}</td>
							<td>${frappe.utils.escape_html(s.city || "")}</td>
							<td class="${cls}">${Math.floor(s.qty)}</td>
						</tr>`;
					}).join("");
					body = `<table class="table table-sm ch-pos-nearby-table">
						<thead><tr><th>${__("Store")}</th><th>${__("City")}</th><th>${__("Qty")}</th></tr></thead>
						<tbody>${rows}</tbody>
					</table>`;
				} else {
					body = `<p class="text-muted text-center">${__("Not available at any nearby stores")}</p>`;
				}
				new frappe.ui.Dialog({
					title: __("Stock: {0}", [frappe.utils.escape_html(item_name)]),
					fields: [{ fieldtype: "HTML", options: body }],
					size: "small",
				}).show();
			},
		});
	}
}
