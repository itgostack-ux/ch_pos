/**
 * CH POS — Bin Manager Workspace
 *
 * Destination-first bin operations for store users:
 *  - One pill per bin type with live counts and color coding
 *  - Scan IMEI / Serial → auto-detect current bin
 *  - Move to selected destination bin with a mandatory reason
 *  - Live table of serials currently in the selected bin
 */
import { PosState, EventBus } from "../../state.js";

const BIN_TYPES = ["Sellable", "In-Transit", "Damaged", "Disposed", "Reserved"];

// Visual config per bin type (theme tokens used by ch-pos styles)
const BIN_META = {
	"Sellable":   { icon: "fa-check-circle",   tone: "success", color: "#1f8f5f", bg: "#e8f7ef", hint: __("Available for sale on the shop floor") },
	"In-Transit": { icon: "fa-truck",          tone: "info",    color: "#0b6bcb", bg: "#e7f1ff", hint: __("In-flight between store and zone hub") },
	"Damaged":    { icon: "fa-wrench",         tone: "warning", color: "#b45309", bg: "#fef3c7", hint: __("Awaiting inspection or repair") },
	"Disposed":   { icon: "fa-trash",          tone: "danger",  color: "#b91c1c", bg: "#fee2e2", hint: __("Scrapped / written off — locked") },
	"Reserved":   { icon: "fa-bookmark",       tone: "purple",  color: "#6d28d9", bg: "#ede9fe", hint: __("Held against bookings / customer orders") },
};

export class BinManagerWorkspace {
	constructor() {
		this._active_bin = "Sellable";
		this._serial_ctx = null;
		this._reasons = [];
		this._counts = {};
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "bin_manager") return;
			this.render(ctx.panel);
		});
	}

	render(panel) {
		this.panel = panel;
		this._serial_ctx = null;

		const tabs_html = BIN_TYPES.map((bin) => {
			const meta = BIN_META[bin];
			const active = bin === this._active_bin ? "active" : "";
			return `
				<button class="ch-bm-tab ${active}" data-bin="${bin}"
					style="--bin-color:${meta.color};--bin-bg:${meta.bg}">
					<span class="ch-bm-tab-icon"><i class="fa ${meta.icon}"></i></span>
					<span class="ch-bm-tab-body">
						<span class="ch-bm-tab-name">${__(bin)}</span>
						<span class="ch-bm-tab-count" data-bin-count="${bin}">—</span>
					</span>
				</button>`;
		}).join("");

		panel.html(`
			<style>
				.ch-bm-wrap{display:flex;flex-direction:column;gap:14px;}
				.ch-bm-tabs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;}
				.ch-bm-tab{display:flex;align-items:center;gap:10px;padding:12px 14px;border:1px solid var(--pos-border,#e5e7eb);background:#fff;border-radius:10px;cursor:pointer;transition:all .15s ease;text-align:left;}
				.ch-bm-tab:hover{border-color:var(--bin-color);box-shadow:0 1px 3px rgba(0,0,0,.06);}
				.ch-bm-tab.active{border-color:var(--bin-color);background:var(--bin-bg);box-shadow:0 2px 6px rgba(0,0,0,.08);}
				.ch-bm-tab-icon{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:var(--bin-bg);color:var(--bin-color);font-size:14px;flex-shrink:0;}
				.ch-bm-tab.active .ch-bm-tab-icon{background:#fff;}
				.ch-bm-tab-body{display:flex;flex-direction:column;line-height:1.15;min-width:0;}
				.ch-bm-tab-name{font-weight:600;color:#111827;font-size:13px;}
				.ch-bm-tab-count{font-size:11px;color:#6b7280;margin-top:2px;}
				.ch-bm-tab.active .ch-bm-tab-count{color:var(--bin-color);font-weight:600;}
				.ch-bm-grid{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1.4fr);gap:14px;align-items:start;}
				.ch-bm-card{background:#fff;border:1px solid var(--pos-border,#e5e7eb);border-radius:10px;overflow:hidden;}
				.ch-bm-card-head{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid #f1f5f9;background:#fafbfc;}
				.ch-bm-card-head h5{margin:0;font-size:13px;font-weight:600;color:#111827;letter-spacing:.2px;}
				.ch-bm-card-head .sub{font-size:11px;color:#6b7280;margin-left:auto;}
				.ch-bm-card-body{padding:14px;}
				.ch-bm-field{display:flex;flex-direction:column;gap:4px;}
				.ch-bm-field > label{font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.4px;}
				.ch-bm-field .form-control{border-radius:6px;}
				.ch-bm-row{display:grid;gap:10px;}
				.ch-bm-row.cols-2{grid-template-columns:1fr 1fr;}
				.ch-bm-bin-pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:600;}
				.ch-bm-context{margin-top:12px;padding:10px 12px;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;font-size:12px;display:none;}
				.ch-bm-context.visible{display:block;}
				.ch-bm-context .ctx-row{display:flex;flex-wrap:wrap;gap:14px;align-items:center;}
				.ch-bm-context .ctx-row b{color:#374151;margin-right:4px;}
				.ch-bm-actions{display:flex;gap:8px;margin-top:10px;}
				.ch-bm-actions .btn{border-radius:6px;font-weight:600;padding:6px 14px;}
				.ch-bm-toolbar{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #f1f5f9;background:#fafbfc;}
				.ch-bm-toolbar .ch-bm-search{flex:1;}
				.ch-bm-list-table{width:100%;border-collapse:collapse;}
				.ch-bm-list-table thead th{position:sticky;top:0;background:#f9fafb;font-size:11px;text-transform:uppercase;letter-spacing:.4px;color:#6b7280;padding:9px 14px;text-align:left;border-bottom:1px solid #e5e7eb;}
				.ch-bm-list-table tbody td{padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:13px;vertical-align:top;}
				.ch-bm-list-table tbody tr:hover{background:#f9fafb;}
				.ch-bm-list-table .mono{font-family:var(--pos-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);font-weight:600;color:#111827;}
				.ch-bm-list-table .item-name{color:#6b7280;font-size:11px;margin-top:2px;}
				.ch-bm-status-badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;font-weight:600;background:#e5e7eb;color:#374151;}
				.ch-bm-empty{padding:30px 14px;text-align:center;color:#6b7280;font-size:13px;}
				.ch-bm-empty .icon{font-size:28px;color:#cbd5e1;margin-bottom:8px;}
				.ch-bm-warehouse{font-family:var(--pos-font-mono);font-size:11px;color:#475569;background:#eef2f7;padding:3px 8px;border-radius:6px;}
				.ch-bm-meta-hint{font-size:11px;color:#94a3b8;margin-top:8px;}
				@media (max-width: 1100px){.ch-bm-grid{grid-template-columns:1fr;}.ch-bm-tabs{grid-template-columns:repeat(2,1fr);}}
			</style>

			<div class="ch-pos-mode-panel ch-bm-wrap">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#e8f7ef;color:#1f8f5f">
							<i class="fa fa-th-large"></i>
						</span>
						${__("Bin Manager")}
					</h4>
					<span class="ch-mode-hint">${__("Scan IMEI, choose reason, and move stock between bins")}</span>
				</div>

				<div class="ch-bm-tabs">${tabs_html}</div>

				<div class="ch-bm-grid">
					<!-- Left: Move action card -->
					<div class="ch-bm-card">
						<div class="ch-bm-card-head">
							<i class="fa fa-exchange" style="color:#0b6bcb"></i>
							<h5>${__("Move Stock To Bin")}</h5>
							<span class="sub" id="ch-bm-dest-pill"></span>
						</div>
						<div class="ch-bm-card-body">
							<div class="ch-bm-row cols-2">
								<div class="ch-bm-field">
									<label>${__("IMEI / Serial")}</label>
									<input type="text" class="form-control ch-bm-serial" placeholder="${__("Scan or type serial...")}" autocomplete="off">
								</div>
								<div class="ch-bm-field ch-bm-item-field"></div>
							</div>

							<div class="ch-bm-row cols-2" style="margin-top:10px;">
								<div class="ch-bm-field">
									<label>${__("From Bin")}</label>
									<input type="text" class="form-control ch-bm-from-bin" readonly placeholder="${__("Will auto-fill after lookup")}">
								</div>
								<div class="ch-bm-field ch-bm-reason-field"></div>
							</div>

							<div class="ch-bm-actions">
								<button class="btn btn-default ch-bm-lookup">
									<i class="fa fa-search"></i> ${__("Lookup")}
								</button>
								<button class="btn btn-primary ch-bm-move">
									<i class="fa fa-arrow-right"></i> ${__("Move to {0}", [this._active_bin])}
								</button>
							</div>

							<div class="ch-bm-context"></div>
							<div class="ch-bm-meta-hint">${BIN_META[this._active_bin].hint}</div>
						</div>
					</div>

					<!-- Right: Bin contents -->
					<div class="ch-bm-card">
						<div class="ch-bm-card-head">
							<i class="fa fa-list" style="color:#475569"></i>
							<h5 class="ch-bm-list-title">${__("Items in {0} Bin", [this._active_bin])}</h5>
							<span class="sub ch-bm-warehouse" data-role="warehouse">—</span>
						</div>
						<div class="ch-bm-toolbar">
							<input type="text" class="form-control input-sm ch-bm-search" placeholder="${__("Filter serial or item...")}">
							<button class="btn btn-default btn-sm ch-bm-refresh" title="${__("Refresh")}">
								<i class="fa fa-refresh"></i>
							</button>
						</div>
						<div class="ch-bm-list" style="max-height:520px;overflow:auto;"></div>
					</div>
				</div>
			</div>
		`);

		this._init_controls(panel);
		this._bind(panel);
		this._update_dest_pill();
		this._load_reasons();
		this._load_counts();
		this._refresh_bin_view();
	}

	_init_controls(panel) {
		this.item_field = frappe.ui.form.make_control({
			df: {
				fieldname: "item_code",
				fieldtype: "Link",
				options: "Item",
				label: __("Item (optional)"),
				placeholder: __("Filter by item"),
				get_query: () => ({ filters: { disabled: 0, is_stock_item: 1 } }),
				onchange: () => this._refresh_bin_view(),
			},
			parent: panel.find(".ch-bm-item-field"),
			render_input: true,
		});

		this.reason_field = frappe.ui.form.make_control({
			df: {
				fieldname: "reason",
				fieldtype: "Select",
				label: __("Reason"),
				options: "",
				reqd: 1,
			},
			parent: panel.find(".ch-bm-reason-field"),
			render_input: true,
		});
	}

	_bind(panel) {
		panel.on("click", ".ch-bm-tab", (e) => {
			const bin = $(e.currentTarget).data("bin");
			if (!bin || bin === this._active_bin) return;
			this._active_bin = bin;
			panel.find(".ch-bm-tab").removeClass("active");
			$(e.currentTarget).addClass("active");
			panel.find(".ch-bm-list-title").text(__("Items in {0} Bin", [bin]));
			panel.find(".ch-bm-meta-hint").text(BIN_META[bin].hint);
			panel.find(".ch-bm-move").html(`<i class="fa fa-arrow-right"></i> ${__("Move to {0}", [bin])}`);
			this._update_dest_pill();
			this._set_reason_options();
			this._refresh_bin_view();
		});

		panel.on("click", ".ch-bm-lookup", () => this._lookup_serial());
		panel.on("keypress", ".ch-bm-serial", (e) => {
			if (e.which === 13) { e.preventDefault(); this._lookup_serial(); }
		});
		panel.on("click", ".ch-bm-move", () => this._move_serial());
		panel.on("click", ".ch-bm-refresh", () => { this._load_counts(); this._refresh_bin_view(); });

		// Client-side row filter
		panel.on("input", ".ch-bm-search", (e) => {
			const q = (e.currentTarget.value || "").toLowerCase().trim();
			panel.find(".ch-bm-list tbody tr").each(function() {
				const txt = $(this).text().toLowerCase();
				$(this).toggle(!q || txt.indexOf(q) !== -1);
			});
		});
	}

	_update_dest_pill() {
		const m = BIN_META[this._active_bin];
		this.panel.find("#ch-bm-dest-pill").html(
			`<span class="ch-bm-bin-pill" style="background:${m.bg};color:${m.color}">
				<i class="fa ${m.icon}"></i> ${__("Destination")}: ${__(this._active_bin)}
			</span>`
		);
	}

	_load_reasons() {
		frappe.call({
			method: "ch_item_master.ch_core.bin_transfer.get_bin_transfer_reasons",
			callback: (r) => {
				this._reasons = r.message || [];
				this._set_reason_options();
			},
		});
	}

	_set_reason_options() {
		const reasons = this._reasons.filter((x) => x.target_bin_type === this._active_bin);
		const opts = [""];
		reasons.forEach((r) => opts.push(r.name));
		this.reason_field.df.options = opts.join("\n");
		this.reason_field.refresh();
		if (reasons.length && !this.reason_field.get_value()) {
			this.reason_field.set_value(reasons[0].name);
		}
	}

	_load_counts() {
		if (!PosState.store) return;
		frappe.call({
			method: "ch_item_master.ch_core.bin_transfer.get_pos_bin_summary",
			args: { store: PosState.store },
			callback: (r) => {
				const list = (r.message && r.message.bins) || [];
				const data = {};
				for (const row of list) {
					data[row.bin_type] = row;
				}
				this._counts = data;
				BIN_TYPES.forEach((bin) => {
					const info = data[bin] || {};
					const qty = info.qty != null ? info.qty : (info.items || 0);
					this.panel.find(`[data-bin-count="${bin}"]`).text(
						qty ? __("{0} units", [qty]) : __("Empty")
					);
				});
			},
		});
	}

	_lookup_serial() {
		const serial_no = (this.panel.find(".ch-bm-serial").val() || "").trim();
		if (!serial_no) {
			frappe.show_alert({ message: __("Enter IMEI / serial"), indicator: "orange" });
			return;
		}

		frappe.call({
			method: "ch_item_master.ch_core.bin_transfer.get_serial_bin_context",
			args: { serial_no, store: PosState.store || null },
			callback: (r) => {
				const d = r.message || null;
				if (!d || !d.serial_no) {
					frappe.show_alert({ message: __("Serial not found in current store bins"), indicator: "red" });
					this._serial_ctx = null;
					this.panel.find(".ch-bm-context").removeClass("visible").empty();
					return;
				}
				this._serial_ctx = d;
				if (!this.item_field.get_value()) {
					this.item_field.set_value(d.item_code || "");
				}
				this.panel.find(".ch-bm-from-bin").val(d.bin_type || "");
				const src = BIN_META[d.bin_type] || BIN_META["Sellable"];
				const dst = BIN_META[this._active_bin];
				this.panel.find(".ch-bm-context").addClass("visible").html(`
					<div class="ctx-row">
						<span><b>${__("Serial")}:</b> <span class="mono">${frappe.utils.escape_html(d.serial_no)}</span></span>
						<span><b>${__("Item")}:</b> ${frappe.utils.escape_html(d.item_code || "")} — ${frappe.utils.escape_html(d.item_name || "")}</span>
					</div>
					<div class="ctx-row" style="margin-top:6px;">
						<span class="ch-bm-bin-pill" style="background:${src.bg};color:${src.color}">
							<i class="fa ${src.icon}"></i> ${__("Currently in")}: ${__(d.bin_type || "")}
						</span>
						<span style="color:#94a3b8"><i class="fa fa-arrow-right"></i></span>
						<span class="ch-bm-bin-pill" style="background:${dst.bg};color:${dst.color}">
							<i class="fa ${dst.icon}"></i> ${__("Move to")}: ${__(this._active_bin)}
						</span>
					</div>
				`);
				if (d.bin_type === this._active_bin) {
					frappe.show_alert({ message: __("Serial is already in {0}", [this._active_bin]), indicator: "orange" });
				}
			},
		});
	}

	_move_serial() {
		const serial_no = (this.panel.find(".ch-bm-serial").val() || "").trim();
		const reason = this.reason_field.get_value();
		const item_code = this.item_field.get_value();

		if (!serial_no) {
			frappe.show_alert({ message: __("Scan or enter serial first"), indicator: "orange" });
			return;
		}
		if (!this._serial_ctx || !this._serial_ctx.bin_type) {
			frappe.show_alert({ message: __("Lookup serial before moving"), indicator: "orange" });
			return;
		}
		if (!reason) {
			frappe.show_alert({ message: __("Select a reason"), indicator: "orange" });
			return;
		}
		if (item_code && this._serial_ctx.item_code && item_code !== this._serial_ctx.item_code) {
			frappe.show_alert({ message: __("Selected item does not match serial item"), indicator: "red" });
			return;
		}
		if (this._serial_ctx.bin_type === this._active_bin) {
			frappe.show_alert({ message: __("Source and destination bins are same"), indicator: "orange" });
			return;
		}

		frappe.call({
			method: "ch_item_master.ch_core.bin_transfer.pos_bin_transfer",
			args: {
				store: PosState.store || null,
				item_code: this._serial_ctx.item_code,
				qty: 1,
				from_bin_type: this._serial_ctx.bin_type,
				to_bin_type: this._active_bin,
				reason,
				serial_no,
			},
			callback: (r) => {
				const m = r.message || {};
				if (!m.stock_entry) return;
				frappe.show_alert({
					message: __("Moved {0} to {1}. Stock Entry: {2}", [serial_no, this._active_bin, m.stock_entry]),
					indicator: "green",
				});
				this.panel.find(".ch-bm-serial").val("").focus();
				this.panel.find(".ch-bm-from-bin").val("");
				this.panel.find(".ch-bm-context").removeClass("visible").empty();
				this._serial_ctx = null;
				this._load_counts();
				this._refresh_bin_view();
			},
		});
	}

	_refresh_bin_view() {
		const list = this.panel.find(".ch-bm-list");
		const wh_el = this.panel.find('[data-role="warehouse"]');
		list.html(`<div class="ch-bm-empty"><i class="fa fa-spinner fa-spin"></i><br>${__("Loading...")}</div>`);

		frappe.call({
			method: "ch_item_master.ch_core.bin_transfer.get_store_bin_serials",
			args: {
				store: PosState.store || null,
				bin_type: this._active_bin,
				item_code: this.item_field ? this.item_field.get_value() : null,
				limit: 200,
			},
			callback: (r) => {
				const d = r.message || {};
				const rows = d.serials || [];
				wh_el.text(d.warehouse || "—");

				if (!rows.length) {
					const m = BIN_META[this._active_bin];
					list.html(`
						<div class="ch-bm-empty">
							<div class="icon"><i class="fa ${m.icon}"></i></div>
							${__("No serials in {0} bin yet", [this._active_bin])}
							<div style="font-size:11px;color:#94a3b8;margin-top:6px">${m.hint}</div>
						</div>
					`);
					return;
				}

				list.html(`
					<table class="ch-bm-list-table">
						<thead>
							<tr>
								<th style="width:34%">${__("Serial")}</th>
								<th>${__("Item")}</th>
								<th style="width:110px">${__("Status")}</th>
							</tr>
						</thead>
						<tbody>
							${rows.map((x) => `
								<tr>
									<td class="mono">${frappe.utils.escape_html(x.serial_no || "")}</td>
									<td>
										<div>${frappe.utils.escape_html(x.item_code || "")}</div>
										<div class="item-name">${frappe.utils.escape_html(x.item_name || "")}</div>
									</td>
									<td><span class="ch-bm-status-badge">${frappe.utils.escape_html(x.status || "—")}</span></td>
								</tr>
							`).join("")}
						</tbody>
					</table>
				`);
			},
		});
	}
}
