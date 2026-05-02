/**
 * CH POS — Model Comparison Workspace
 *
 * Store executives compare phones by specs (RAM, storage, brand)
 * and see associated offers, bank offers, and schemes at a glance.
 * Designed for assisted selling: "I need a 16GB Android phone."
 */
import { PosState, EventBus } from "../../state.js";
import { format_number } from "../../shared/helpers.js";

export class ModelCompareWorkspace {
	constructor() {
		this.filters = { brand: "", ram: "", storage: "", search: "" };
		this.filter_options = { brands: [], ram_values: [], storage_values: [] };
		this.results = [];
		this.selected = []; // items selected for side-by-side comparison
		this.loading = false;

		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "model_compare") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this.panel = panel;
		this.selected = [];
		this.results = [];

		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#fef3c7;color:#d97706">
							<i class="fa fa-columns"></i>
						</span>
						${__("Model Comparison")}
					</h4>
					<span class="ch-mode-hint">${__("Compare phones by specs and see all offers at a glance")}</span>
				</div>

				<!-- Filter Bar -->
				<div class="ch-compare-filters">
					<div class="ch-compare-search-wrap">
						<i class="fa fa-search ch-pos-search-icon"></i>
						<input type="text" class="form-control ch-compare-search"
							placeholder="${__("Search model name...")}">
					</div>
					<select class="form-control ch-compare-brand">
						<option value="">${__("All Brands")}</option>
					</select>
					<select class="form-control ch-compare-ram">
						<option value="">${__("Any RAM")}</option>
					</select>
					<select class="form-control ch-compare-storage">
						<option value="">${__("Any Storage")}</option>
					</select>
					<button class="btn btn-primary ch-compare-search-btn">
						<i class="fa fa-search"></i> ${__("Find")}
					</button>				<label class="ch-pos-stock-toggle" style="white-space:nowrap;font-size:12px;cursor:pointer;display:flex;align-items:center;gap:4px;">
					<input type="checkbox" class="ch-compare-instock-check">
					<i class="fa fa-check-circle" style="font-size:11px"></i>
					<span>${__("In Stock Only")}</span>
				</label>				</div>

				<!-- Compare Bar (shows when items selected) -->
				<div class="ch-compare-bar" style="display:none">
					<span class="ch-compare-bar-text"></span>
					<button class="btn btn-warning ch-compare-now-btn">
						<i class="fa fa-columns"></i> ${__("Compare Selected")}
					</button>
					<button class="btn btn-outline-secondary ch-compare-clear-btn">
						${__("Clear")}
					</button>
				</div>

				<!-- Results Area -->
				<div class="ch-compare-results">
					<div class="ch-pos-empty-state" style="padding:40px 16px;">
						<div class="empty-icon"><i class="fa fa-columns"></i></div>
						<div class="empty-title">${__("Search & Compare")}</div>
						<div class="empty-subtitle">${__("Select brand, RAM or storage to find matching models with offers")}</div>
					</div>
				</div>

				<!-- Side-by-side Comparison View (hidden initially) -->
				<div class="ch-compare-side-by-side" style="display:none"></div>
			</div>
		`);

		this._bind(panel);
		this._load_filters();
	}

	// ── Filter loading ──────────────────────────────────
	_load_filters() {
		frappe.call({
			method: "ch_pos.api.pos_api.get_comparison_filters",
			async: true,
			callback: (r) => {
				if (!r.message) return;
				this.filter_options = r.message;
				this._populate_filter_dropdowns();
			},
		});
	}

	_populate_filter_dropdowns() {
		const p = this.panel;
		const brandSel = p.find(".ch-compare-brand");
		const ramSel = p.find(".ch-compare-ram");
		const storageSel = p.find(".ch-compare-storage");

		for (const b of this.filter_options.brands || []) {
			brandSel.append(`<option value="${frappe.utils.escape_html(b)}">${frappe.utils.escape_html(b)}</option>`);
		}
		for (const r of this.filter_options.ram_values || []) {
			ramSel.append(`<option value="${frappe.utils.escape_html(r)}">${frappe.utils.escape_html(r)}</option>`);
		}
		for (const s of this.filter_options.storage_values || []) {
			storageSel.append(`<option value="${frappe.utils.escape_html(s)}">${frappe.utils.escape_html(s)}</option>`);
		}
	}

	// ── Event binding ───────────────────────────────────
	_bind(panel) {
		let searchTimeout;
		panel.find(".ch-compare-search").on("input", () => {
			clearTimeout(searchTimeout);
			searchTimeout = setTimeout(() => this._do_search(), 400);
		});

		panel.find(".ch-compare-brand, .ch-compare-ram, .ch-compare-storage").on("change", () => {
			this._do_search();
		});

		panel.find(".ch-compare-instock-check").on("change", () => {
			this._do_search();
		});

		panel.find(".ch-compare-search-btn").on("click", () => this._do_search());

		panel.find(".ch-compare-search").on("keydown", (e) => {
			if (e.key === "Enter") this._do_search();
		});

		panel.on("click", ".ch-compare-card-select", (e) => {
			const itemCode = $(e.currentTarget).data("item");
			this._toggle_select(itemCode);
		});

		panel.find(".ch-compare-now-btn").on("click", () => this._show_comparison());
		panel.find(".ch-compare-clear-btn").on("click", () => {
			this.selected = [];
			this._update_compare_bar();
			this.panel.find(".ch-compare-card").removeClass("selected");
		});

		panel.on("click", ".ch-compare-back-btn", () => {
			this.panel.find(".ch-compare-side-by-side").hide();
			this.panel.find(".ch-compare-results").show();
			this.panel.find(".ch-compare-filters").show();
			this.panel.find(".ch-compare-bar").show();
		});

		// Toggle nearby stock detail expand
		panel.on("click", ".ch-compare-nearby", (e) => {
			$(e.currentTarget).closest(".ch-compare-card-body")
				.find(".ch-nearby-detail").slideToggle(150);
		});
	}

	// ── Search ──────────────────────────────────────────
	_do_search() {
		const p = this.panel;
		this.filters = {
			brand: p.find(".ch-compare-brand").val(),
			ram: p.find(".ch-compare-ram").val(),
			storage: p.find(".ch-compare-storage").val(),
			search: p.find(".ch-compare-search").val().trim(),
			in_stock_only: p.find(".ch-compare-instock-check").prop("checked") ? 1 : 0,
		};

		// Need at least one filter
		if (!this.filters.brand && !this.filters.ram && !this.filters.storage && !this.filters.search) {
			return;
		}

		this.loading = true;
		this._render_loading();

		frappe.call({
			method: "ch_pos.api.pos_api.get_model_comparison",
			args: {
				brand: this.filters.brand || undefined,
				ram: this.filters.ram || undefined,
				storage: this.filters.storage || undefined,
				search_text: this.filters.search || undefined,
				pos_profile: PosState.pos_profile || undefined,
				in_stock_only: this.filters.in_stock_only || undefined,
			},
			callback: (r) => {
				this.loading = false;
				this.results = r.message || [];
				this._render_results();
			},
			error: () => {
				this.loading = false;
				this._render_error();
			},
		});
	}

	// ── Rendering ───────────────────────────────────────
	_render_loading() {
		this.panel.find(".ch-compare-results").html(`
			<div style="text-align:center;padding:48px">
				<i class="fa fa-spinner fa-spin fa-2x" style="color:var(--pos-primary);opacity:0.5"></i>
				<div style="margin-top:12px;color:var(--pos-text-muted)">${__("Finding models...")}</div>
			</div>
		`);
	}

	_render_error() {
		this.panel.find(".ch-compare-results").html(`
			<div class="ch-pos-empty-state" style="padding:40px 16px;">
				<div class="empty-icon"><i class="fa fa-exclamation-triangle" style="color:var(--pos-danger)"></i></div>
				<div class="empty-title">${__("Error loading models")}</div>
				<div class="empty-subtitle">${__("Check your connection and try again")}</div>
			</div>
		`);
	}

	_render_results() {
		const container = this.panel.find(".ch-compare-results");

		if (!this.results.length) {
			container.html(`
				<div class="ch-pos-empty-state" style="padding:40px 16px;">
					<div class="empty-icon"><i class="fa fa-search"></i></div>
					<div class="empty-title">${__("No models found")}</div>
					<div class="empty-subtitle">${__("Try different filters or search terms")}</div>
				</div>
			`);
			return;
		}

		let html = `
			<div class="ch-compare-summary">
				<span>${this.results.length} ${__("model(s) found")}</span>
			</div>
			<div class="ch-compare-grid">
		`;

		for (const item of this.results) {
			const isSelected = this.selected.includes(item.item_code);
			const priceText = item.min_price
				? (item.min_price === item.max_price
					? `₹${format_number(item.min_price)}`
					: `₹${format_number(item.min_price)} — ₹${format_number(item.max_price)}`)
				: __("Price N/A");
			const stockClass = item.stock > 0 ? "in-stock" : "no-stock";
			const stockText = item.stock > 0 ? `${item.stock} ${__("in stock")}` : __("Out of stock");

			// Spec pills
			let specPills = "";
			if (item.specs.RAM) specPills += `<span class="ch-spec-pill"><i class="fa fa-microchip"></i> ${frappe.utils.escape_html(item.specs.RAM)}</span>`;
			if (item.specs.Storage) specPills += `<span class="ch-spec-pill"><i class="fa fa-database"></i> ${frappe.utils.escape_html(item.specs.Storage)}</span>`;
			if (item.specs.Color) specPills += `<span class="ch-spec-pill"><i class="fa fa-paint-brush"></i> ${frappe.utils.escape_html(item.specs.Color)}</span>`;

			// Offer badges
			let offerBadges = "";
			if (item.brand_offers.length) {
				offerBadges += `<span class="ch-offer-badge brand-offer"><i class="fa fa-tag"></i> ${item.brand_offers.length} ${__("Brand")}</span>`;
			}
			if (item.bank_offers.length) {
				offerBadges += `<span class="ch-offer-badge bank-offer"><i class="fa fa-university"></i> ${item.bank_offers.length} ${__("Bank")}</span>`;
			}
			if (item.other_offers.length) {
				offerBadges += `<span class="ch-offer-badge other-offer"><i class="fa fa-percent"></i> ${item.other_offers.length} ${__("Other")}</span>`;
			}

			// Nearby stock
			const nearby = item.nearby_stock || [];
			let nearbyHtml = "";
			if (nearby.length) {
				const totalNearby = nearby.reduce((s, n) => s + (n.qty || 0), 0);
				const rows = nearby.map(n =>
					`<span style="font-size:10px;color:#374151">${frappe.utils.escape_html(n.pos_profile)}: ${n.qty}</span>`
				).join(" · ");
				nearbyHtml = `
					<div class="ch-compare-nearby" style="margin-top:4px;font-size:11px;color:#0369a1;cursor:pointer" title="${rows}">
						<i class="fa fa-map-marker"></i> ${totalNearby} ${__("nearby")}
						<span class="ch-nearby-expand" style="font-size:10px;color:#94a3b8"> (${nearby.length} store${nearby.length > 1 ? "s" : ""})</span>
					</div>
					<div class="ch-nearby-detail" style="display:none;margin-top:2px;padding:4px 8px;background:#f0f9ff;border-radius:6px;font-size:10px;color:#374151">
						${rows}
					</div>`;
			} else if (item.stock === 0) {
				nearbyHtml = `<div style="font-size:11px;color:#9ca3af;margin-top:4px"><i class="fa fa-times-circle"></i> ${__("Not available at other stores")}</div>`;
			}

			html += `
				<div class="ch-compare-card${isSelected ? " selected" : ""}" data-item="${frappe.utils.escape_html(item.item_code)}">
					<div class="ch-compare-card-top">
						<div class="ch-compare-card-image">
							${item.image ? `<img src="${item.image}" alt="">` : `<i class="fa fa-mobile fa-2x"></i>`}
						</div>
						<button class="ch-compare-card-select" data-item="${frappe.utils.escape_html(item.item_code)}"
							title="${__("Add to comparison")}">
							<i class="fa ${isSelected ? "fa-check-square" : "fa-square-o"}"></i>
						</button>
					</div>
					<div class="ch-compare-card-body">
						<div class="ch-compare-card-brand">${frappe.utils.escape_html(item.brand || "")}</div>
						<div class="ch-compare-card-name">${frappe.utils.escape_html(item.item_name || item.item_code)}</div>
						<div class="ch-compare-card-specs">${specPills}</div>
						<div class="ch-compare-card-price">${priceText}</div>
						<div class="ch-compare-card-stock ${stockClass}">
							<i class="fa ${item.stock > 0 ? "fa-check-circle" : "fa-times-circle"}"></i> ${stockText}
						</div>
						${nearbyHtml}
						${item.variant_count ? `<div class="ch-compare-card-variants">${item.variant_count} ${__("variant(s)")}</div>` : ""}
					</div>
					${offerBadges ? `<div class="ch-compare-card-offers">${offerBadges}</div>` : ""}
				</div>
			`;
		}

		html += `</div>`;
		container.html(html);

		this._update_compare_bar();
	}

	// ── Selection ───────────────────────────────────────
	_toggle_select(itemCode) {
		const idx = this.selected.indexOf(itemCode);
		if (idx >= 0) {
			this.selected.splice(idx, 1);
		} else {
			if (this.selected.length >= 4) {
				frappe.show_alert({ message: __("Maximum 4 items for comparison"), indicator: "orange" });
				return;
			}
			this.selected.push(itemCode);
		}
		// Update card UI
		const card = this.panel.find(`.ch-compare-card[data-item="${itemCode}"]`);
		card.toggleClass("selected", this.selected.includes(itemCode));
		card.find(".ch-compare-card-select i")
			.toggleClass("fa-check-square", this.selected.includes(itemCode))
			.toggleClass("fa-square-o", !this.selected.includes(itemCode));

		this._update_compare_bar();
	}

	_update_compare_bar() {
		const bar = this.panel.find(".ch-compare-bar");
		if (this.selected.length >= 2) {
			bar.show();
			bar.find(".ch-compare-bar-text").text(
				`${this.selected.length} ${__("models selected")}`
			);
		} else if (this.selected.length === 1) {
			bar.show();
			bar.find(".ch-compare-bar-text").text(__("Select at least 1 more to compare"));
		} else {
			bar.hide();
		}
	}

	// ── Side-by-side Comparison ─────────────────────────
	_show_comparison() {
		if (this.selected.length < 2) {
			frappe.show_alert({ message: __("Select at least 2 models"), indicator: "orange" });
			return;
		}

		const items = this.selected.map((code) =>
			this.results.find((r) => r.item_code === code)
		).filter(Boolean);

		// Hide grid, show comparison
		this.panel.find(".ch-compare-results").hide();
		this.panel.find(".ch-compare-filters").hide();
		this.panel.find(".ch-compare-bar").hide();

		const sbs = this.panel.find(".ch-compare-side-by-side");
		sbs.show();

		// Collect all spec keys across selected items
		const allSpecs = new Set();
		const allFeatureGroups = new Map(); // group → Set of feature names
		for (const item of items) {
			Object.keys(item.specs || {}).forEach((k) => allSpecs.add(k));
			for (const [group, features] of Object.entries(item.features || {})) {
				if (!allFeatureGroups.has(group)) allFeatureGroups.set(group, new Set());
				for (const f of features) {
					allFeatureGroups.get(group).add(f.feature);
				}
			}
		}

		const colWidth = Math.floor(100 / items.length);

		let html = `
			<div class="ch-compare-sbs-header">
				<button class="btn btn-outline-secondary ch-compare-back-btn">
					<i class="fa fa-arrow-left"></i> ${__("Back to Results")}
				</button>
				<h5>${__("Side-by-Side Comparison")}</h5>
			</div>
			<div class="ch-compare-sbs-table">
		`;

		// ── Header row: images + names ──
		html += `<div class="ch-sbs-row ch-sbs-header-row">`;
		for (const item of items) {
			html += `
				<div class="ch-sbs-cell" style="width:${colWidth}%">
					<div class="ch-sbs-item-image">
						${item.image ? `<img src="${item.image}" alt="">` : `<i class="fa fa-mobile fa-3x"></i>`}
					</div>
					<div class="ch-sbs-item-brand">${frappe.utils.escape_html(item.brand || "")}</div>
					<div class="ch-sbs-item-name">${frappe.utils.escape_html(item.item_name || item.item_code)}</div>
				</div>`;
		}
		html += `</div>`;

		// ── Price row ──
		html += this._sbs_row(__("Price"), items.map((i) => {
			if (!i.min_price) return `<span class="ch-sbs-na">${__("N/A")}</span>`;
			return i.min_price === i.max_price
				? `<strong>₹${format_number(i.min_price)}</strong>`
				: `₹${format_number(i.min_price)} — ₹${format_number(i.max_price)}`;
		}), colWidth);

		// ── Stock row ──
		html += this._sbs_row(__("Stock"), items.map((i) => {
			const cls = i.stock > 0 ? "in-stock" : "no-stock";
			return `<span class="ch-compare-card-stock ${cls}">
				<i class="fa ${i.stock > 0 ? "fa-check-circle" : "fa-times-circle"}"></i>
				${i.stock > 0 ? `${i.stock} ${__("units")}` : __("Out of stock")}
			</span>`;
		}), colWidth);

		// ── Spec rows ──
		if (allSpecs.size) {
			html += `<div class="ch-sbs-section-label">${__("Specifications")}</div>`;
			for (const spec of allSpecs) {
				html += this._sbs_row(spec, items.map((i) => {
					const val = (i.specs || {})[spec];
					return val ? frappe.utils.escape_html(val) : `<span class="ch-sbs-na">—</span>`;
				}), colWidth);
			}
		}

		// ── Feature rows by group ──
		for (const [group, featureNames] of allFeatureGroups) {
			html += `<div class="ch-sbs-section-label">${frappe.utils.escape_html(group)}</div>`;
			for (const fname of featureNames) {
				html += this._sbs_row(fname, items.map((i) => {
					const groupFeatures = (i.features || {})[group] || [];
					const match = groupFeatures.find((f) => f.feature === fname);
					return match ? frappe.utils.escape_html(match.value) : `<span class="ch-sbs-na">—</span>`;
				}), colWidth);
			}
		}

		// ── Offers section ──
		html += `<div class="ch-sbs-section-label">${__("Offers & Schemes")}</div>`;

		// Brand offers
		html += this._sbs_row(__("Brand Offers"), items.map((i) =>
			this._render_offer_list(i.brand_offers, "brand-offer")
		), colWidth);

		// Bank offers
		html += this._sbs_row(__("Bank Offers"), items.map((i) =>
			this._render_offer_list(i.bank_offers, "bank-offer")
		), colWidth);

		// Other offers
		html += this._sbs_row(__("Other Offers"), items.map((i) =>
			this._render_offer_list(i.other_offers, "other-offer")
		), colWidth);

		html += `</div>`; // end sbs-table
		sbs.html(html);
	}

	_sbs_row(label, values, colWidth) {
		let html = `<div class="ch-sbs-row">
			<div class="ch-sbs-label">${label}</div>
			<div class="ch-sbs-values">`;
		for (const val of values) {
			html += `<div class="ch-sbs-cell" style="width:${colWidth}%">${val}</div>`;
		}
		html += `</div></div>`;
		return html;
	}

	_render_offer_list(offers, cls) {
		if (!offers || !offers.length) {
			return `<span class="ch-sbs-na">${__("None")}</span>`;
		}
		let html = "";
		for (const o of offers) {
			const valText = o.value_type === "Percentage"
				? `${o.value}% off`
				: `₹${format_number(o.value)} off`;
			let detail = "";
			if (o.bank_name) detail += ` · ${frappe.utils.escape_html(o.bank_name)}`;
			if (o.card_type) detail += ` (${frappe.utils.escape_html(o.card_type)})`;
			html += `
				<div class="ch-sbs-offer ${cls}">
					<strong>${frappe.utils.escape_html(o.offer_name)}</strong>
					<span class="ch-sbs-offer-val">${valText}${detail}</span>
				</div>`;
		}
		return html;
	}
}
