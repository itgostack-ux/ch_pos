/**
 * CH POS — Sync Service (Offline Manager)
 *
 * IndexedDB-based offline cache and invoice queue.
 * Stores: items, pending_invoices, profile, customers.
 * Auto-syncs when coming back online.
 */
import { PosState, EventBus } from "../state.js";

export class SyncService {
	constructor() {
		this.db = null;
		this.DB_NAME = "ch_pos_offline";
		this.DB_VERSION = 1;
		this._syncing = false;
		this._init_db();
		this._bind_events();
	}

	// ── IndexedDB Setup ─────────────────────────────────
	_init_db() {
		return new Promise((resolve, reject) => {
			const request = indexedDB.open(this.DB_NAME, this.DB_VERSION);
			request.onupgradeneeded = (e) => {
				const db = e.target.result;
				if (!db.objectStoreNames.contains("items")) {
					const store = db.createObjectStore("items", { keyPath: "item_code" });
					store.createIndex("item_group", "item_group", { unique: false });
					store.createIndex("item_name", "item_name", { unique: false });
				}
				if (!db.objectStoreNames.contains("pending_invoices")) {
					db.createObjectStore("pending_invoices", { keyPath: "id", autoIncrement: true });
				}
				if (!db.objectStoreNames.contains("profile")) {
					db.createObjectStore("profile", { keyPath: "key" });
				}
				if (!db.objectStoreNames.contains("customers")) {
					db.createObjectStore("customers", { keyPath: "name" });
				}
			};
			request.onsuccess = (e) => {
				this.db = e.target.result;
				resolve(this.db);
			};
			request.onerror = (e) => {
				console.error("CH POS: IndexedDB init failed", e);
				reject(e);
			};
		});
	}

	_get_db() {
		if (this.db) return Promise.resolve(this.db);
		return this._init_db();
	}

	// ── Event Bindings ──────────────────────────────────
	_bind_events() {
		// Cache items from API response
		EventBus.on("sync:cache_items", (items) => this.cache_items(items));

		// Get cached items for offline search
		EventBus.on("sync:get_cached_items", (opts) => {
			this.get_cached_items(opts.search_term, opts.filters).then(opts.callback);
		});

		// Queue invoice for offline submission
		EventBus.on("sync:queue_invoice", (opts) => {
			this.queue_invoice(opts.data).then(opts.callback);
		});

		// Manual retry
		EventBus.on("sync:retry", () => this.sync_pending());
		EventBus.on("sync:start", () => this.sync_pending());

		// Cache profile data after load
		EventBus.on("profile:loaded", () => this.cache_profile_data());
	}

	// ── Cache Items ─────────────────────────────────────
	cache_items(items) {
		if (!items || !items.length) return;
		this._get_db().then((db) => {
			const tx = db.transaction("items", "readwrite");
			const store = tx.objectStore("items");
			items.forEach((item) => store.put(item));
		});
	}

	get_cached_items(search_term, filters) {
		return this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx = db.transaction("items", "readonly");
				const store = tx.objectStore("items");
				const request = store.getAll();
				request.onsuccess = () => {
					let items = request.result || [];
					if (filters.item_group) {
						items = items.filter((i) => i.item_group === filters.item_group);
					}
					if (filters.in_stock_only) {
						items = items.filter((i) => flt(i.actual_qty) > 0);
					}
					if (search_term) {
						const q = search_term.toLowerCase();
						items = items.filter((i) =>
							(i.item_name || "").toLowerCase().includes(q) ||
							(i.item_code || "").toLowerCase().includes(q) ||
							(i.barcode || "").toLowerCase().includes(q)
						);
					}
					resolve({ items: items.slice(0, 40), total: items.length });
				};
				request.onerror = () => resolve({ items: [], total: 0 });
			});
		}).catch(() => ({ items: [], total: 0 }));
	}

	// ── Cache Profile ───────────────────────────────────
	cache_profile_data() {
		this._get_db().then((db) => {
			const tx = db.transaction("profile", "readwrite");
			const store = tx.objectStore("profile");
			store.put({
				key: "pos_profile",
				pos_profile: PosState.pos_profile,
				company: PosState.company,
				warehouse: PosState.warehouse,
				price_list: PosState.price_list,
				payment_modes: PosState.payment_modes,
				store_caps: PosState.store_caps,
				pos_ext: PosState.pos_ext,
			});
		});
	}

	// ── Queue Invoice ───────────────────────────────────
	queue_invoice(invoice_data) {
		return this._get_db().then((db) => {
			return new Promise((resolve, reject) => {
				const tx = db.transaction("pending_invoices", "readwrite");
				const store = tx.objectStore("pending_invoices");
				const record = {
					data: invoice_data,
					created_at: new Date().toISOString(),
					status: "pending",
				};
				const request = store.add(record);
				request.onsuccess = () => {
					this._update_sync_badge();
					resolve(request.result);
				};
				request.onerror = () => reject(request.error);
			});
		});
	}

	// ── Sync Pending ────────────────────────────────────
	sync_pending() {
		if (this._syncing || !navigator.onLine) return;
		this._syncing = true;

		this._get_pending_count().then((count) => {
			if (count > 0) {
				EventBus.emit("sync:state", { status: "syncing", queue_count: count });
			}
		});

		this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx = db.transaction("pending_invoices", "readonly");
				const store = tx.objectStore("pending_invoices");
				const request = store.getAll();
				request.onsuccess = () => resolve(request.result || []);
				request.onerror = () => resolve([]);
			});
		}).then((pending) => {
			return this._sync_batch(pending);
		}).then(() => {
			this._syncing = false;
			EventBus.emit("sync:state", { status: "done", queue_count: 0 });
			this._update_sync_badge();
		}).catch((err) => {
			console.error("CH POS: Sync failed", err);
			this._syncing = false;
			EventBus.emit("sync:state", { status: "error", queue_count: 0 });
			setTimeout(() => this.sync_pending(), 30000);
		});
	}

	_sync_batch(pending) {
		if (!pending.length) return Promise.resolve();
		const item = pending.shift();

		const do_create = () => {
			return new Promise((resolve, reject) => {
				frappe.call({
					method: "ch_pos.api.pos_api.create_pos_invoice",
					args: item.data,
					callback: (r) => {
						if (r.message) {
							this._remove_pending(item.id).then(() => {
								frappe.show_alert({
									message: __("Synced: {0}", [r.message.name]),
									indicator: "green",
								});
								resolve();
							});
						} else {
							reject(new Error("Invoice creation returned no result"));
						}
					},
					error: () => reject(new Error("API call failed")),
				});
			});
		};

		let chain;
		if (item.data.product_exchange_invoice && item.data.return_items) {
			chain = new Promise((resolve, reject) => {
				frappe.call({
					method: "ch_pos.api.pos_api.create_pos_return",
					args: {
						original_invoice: item.data.product_exchange_invoice,
						return_items: item.data.return_items,
					},
					callback: (r) => {
						if (r.message) resolve();
						else reject(new Error("Return creation failed"));
					},
					error: () => reject(new Error("Return API failed")),
				});
			}).then(do_create);
		} else {
			chain = do_create();
		}

		return chain.then(() => this._sync_batch(pending));
	}

	_remove_pending(id) {
		return this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx = db.transaction("pending_invoices", "readwrite");
				const store = tx.objectStore("pending_invoices");
				store.delete(id);
				tx.oncomplete = () => resolve();
			});
		});
	}

	_get_pending_count() {
		return this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx = db.transaction("pending_invoices", "readonly");
				const store = tx.objectStore("pending_invoices");
				const request = store.count();
				request.onsuccess = () => resolve(request.result);
				request.onerror = () => resolve(0);
			});
		}).catch(() => 0);
	}

	_update_sync_badge() {
		this._get_pending_count().then((count) => {
			EventBus.emit("sync:badge_count", count);
		});
	}
}
