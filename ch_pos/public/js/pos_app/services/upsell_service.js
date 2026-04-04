/**
 * CH POS — AI Upsell Service
 *
 * Listens for new items added to cart and shows AI-powered upsell
 * suggestions (accessories, protection plans, upgrades).
 * Falls back to rule-based suggestions if AI is unavailable.
 */
import { PosState, EventBus } from "../state.js";
import { format_number } from "../shared/helpers.js";

const TYPE_ICONS = {
	"Accessory": "🔌",
	"Protection Plan": "🛡️",
	"Upgrade": "⬆️",
	"Warranty": "🛡️",
};

const TYPE_COLORS = {
	"Accessory": "#e8f5e9",
	"Protection Plan": "#e3f2fd",
	"Upgrade": "#fff3e0",
	"Warranty": "#e3f2fd",
};

export class UpsellService {
	constructor() {
		this._pending = false;
		this._bind_events();
	}

	_bind_events() {
		EventBus.on("cart:item_added", ({ item_data, cart_item }) => {
			// Skip free/bundle items, warranty items, zero-rate items
			if (cart_item.is_free_bundle_item || cart_item.is_warranty || cart_item.is_vas) return;
			if (cart_item.ch_allow_zero_rate && cart_item.rate === 0) return;

			// Check if AI upsell is enabled for this profile
			if (!PosState.pos_ext?.enable_ai_upsell) return;

			// Debounce: don't fire multiple upsell panels at once
			if (this._pending) return;
			this._pending = true;

			this._fetch_and_show(item_data, cart_item).finally(() => {
				this._pending = false;
			});
		});
	}

	async _fetch_and_show(item_data, cart_item) {
		try {
			// Collect current cart item codes (excluding the just-added one)
			const cart_codes = PosState.cart
				.filter(c => c.item_code !== cart_item.item_code && !c.is_warranty && !c.is_vas)
				.map(c => c.item_code);

			const r = await frappe.xcall("ch_pos.api.ai.get_upsell_suggestions", {
				item_code: cart_item.item_code,
				cart_items: cart_codes,
			});

			if (!r || !r.length) return;

			this._show_upsell_panel(r, item_data, cart_item);
		} catch (e) {
			console.warn("Upsell fetch failed:", e);
		}
	}

	_show_upsell_panel(suggestions, item_data, cart_item) {
		const item_name = frappe.utils.escape_html(cart_item.item_name);
		const sales_tip = suggestions[0]?.sales_tip || "";
		const source = suggestions[0]?.source || "Rule";

		let html = `<div class="ch-upsell-panel">`;

		// Sales coaching tip (from AI)
		if (sales_tip) {
			html += `<div class="ch-upsell-tip">
				<span class="ch-upsell-tip-icon">💡</span>
				<span class="ch-upsell-tip-text">${frappe.utils.escape_html(sales_tip)}</span>
			</div>`;
		}

		html += `<p class="text-muted" style="margin:0 0 12px 0;font-size:12px">
			${__("Recommended for")} <b>${item_name}</b>
			<span class="badge badge-light" style="font-size:10px;margin-left:4px">${source === "AI" ? "✨ AI" : "⚡ Smart"}</span>
		</p>`;

		// Suggestion rows
		suggestions.forEach((s, i) => {
			const icon = TYPE_ICONS[s.type] || "📦";
			const bg = TYPE_COLORS[s.type] || "#f5f5f5";
			const price_str = s.price ? `₹${format_number(s.price)}` : "";
			const reason = frappe.utils.escape_html(s.reason || "");
			const priority_badge = s.priority === 1
				? `<span class="badge" style="background:#ff9800;color:#fff;font-size:9px;margin-left:4px">${__("Must-Have")}</span>`
				: "";

			html += `<div class="ch-upsell-row" style="background:${bg}" data-idx="${i}">
				<div class="ch-upsell-row-main">
					<span class="ch-upsell-icon">${icon}</span>
					<div class="ch-upsell-info">
						<div class="ch-upsell-name">
							${frappe.utils.escape_html(s.item_name)}${priority_badge}
						</div>
						<div class="ch-upsell-reason">${reason}</div>
					</div>
					<div class="ch-upsell-price">${price_str}</div>
					<button class="btn btn-xs btn-primary ch-upsell-add" data-idx="${i}">
						${__("Add")}
					</button>
				</div>
			</div>`;
		});

		html += `</div>`;

		const dialog = new frappe.ui.Dialog({
			title: __("Suggested for You — {0}", [item_name]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "upsell_panel",
					options: html,
				},
			],
			size: "large",
			primary_action_label: __("Done"),
			primary_action: () => dialog.hide(),
			secondary_action_label: __("Skip All"),
			secondary_action: () => dialog.hide(),
		});

		// Style the panel
		dialog.$wrapper.find(".ch-upsell-panel").parent().css("padding", "0");
		this._inject_styles(dialog);

		// Bind add buttons
		dialog.$wrapper.on("click", ".ch-upsell-add", (e) => {
			const idx = $(e.currentTarget).data("idx");
			const s = suggestions[idx];
			if (!s) return;

			const $btn = $(e.currentTarget);
			if ($btn.hasClass("disabled")) return;

			if (s.type === "Protection Plan" || s.type === "Warranty") {
				// Add as warranty/plan item
				PosState.cart.push({
					item_code: s.item_code,
					item_name: s.item_name,
					qty: 1,
					rate: flt(s.price),
					mrp: flt(s.price),
					uom: "Nos",
					discount_percentage: 0,
					discount_amount: 0,
					offers: [],
					applied_offer: null,
					warranty_plan: s.item_code,
					is_warranty: true,
					is_vas: false,
					has_serial_no: 0,
					serial_no: "",
					for_item_code: cart_item.item_code,
					for_serial_no: cart_item.serial_no || "",
					ch_item_type: "Plan",
					ch_allow_zero_rate: 0,
					stock_qty: 999,
					must_be_whole_number: 1,
				});
			} else {
				// Regular item (accessory / upgrade)
				PosState.cart.push({
					item_code: s.item_code,
					item_name: s.item_name,
					qty: 1,
					rate: flt(s.price),
					mrp: flt(s.price),
					uom: "Nos",
					discount_percentage: 0,
					discount_amount: 0,
					offers: [],
					applied_offer: null,
					warranty_plan: null,
					is_warranty: false,
					is_vas: false,
					has_serial_no: 0,
					serial_no: "",
					ch_item_type: "",
					ch_allow_zero_rate: 0,
					stock_qty: 0,
					must_be_whole_number: 1,
				});
			}

			EventBus.emit("cart:updated");

			// Visual feedback
			$btn.removeClass("btn-primary").addClass("btn-success disabled").html("✓ " + __("Added"));
			$(e.currentTarget).closest(".ch-upsell-row").css("opacity", "0.6");

			frappe.show_alert({
				message: __("{0} added to cart", [frappe.utils.escape_html(s.item_name)]),
				indicator: "green",
			});
		});

		dialog.show();
	}

	_inject_styles(dialog) {
		if (dialog.$wrapper.find(".ch-upsell-styles").length) return;
		dialog.$wrapper.append(`<style class="ch-upsell-styles">
			.ch-upsell-panel { padding: 16px; }

			.ch-upsell-tip {
				display: flex;
				align-items: flex-start;
				gap: 8px;
				padding: 10px 14px;
				background: linear-gradient(135deg, #fff8e1, #fff3e0);
				border: 1px solid #ffe0b2;
				border-radius: 8px;
				margin-bottom: 14px;
				font-size: 13px;
				line-height: 1.5;
			}
			.ch-upsell-tip-icon { font-size: 18px; flex-shrink: 0; margin-top: 1px; }
			.ch-upsell-tip-text { color: #e65100; font-weight: 500; }

			.ch-upsell-row {
				border-radius: 8px;
				padding: 12px 14px;
				margin-bottom: 8px;
				border: 1px solid rgba(0,0,0,0.06);
				transition: opacity 0.3s ease;
			}
			.ch-upsell-row-main {
				display: flex;
				align-items: center;
				gap: 12px;
			}
			.ch-upsell-icon { font-size: 22px; flex-shrink: 0; }
			.ch-upsell-info { flex: 1; min-width: 0; }
			.ch-upsell-name {
				font-weight: 600;
				font-size: 13px;
				color: #1a1a2e;
				white-space: nowrap;
				overflow: hidden;
				text-overflow: ellipsis;
			}
			.ch-upsell-reason {
				font-size: 12px;
				color: #666;
				margin-top: 2px;
				line-height: 1.4;
			}
			.ch-upsell-price {
				font-weight: 700;
				font-size: 14px;
				color: #1a1a2e;
				white-space: nowrap;
				flex-shrink: 0;
			}
			.ch-upsell-add {
				flex-shrink: 0;
				min-width: 60px;
				font-weight: 600;
			}

			@media (max-width: 768px) {
				.ch-upsell-row-main { flex-wrap: wrap; }
				.ch-upsell-info { width: calc(100% - 50px); }
				.ch-upsell-price { margin-left: 34px; }
				.ch-upsell-add { margin-left: auto; }
			}
		</style>`);
	}
}
