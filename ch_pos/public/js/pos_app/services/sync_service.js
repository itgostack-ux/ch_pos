/**
 * CH POS — Sync Service v2 (Offline Manager)
 *
 * IndexedDB stores:
 *   items            — full item catalog (keyed by item_code)
 *   pending_invoices — queued offline invoices (autoIncrement id)
 *   profile          — POS profile snapshot
 *   customers        — customer catalog for offline lookup
 *
 * New in v2:
 *   preload_catalog()       — fetches ALL items paginated at session open
 *   preload_customers()     — fetches recent customers for offline search
 *   get_cached_customers()  — offline customer typeahead
 *   Background Sync API     — registers pos-invoice-sync tag after queuing
 */
import { PosState, EventBus } from "../state.js";

const DB_NAME    = "ch_pos_offline";
const DB_VERSION = 3;        // bumped to force-clear stale items cached before lifecycle filter (Active/Obsolete only)

export class SyncService {
	constructor() {
		this.db       = null;
		this._syncing = false;
		this._warming = false;
		this._init_db();
		this._bind_events();
	}

	// ── IndexedDB Setup ─────────────────────────────────────────────────────

	_init_db() {
		return new Promise((resolve, reject) => {
			const request = indexedDB.open(DB_NAME, DB_VERSION);

			request.onupgradeneeded = (e) => {
				const db = e.target.result;
				const old = e.oldVersion;

				if (old < 1) {
					const items = db.createObjectStore("items", { keyPath: "item_code" });
					items.createIndex("item_group", "item_group", { unique: false });
					items.createIndex("item_name",  "item_name",  { unique: false });

					db.createObjectStore("pending_invoices", { keyPath: "id", autoIncrement: true });
					db.createObjectStore("profile",   { keyPath: "key" });
				}
				if (old < 2) {
					// v2: customers store
					if (!db.objectStoreNames.contains("customers")) {
						const cust = db.createObjectStore("customers", { keyPath: "name" });
						cust.createIndex("customer_name", "customer_name", { unique: false });
						cust.createIndex("mobile_no",     "mobile_no",     { unique: false });
					}
					// v2: catalog_meta — tracks last warm timestamp and item count
					if (!db.objectStoreNames.contains("catalog_meta")) {
						db.createObjectStore("catalog_meta", { keyPath: "key" });
					}
				}
				if (old < 3) {
					// v3: server-side lifecycle filter now hides Draft/Pending Review/Blocked items.
					// Wipe stale cached items + reset warm marker so the next session triggers a re-warm.
					if (db.objectStoreNames.contains("items")) {
						e.target.transaction.objectStore("items").clear();
					}
					if (db.objectStoreNames.contains("catalog_meta")) {
						e.target.transaction.objectStore("catalog_meta").clear();
					}
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

	// ── Event Bindings ──────────────────────────────────────────────────────

	_bind_events() {
		EventBus.on("sync:cache_items",      (items)   => this.cache_items(items));
		EventBus.on("sync:get_cached_items", (opts)    => {
			this.get_cached_items(opts.search_term, opts.filters).then(opts.callback);
		});
		EventBus.on("sync:queue_invoice",    (opts)    => {
			this.queue_invoice(opts.data).then(opts.callback);
		});
		EventBus.on("sync:retry",            ()        => this.sync_pending());
		EventBus.on("sync:start",            ()        => this.sync_pending());
		EventBus.on("profile:loaded",        ()        => {
			this.cache_profile_data();
			// Kick off background catalog warm after session loads
			setTimeout(() => this.preload_catalog(), 3000);
		});
		EventBus.on("sync:get_cached_customers", (opts) => {
			this.get_cached_customers(opts.search_term).then(opts.callback);
		});
	}

	// ── Cache Items ─────────────────────────────────────────────────────────

	cache_items(items) {
		if (!items || !items.length) return;
		this._get_db().then((db) => {
			const tx    = db.transaction("items", "readwrite");
			const store = tx.objectStore("items");
			items.forEach((item) => store.put(item));
		});
	}

	get_cached_items(search_term, filters) {
		return this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx      = db.transaction("items", "readonly");
				const store   = tx.objectStore("items");
				const request = store.getAll();
				request.onsuccess = () => {
					let items = request.result || [];
					if (filters && filters.item_group) {
						items = items.filter((i) => i.item_group === filters.item_group);
					}
					if (filters && filters.in_stock_only) {
						items = items.filter((i) => flt(i.actual_qty) > 0);
					}
					if (search_term) {
						const q = search_term.toLowerCase();
						items = items.filter((i) =>
							(i.item_name || "").toLowerCase().includes(q) ||
							(i.item_code || "").toLowerCase().includes(q) ||
							(i.barcode   || "").toLowerCase().includes(q)
						);
					}
					resolve({ items: items.slice(0, 40), total: items.length });
				};
				request.onerror = () => resolve({ items: [], total: 0 });
			});
		}).catch(() => ({ items: [], total: 0 }));
	}

	// ── Proactive Catalog Pre-Warm ──────────────────────────────────────────

	/**
	 * Load the ENTIRE item catalog into IndexedDB in batches of 200.
	 * Called automatically 3 s after session open, and on demand.
	 * Skips if already warmed within the last 6 hours.
	 */
	async preload_catalog() {
		if (this._warming) return;
		if (!navigator.onLine) return;

		const stale = await this._is_catalog_stale();
		if (!stale) return;

		this._warming = true;
		EventBus.emit("sync:catalog_warm_start");
		console.log("[CH POS] Warming item catalog for offline use…");

		try {
			let page       = 0;
			const page_size = 200;
			let total_loaded = 0;
			let has_more   = true;

			while (has_more && navigator.onLine) {
				const result = await new Promise((resolve) => {
					frappe.call({
						method: "ch_pos.api.offline_sync.get_full_item_catalog",
						args: {
							pos_profile: PosState.pos_profile,
							company:     PosState.active_company || "",
							page,
							page_size,
						},
						callback: (r) => resolve(r.message || {}),
						error:    () => resolve({}),
					});
				});

				const items = result.items || [];
				if (items.length) {
					this.cache_items(items);
					total_loaded += items.length;
				}
				has_more = result.has_more || false;
				page++;
			}

			await this._save_catalog_meta(total_loaded);
			EventBus.emit("sync:catalog_warm_done", { total: total_loaded });
			console.log(`[CH POS] Catalog warm complete — ${total_loaded} items cached`);
		} catch (err) {
			console.warn("[CH POS] Catalog warm failed:", err);
		} finally {
			this._warming = false;
		}
	}

	async _is_catalog_stale() {
		return this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx      = db.transaction("catalog_meta", "readonly");
				const store   = tx.objectStore("catalog_meta");
				const request = store.get("last_warm");
				request.onsuccess = () => {
					const meta = request.result;
					if (!meta) return resolve(true);
					const six_hours = 6 * 60 * 60 * 1000;
					resolve(Date.now() - meta.ts > six_hours);
				};
				request.onerror = () => resolve(true);
			});
		}).catch(() => true);
	}

	async _save_catalog_meta(item_count) {
		return this._get_db().then((db) => {
			const tx    = db.transaction("catalog_meta", "readwrite");
			const store = tx.objectStore("catalog_meta");
			store.put({ key: "last_warm", ts: Date.now(), item_count });
		}).catch(() => {});
	}

	// ── Customer Cache ──────────────────────────────────────────────────────

	/** Called after session open to cache the most recent 500 customers. */
	async preload_customers() {
		if (!navigator.onLine) return;
		const customers = await new Promise((resolve) => {
			frappe.call({
				method: "ch_pos.api.offline_sync.get_customer_catalog",
				args: { limit: 500 },
				callback: (r) => resolve((r.message || {}).customers || []),
				error:    () => resolve([]),
			});
		});
		if (!customers.length) return;
		const db   = await this._get_db();
		const tx   = db.transaction("customers", "readwrite");
		const store = tx.objectStore("customers");
		customers.forEach((c) => store.put(c));
		console.log(`[CH POS] Cached ${customers.length} customers for offline search`);
	}

	cache_customers(customers) {
		if (!customers || !customers.length) return;
		this._get_db().then((db) => {
			const tx    = db.transaction("customers", "readwrite");
			const store = tx.objectStore("customers");
			customers.forEach((c) => store.put(c));
		});
	}

	get_cached_customers(search_term) {
		return this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx      = db.transaction("customers", "readonly");
				const store   = tx.objectStore("customers");
				const request = store.getAll();
				request.onsuccess = () => {
					let custs = request.result || [];
					if (search_term) {
						const q = search_term.toLowerCase();
						custs = custs.filter((c) =>
							(c.customer_name || "").toLowerCase().includes(q) ||
							(c.mobile_no     || "").toLowerCase().includes(q) ||
							(c.name          || "").toLowerCase().includes(q)
						);
					}
					resolve(custs.slice(0, 20));
				};
				request.onerror = () => resolve([]);
			});
		}).catch(() => []);
	}

	// ── Cache Profile ───────────────────────────────────────────────────────

	cache_profile_data() {
		this._get_db().then((db) => {
			const tx    = db.transaction("profile", "readwrite");
			const store = tx.objectStore("profile");
			store.put({
				key:          "pos_profile",
				pos_profile:  PosState.pos_profile,
				company:      PosState.company,
				warehouse:    PosState.warehouse,
				price_list:   PosState.price_list,
				payment_modes: PosState.payment_modes,
				store_caps:   PosState.store_caps,
				pos_ext:      PosState.pos_ext,
			});
		});
	}

	get_cached_profile() {
		return this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx      = db.transaction("profile", "readonly");
				const store   = tx.objectStore("profile");
				const request = store.get("pos_profile");
				request.onsuccess = () => resolve(request.result || null);
				request.onerror   = () => resolve(null);
			});
		}).catch(() => null);
	}

	// ── Queue Invoice ───────────────────────────────────────────────────────

	queue_invoice(invoice_data) {
		// Attach a client-generated idempotency key so the server can dedupe
		// in case the request is retried after a partial failure.
		if (!invoice_data.client_id) {
			invoice_data.client_id = `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
		}

		return this._get_db().then((db) => {
			return new Promise((resolve, reject) => {
				const tx    = db.transaction("pending_invoices", "readwrite");
				const store = tx.objectStore("pending_invoices");
				const record = {
					data:       invoice_data,
					created_at: new Date().toISOString(),
					status:     "pending",
					attempts:   0,
				};
				const request = store.add(record);
				request.onsuccess = () => {
					this._update_sync_badge();
					// Request a background sync so the SW can flush even if this
					// tab is closed before connectivity returns.
					this._register_bg_sync();
					resolve(request.result);
				};
				request.onerror = () => reject(request.error);
			});
		});
	}

	_register_bg_sync() {
		if (!("serviceWorker" in navigator)) return;
		navigator.serviceWorker.ready.then((reg) => {
			if (reg.sync) {
				reg.sync.register("pos-invoice-sync").catch(() => {});
			}
		}).catch(() => {});
	}

	// ── Sync Pending ────────────────────────────────────────────────────────

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
				const tx      = db.transaction("pending_invoices", "readonly");
				const store   = tx.objectStore("pending_invoices");
				const request = store.getAll();
				request.onsuccess = () => resolve(request.result || []);
				request.onerror   = () => resolve([]);
			});
		}).then((pending) => this._sync_batch(pending))
		  .then(() => {
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
					method:   "ch_pos.api.offline_sync.create_pos_invoice_offline",
					args:     item.data,
					callback: (r) => {
						if (r.message) {
							this._remove_pending(item.id).then(() => {
								frappe.show_alert({
									message:   __("Synced: {0}", [r.message.name]),
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
					method:   "ch_pos.api.pos_api.create_pos_return",
					args: {
						original_invoice: item.data.product_exchange_invoice,
						return_items:     item.data.return_items,
						// See payment_dialog.js: Product Exchange flow does not collect a
						// manual remark, so supply a deterministic default that satisfies
						// the compliance check on create_pos_return.
						return_reason:    item.data.return_reason  || "Product Exchange",
						return_remarks:   item.data.return_remarks || "Product exchange — old device returned for credit applied to new purchase.",
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
				const tx    = db.transaction("pending_invoices", "readwrite");
				const store = tx.objectStore("pending_invoices");
				store.delete(id);
				tx.oncomplete = () => resolve();
			});
		});
	}

	_get_pending_count() {
		return this._get_db().then((db) => {
			return new Promise((resolve) => {
				const tx      = db.transaction("pending_invoices", "readonly");
				const store   = tx.objectStore("pending_invoices");
				const request = store.count();
				request.onsuccess = () => resolve(request.result);
				request.onerror   = () => resolve(0);
			});
		}).catch(() => 0);
	}

	_update_sync_badge() {
		this._get_pending_count().then((count) => {
			EventBus.emit("sync:badge_count", count);
		});
	}
}
