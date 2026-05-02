/**
 * CH POS — Item Service
 *
 * API layer for item search, loading, and caching.
 * Wraps ch_pos.api.search.pos_item_search and provides
 * offline fallback via SyncService's IndexedDB.
 */
import { PosState, EventBus } from "../state.js";

export class ItemService {
	constructor() {
		this._bind_events();
	}

	_bind_events() {
		EventBus.on("items:reload", () => this.load_items());
		EventBus.on("company:switched", () => this.load_items());
	}

	/**
	 * Load items from server (or offline cache as fallback).
	 * Stores results in PosState and emits items:loaded.
	 */
	load_items() {
		const filters = {};
		if (PosState.item_group_filter) {
			filters.item_group = PosState.item_group_filter;
		}
		if (PosState.brand_filter) {
			filters.brand = PosState.brand_filter;
		}
		if (PosState.in_stock_only) {
			filters.in_stock_only = 1;
		}

		if (!navigator.onLine) {
			this._load_offline(filters);
			return;
		}

		frappe.call({
			method: "ch_pos.api.search.pos_item_search",
			args: {
				search_term: PosState.search_term || "",
				pos_profile: PosState.pos_profile,
				filters: filters,
				page: PosState.item_page,
				page_size: PosState.item_page_size,
				company: PosState.active_company || "",
				usage_context: PosState.active_mode === "repair" || PosState.active_mode === "service"
					? "repair" : "sale",
			},
			callback: (r) => {
				if (r.message) {
					PosState.last_items = r.message.items || [];
					PosState.total_items = r.message.total || 0;
					EventBus.emit("items:loaded", {
						items: PosState.last_items,
						total: PosState.total_items,
					});
					// Cache for offline use
					EventBus.emit("sync:cache_items", PosState.last_items);
				}
			},
		});
	}

	/** Offline fallback — read from IndexedDB via SyncService */
	_load_offline(filters) {
		EventBus.emit("sync:get_cached_items", {
			search_term: PosState.search_term,
			filters: filters,
			callback: (result) => {
				PosState.last_items = result.items || [];
				PosState.total_items = result.total || 0;
				EventBus.emit("items:loaded", {
					items: PosState.last_items,
					total: PosState.total_items,
				});
			},
		});
	}
}
