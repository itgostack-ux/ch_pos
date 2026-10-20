/**
 * CH POS — Inbound Receive Workspace
 *
 * Store-side entry point for the Purchase Receipt "Generate + Submit"
 * pipeline. Same server logic the stock team uses on the PR desk form,
 * but scoped to the current POS Profile's warehouse and gated by role.
 *
 * Tabs:
 *   • Draft PRs           — Purchase Receipts already targeting this store
 *                            that need Generate + Submit.
 *   • Pending POs         — Submitted POs (drop-ship or store-direct) with
 *                            qty left to receive; one-click materialises a
 *                            Draft PR the operator can then complete here.
 *
 * All mutating calls delegate to ``ch_pos.api.pos_inbound.*``, which in
 * turn reuses ``ch_erp15.ch_erp15.custom.purchase_receipt`` helpers so we
 * never re-implement barcode / IMEI generation logic on the client.
 */
import { PosState, EventBus } from "../../state.js";
import { wh_label } from "../../shared/helpers.js";

const API = "ch_pos.api.pos_inbound";

export class InboundReceiveWorkspace {
	constructor() {
		this.current_pr = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "inbound_receive") return;
			this.render(ctx.panel);
		});
	}

	// ── Shell ───────────────────────────────────────────────────────
	render(panel) {
		this.panel = panel;
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-mode-header">
					<h4>
						<span class="mode-icon" style="background:#dbeafe;color:#1d4ed8">
							<i class="fa fa-inbox"></i>
						</span>
						${__("Purchase Receipt Receive")}
					</h4>
					<span class="ch-mode-hint">${__("Complete stock-team GRN steps for goods arriving at this store")}</span>
				</div>

				<div class="ch-inbound-tabs" style="display:flex;gap:8px;margin-bottom:var(--pos-space-md)">
					<button class="ch-pos-category-chip active" data-tab="drafts">
						<i class="fa fa-file-text-o"></i> ${__("Draft Receipts")}
					</button>
					<button class="ch-pos-category-chip" data-tab="pos">
						<i class="fa fa-truck"></i> ${__("Pending POs")}
					</button>
				</div>

				<div class="ch-inbound-tab-content">
					<div class="ch-inbound-loading" style="padding:40px;text-align:center">
						<i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i>
					</div>
					<div class="ch-inbound-body"></div>
				</div>
			</div>
		`);

		this._bind(panel);
		this._load_drafts(panel);
	}

	_bind(panel) {
		panel.on("click", ".ch-inbound-tabs .ch-pos-category-chip", (e) => {
			const tab = $(e.currentTarget).data("tab");
			panel.find(".ch-inbound-tabs .ch-pos-category-chip").removeClass("active");
			$(e.currentTarget).addClass("active");
			this.current_pr = null;
			if (tab === "pos") this._load_pos(panel);
			else this._load_drafts(panel);
		});

		// Drafts list actions
		panel.on("click", ".ch-inbound-open-pr", (e) => {
			const name = $(e.currentTarget).data("name");
			this._open_pr(panel, name);
		});
		panel.on("click", ".ch-inbound-view-desk", (e) => {
			const name = $(e.currentTarget).data("name");
			frappe.set_route("Form", "Purchase Receipt", name);
		});

		// PO list actions
		panel.on("click", ".ch-inbound-create-pr", (e) => {
			const name = $(e.currentTarget).data("name");
			this._create_pr_from_po(panel, name);
		});
		panel.on("click", ".ch-inbound-view-po", (e) => {
			const name = $(e.currentTarget).data("name");
			frappe.set_route("Form", "Purchase Order", name);
		});

		// Detail view actions
		panel.on("click", ".ch-inbound-back", () => {
			this.current_pr = null;
			this._load_drafts(panel);
		});
		panel.on("click", ".ch-inbound-generate-barcode", (e) => {
			const row_name = $(e.currentTarget).data("row");
			this._generate_barcode(panel, row_name);
		});
		panel.on("click", ".ch-inbound-scan-imei", (e) => {
			const row_name = $(e.currentTarget).data("row");
			this._open_imei_dialog(panel, row_name);
		});
		panel.on("click", ".ch-inbound-submit-pr", () => {
			this._submit_pr(panel);
		});
	}

	// ── Loaders ─────────────────────────────────────────────────────
	_load_drafts(panel) {
		const loading = panel.find(".ch-inbound-loading").show();
		const body = panel.find(".ch-inbound-body").empty();

		frappe.call({
			method: `${API}.list_open_purchase_receipts`,
			args: { pos_profile: PosState.pos_profile },
			callback: (r) => {
				loading.hide();
				const rows = r.message || [];
				if (!rows.length) {
					body.html(this._empty_state({
						icon: "file-text-o",
						title: __("No Draft Purchase Receipts"),
						subtitle: __("Ask Purchasing to raise a PR against this store, or create one from a Pending PO."),
					}));
					return;
				}
				body.html(this._pr_table(rows));
			},
		});
	}

	_load_pos(panel) {
		const loading = panel.find(".ch-inbound-loading").show();
		const body = panel.find(".ch-inbound-body").empty();

		frappe.call({
			method: `${API}.list_pending_purchase_orders`,
			args: { pos_profile: PosState.pos_profile },
			callback: (r) => {
				loading.hide();
				const rows = r.message || [];
				if (!rows.length) {
					body.html(this._empty_state({
						icon: "truck",
						title: __("No Pending Purchase Orders"),
						subtitle: __("All submitted POs for this store are fully received or closed."),
					}));
					return;
				}
				body.html(this._po_table(rows));
			},
		});
	}

	// ── List cards ──────────────────────────────────────────────────
	// Draft Receipts as a list (table), in the same layout as Pending POs.
	_pr_table(rows) {
		const esc = frappe.utils.escape_html;
		return this._list_table(rows, [
			{ label: "#", cell: (pr, i) => `<span style="color:var(--pos-text-muted)">${i + 1}</span>` },
			{ label: __("GRN Number"), cell: (pr) => `<span style="font-weight:700;white-space:nowrap">${esc(pr.name)}</span>` },
			{ label: __("Date"), cell: (pr) => `<span style="white-space:nowrap">${frappe.datetime.str_to_user(pr.posting_date)}</span>` },
			{ label: __("Supplier"), cell: (pr) => esc(pr.supplier_name || pr.supplier || "—") },
			{ label: __("To"), cell: (pr) => esc(wh_label(pr.set_warehouse) || "—") },
			{ label: __("Items"), num: true, sum: (pr) => parseInt(pr.item_count || 0, 10) || 0, cell: (pr) => parseInt(pr.item_count || 0, 10) },
			{ label: __("Value"), num: true, money: true, sum: (pr) => parseFloat(pr.grand_total) || 0,
				cell: (pr) => `<span style="white-space:nowrap">${pr.grand_total ? `${esc(pr.currency || "")} ${format_currency(pr.grand_total)}` : "—"}</span>` },
			{ label: __("Status"), cell: (pr) => {
				const pending = parseInt(pr.pending_generate_rows || 0, 10) || 0;
				return pending > 0
					? `<span class="ch-pos-badge ch-pos-badge-warning" style="white-space:nowrap">${__("{0} rows need Generate", [pending])}</span>`
					: `<span class="ch-pos-badge ch-pos-badge-success" style="white-space:nowrap">${__("Ready to Submit")}</span>`;
			} },
			{ label: "", action: true, cell: (pr) => `<div style="display:flex;gap:6px;justify-content:flex-end">
				<button class="btn btn-xs btn-primary ch-inbound-open-pr" data-name="${esc(pr.name)}" style="border-radius:var(--pos-radius-sm);white-space:nowrap">
					<i class="fa fa-check-square-o"></i> ${__("Generate & Receive")}
				</button>
				<button class="btn btn-xs btn-outline-secondary ch-inbound-view-desk" data-name="${esc(pr.name)}" title="${__("Open in Desk")}" style="border-radius:var(--pos-radius-sm)">
					<i class="fa fa-external-link"></i>
				</button></div>` },
		], __("GRN"), __("GRNs"));
	}

	// Pending POs as a list (table): one row per PO, newest first as the
	// server sends them. Buttons keep the card's classes, so the bound
	// handlers (Create GRN / open in desk) are unchanged.
	_po_table(rows) {
		const esc = frappe.utils.escape_html;
		const cols = [
			{ label: "#", cell: (po, i) => `<span style="color:var(--pos-text-muted)">${i + 1}</span>` },
			{ label: __("PO Number"), cell: (po) => `<span style="font-weight:700;white-space:nowrap">${esc(po.name)}</span>`
				+ (parseInt(po.custom_is_drop_ship || 0, 10)
					? ` <span class="ch-pos-badge ch-pos-badge-warning">${__("Drop-Ship")}</span>` : "") },
			{ label: __("Date"), cell: (po) => `<span style="white-space:nowrap">${frappe.datetime.str_to_user(po.transaction_date)}</span>` },
			{ label: __("Supplier"), cell: (po) => esc(po.supplier_name || po.supplier || "—") },
			{ label: __("Destination"), cell: (po) => esc(wh_label(po.set_warehouse) || "—") },
			{ label: __("Items"), num: true, sum: (po) => parseInt(po.item_count || 0, 10) || 0, cell: (po) => parseInt(po.item_count || 0, 10) },
			{ label: __("Value"), num: true, money: true, sum: (po) => parseFloat(po.grand_total) || 0, cell: (po) => `<span style="white-space:nowrap">${po.grand_total
				? `${esc(po.currency || "")} ${format_currency(po.grand_total)}` : "—"}</span>` },
			{ label: __("Status"), cell: (po) => {
				const drafts = po.draft_grns || [];
				const submitted = po.submitted_grns || [];
				const nowrap = "white-space:nowrap";
				if (submitted.length) {
					return `<span class="ch-pos-badge ch-pos-badge-success" style="${nowrap}">${__("GRN Submitted")}</span>
						<div style="font-size:11px;color:var(--pos-text-muted);${nowrap}">${submitted.map(esc).join(", ")}</div>`;
				}
				if (drafts.length) {
					return `<span class="ch-pos-badge ch-pos-badge-warning" style="${nowrap}">${__("GRN Draft")}</span>
						<div style="font-size:11px;color:var(--pos-text-muted);${nowrap}">${drafts.map(esc).join(", ")}</div>`;
				}
				// Company receives from the invoice and none is submitted yet.
				if (po.invoice_first && !(po.receive_from_invoices || []).length) {
					return `<span class="ch-pos-badge ch-pos-badge-muted" style="${nowrap}" title="${__("Create the Purchase Invoice first; the GRN is made from it.")}">${__("Awaiting Invoice")}</span>`;
				}
				const per = parseFloat(po.per_received || 0);
				return per > 0
					? `<span class="ch-pos-badge ch-pos-badge-info" style="${nowrap}">${__("Received {0}%", [per.toFixed(0)])}</span>`
					: `<span class="ch-pos-badge ch-pos-badge-muted" style="${nowrap}">${__("Not yet received")}</span>`;
			} },
			// Create GRN only when no GRN exists for the PO: a draft is finished
			// (Open Draft), a submitted one needs nothing more here.
			{ label: "", action: true, cell: (po) => {
				const drafts = po.draft_grns || [];
				const submitted = po.submitted_grns || [];
				const btn = "border-radius:var(--pos-radius-sm);white-space:nowrap";
				let main = "";
				const awaiting_invoice = po.invoice_first && !(po.receive_from_invoices || []).length;
				if (!drafts.length && !submitted.length && !awaiting_invoice) {
					// Under "PO -> PI -> PR" the server builds it from the invoice.
					const from_pi = po.invoice_first ? (po.receive_from_invoices || [])[0] : "";
					main = `<button class="btn btn-xs btn-primary ch-inbound-create-pr" data-name="${esc(po.name)}" style="${btn}"
						${from_pi ? `title="${__("From Purchase Invoice {0}", [esc(from_pi)])}"` : ""}>
						<i class="fa fa-plus"></i> ${__("Create GRN")}</button>`;
				} else if (drafts.length && !submitted.length) {
					main = `<button class="btn btn-xs btn-default ch-inbound-open-pr" data-name="${esc(drafts[0])}" style="${btn}">
						<i class="fa fa-pencil"></i> ${__("Open Draft")}</button>`;
				}
				return `<div style="display:flex;gap:6px;justify-content:flex-end">${main}
					<button class="btn btn-xs btn-outline-secondary ch-inbound-view-po" data-name="${esc(po.name)}" title="${__("Open in Desk")}" style="border-radius:var(--pos-radius-sm)">
						<i class="fa fa-external-link"></i>
					</button></div>`;
			} },
		];
		return this._list_table(rows, cols, __("PO"), __("POs"));
	}

	// Shared list layout: sticky header, one row per record, totals footer
	// for the columns that carry a `sum`.
	_list_table(rows, cols, one, many) {
		const line = "1px solid var(--pos-border, var(--border-color))";
		const th = `padding:8px 10px;font-size:11px;text-transform:uppercase;letter-spacing:.03em;color:var(--pos-text-muted);background:var(--pos-surface-sunken);position:sticky;top:0;z-index:1;white-space:nowrap;border-bottom:${line}`;
		const td = `padding:8px 10px;vertical-align:middle;border-bottom:${line}`;
		const ft = `padding:8px 10px;font-weight:700;background:var(--pos-surface-sunken);position:sticky;bottom:0;border-top:2px solid var(--pos-border, var(--border-color));white-space:nowrap`;
		const align = (c) => (c.num || c.action ? "right" : "left");
		const head = cols.map((c) => `<th style="${th};text-align:${align(c)}">${c.label}</th>`).join("");
		const body = rows.map((r, i) => `<tr>${cols.map((c) =>
			`<td style="${td};text-align:${align(c)}">${c.cell(r, i)}</td>`).join("")}</tr>`).join("");
		const first_sum = cols.findIndex((c) => c.sum);
		const foot = `<tr><td colspan="${first_sum}" style="${ft}">${__("Total")} · ${rows.length} ${rows.length === 1 ? one : many}</td>`
			+ cols.slice(first_sum).map((c) => {
				if (!c.sum) return `<td style="${ft}"></td>`;
				const total = rows.reduce((t, r) => t + c.sum(r), 0);
				return `<td style="${ft};text-align:right">${c.money ? format_currency(total) : total}</td>`;
			}).join("") + `</tr>`;
		return `<div class="ch-inbound-list" style="max-height:70vh;overflow:auto;border:${line};border-radius:var(--pos-radius-md, 8px);background:var(--pos-surface, #fff)">
			<table style="width:100%;border-collapse:collapse;font-size:var(--pos-fs-xs, 12.5px)">
				<thead><tr>${head}</tr></thead><tbody>${body}</tbody><tfoot>${foot}</tfoot>
			</table></div>`;
	}

	// ── PR Detail (Generate + Submit) ──────────────────────────────
	_open_pr(panel, pr_name) {
		const loading = panel.find(".ch-inbound-loading").show();
		const body = panel.find(".ch-inbound-body").empty();

		frappe.call({
			method: `${API}.get_pr_detail`,
			args: { pr_name, pos_profile: PosState.pos_profile },
			callback: (r) => {
				loading.hide();
				if (!r.message) return;
				this.current_pr = r.message;
				body.html(this._pr_detail_html(this.current_pr));
			},
		});
	}

	_render_current_pr() {
		if (!this.current_pr || !this.panel) return;
		this.panel.find(".ch-inbound-body").html(this._pr_detail_html(this.current_pr));
	}

	_pr_detail_html(pr) {
		const rows_html = (pr.items || []).map(row => this._row_html(row)).join("");
		const submit_disabled = pr.all_rows_complete ? "" : "disabled";
		const submit_hint = pr.all_rows_complete
			? __("All rows have serials — safe to submit.")
			: __("Finish Generate on every IMEI / Barcode row before submitting.");

		return `
			<div style="display:flex;align-items:center;gap:8px;margin-bottom:var(--pos-space-md)">
				<button class="btn btn-xs btn-outline-secondary ch-inbound-back">
					<i class="fa fa-arrow-left"></i> ${__("Back to list")}
				</button>
				<div style="font-weight:700;font-size:var(--pos-fs-md)">${frappe.utils.escape_html(pr.name)}</div>
				<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
					${frappe.datetime.str_to_user(pr.posting_date)} · ${frappe.utils.escape_html(pr.supplier_name || pr.supplier || "")}
				</div>
			</div>

			<div class="ch-inbound-rows">${rows_html || `<div class="ch-pos-empty-state" style="padding:40px">${__("No rows on this PR.")}</div>`}</div>

			<div style="margin-top:var(--pos-space-md);display:flex;justify-content:flex-end;align-items:center;gap:12px">
				<span style="font-size:var(--pos-fs-xs);color:var(--pos-text-muted)">${submit_hint}</span>
				<button class="btn btn-primary ch-inbound-submit-pr" ${submit_disabled}>
					<i class="fa fa-check"></i> ${__("Submit GRN")}
				</button>
			</div>
		`;
	}

	_row_html(row) {
		const idx = row.idx;
		const item = frappe.utils.escape_html(row.item_name || row.item_code || "");
		const wh = wh_label(row.warehouse);
		const type_badge = row.custom_type
			? `<span class="ch-pos-badge ch-pos-badge-info">${frappe.utils.escape_html(row.custom_type)}</span>`
			: `<span class="ch-pos-badge ch-pos-badge-muted">${__("Non-Serial")}</span>`;

		let progress_html = "";
		let action_html = "";

		if (row.needs_generate) {
			const scanned = row.scanned_count || 0;
			const qty = parseInt(row.qty || 0, 10);
			const pct = qty ? Math.min(100, Math.round((scanned / qty) * 100)) : 0;
			const pill_cls = row.complete ? "ch-pos-badge-success" : (scanned > 0 ? "ch-pos-badge-warning" : "ch-pos-badge-muted");
			progress_html = `
				<span class="ch-pos-badge ${pill_cls}">${scanned} / ${qty} ${__("serials")}</span>
				<div style="height:4px;background:var(--pos-surface-sunken);border-radius:2px;width:120px;overflow:hidden">
					<div style="height:100%;width:${pct}%;background:${row.complete ? "var(--pos-success)" : "var(--pos-warning)"}"></div>
				</div>`;

			if (row.custom_type === "Barcode") {
				action_html = `
					<button class="btn btn-xs btn-primary ch-inbound-generate-barcode" data-row="${frappe.utils.escape_html(row.name)}">
						<i class="fa fa-barcode"></i> ${row.complete ? __("Regenerate Barcode") : __("Generate Barcode")}
					</button>`;
			} else if (row.custom_type === "IMEI") {
				action_html = `
					<button class="btn btn-xs btn-primary ch-inbound-scan-imei" data-row="${frappe.utils.escape_html(row.name)}">
						<i class="fa fa-mobile"></i> ${row.complete ? __("Edit IMEIs") : __("Scan IMEIs")}
					</button>`;
			}
		} else {
			progress_html = `<span class="ch-pos-badge ch-pos-badge-success">${__("Ready")}</span>`;
		}

		const preview = (row.serials || []).slice(0, 3).map(s => frappe.utils.escape_html(s)).join(", ");
		const preview_html = preview
			? `<div style="font-size:11px;color:var(--pos-text-muted);margin-top:4px">${preview}${(row.serials || []).length > 3 ? " …" : ""}</div>`
			: "";

		return `
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-sm)">
				<div class="section-body" style="padding:12px 16px">
					<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap">
						<div style="flex:1;min-width:220px">
							<div style="font-weight:700;font-size:var(--pos-fs-sm)">${idx}. ${item}</div>
							<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
								${__("Qty")}: ${parseInt(row.qty || 0, 10)} ${frappe.utils.escape_html(row.uom || "")} · ${__("Warehouse")}: ${wh || "—"}
							</div>
							${preview_html}
						</div>
						<div style="display:flex;flex-direction:column;align-items:flex-end;gap:6px">
							${type_badge}
							<div style="display:flex;align-items:center;gap:8px">${progress_html}</div>
							${action_html}
						</div>
					</div>
				</div>
			</div>
		`;
	}

	// ── Row-level actions ───────────────────────────────────────────
	_generate_barcode(panel, row_name) {
		if (!this.current_pr) return;
		frappe.confirm(
			__("Auto-generate barcode serials for this row? Existing serials will be replaced."),
			() => {
				frappe.call({
					method: `${API}.pos_pr_generate_barcode_serials`,
					args: {
						pr_name: this.current_pr.name,
						row_name,
						pos_profile: PosState.pos_profile,
					},
					freeze: true,
					freeze_message: __("Generating barcode serials..."),
					callback: (r) => {
						if (!r.message) return;
						frappe.show_alert({
							message: __("Serials generated with prefix {0}", [r.message.prefix || ""]),
							indicator: "green",
						});
						this._open_pr(panel, this.current_pr.name);
					},
				});
			}
		);
	}

	// Same model as the Purchase Receipt desk form's IMEI Entry
	// (ch_erp15 purchase_receipt.js open_imei_dialog): a scan box that fills
	// the next empty slot, one slot per unit, a live Scanned / Remaining
	// counter, and the same checks -- not twice in this list, not on another
	// row of this receipt, not an IMEI already in stock. A partial list may be
	// saved; the row counts as Generated once every slot is filled.
	_open_imei_dialog(panel, row_name) {
		if (!this.current_pr) return;
		const row = (this.current_pr.items || []).find(r => r.name === row_name);
		if (!row) return;
		const esc = frappe.utils.escape_html;

		const allowed_qty = Math.max(parseInt(row.qty || 0, 10) || 0, 0);
		if (allowed_qty <= 0) {
			frappe.msgprint({ title: __("No Receivable Quantity"), indicator: "orange",
				message: __("Row {0} has no quantity available for IMEI entry.", [row.idx]) });
			return;
		}
		const existing = (row.serials || []).map(s => String(s).trim()).filter(Boolean);
		if (existing.length > allowed_qty) {
			frappe.msgprint({ title: __("Quantity Mismatch"), indicator: "red",
				message: __("This row allows only {0} quantity, but {1} IMEIs are already present.", [allowed_qty, existing.length]) });
			return;
		}
		const table_data = [];
		for (let i = 0; i < allowed_qty; i++) {
			table_data.push({ item_code: row.item_code, item_name: row.item_name, imei_no: existing[i] || "" });
		}
		// IMEIs already on the receipt's other rows.
		const other_rows = (this.current_pr.items || []).filter(r => r.name !== row.name)
			.map(r => ({ idx: r.idx, item_code: r.item_code, serials: (r.serials || []).map(s => String(s).trim()) }));

		const dlg = new frappe.ui.Dialog({
			title: __("IMEI Entry — {0}", [row.item_code]),
			size: "large",
			fields: [
				{ fieldname: "scanner_input", fieldtype: "Data", label: __("Scan IMEI"), placeholder: __("Scan / Type / Paste IMEI") },
				{ fieldname: "error_html", fieldtype: "HTML" },
				{ fieldname: "counter_html", fieldtype: "HTML" },
				{
					fieldname: "imei_table", fieldtype: "Table", label: __("IMEI List"),
					cannot_add_rows: true, cannot_delete_rows: true, in_place_edit: true, data: table_data,
					fields: [
						{ fieldtype: "Data", fieldname: "item_code", label: __("Item Code"), read_only: 1, in_list_view: 1 },
						{ fieldtype: "Data", fieldname: "item_name", label: __("Item Name"), read_only: 1, in_list_view: 1 },
						{ fieldtype: "Data", fieldname: "imei_no", label: __("IMEI"), in_list_view: 1 },
					],
				},
			],
			primary_action_label: __("Save"),
			primary_action: async () => {
				clear_error();
				const list = grid().get_data().map(d => (d.imei_no || "").trim()).filter(Boolean);
				if (list.length > allowed_qty) {
					show_error(__("Quantity mismatch: allowed {0}, entered {1}.", [allowed_qty, list.length]));
					return;
				}
				const seen = {};
				for (const val of list) {
					if (seen[val]) { show_error(__("Duplicate IMEI in list: {0}", [val])); return; }
					seen[val] = true;
				}
				for (const val of list) {
					const clash = on_other_row(val);
					if (clash) { show_error(clash); return; }
				}
				for (const val of list) {
					if (await in_stock_already(val)) {
						show_error(__("IMEI {0} already exists. Please remove it.", [val]));
						return;
					}
				}
				frappe.call({
					method: `${API}.pos_pr_set_imei_serials`,
					args: { pr_name: this.current_pr.name, row_name, serials: list, pos_profile: PosState.pos_profile },
					freeze: true,
					freeze_message: __("Saving serials..."),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						frappe.show_alert(list.length === allowed_qty
							? { message: __("IMEI saved successfully"), indicator: "green" }
							: { message: __("Saved {0}/{1}. Scan again to continue.", [list.length, allowed_qty]), indicator: "orange" }, 5);
						this._open_pr(panel, this.current_pr.name);
					},
				});
			},
		});
		dlg.show();

		const grid = () => dlg.fields_dict.imei_table.grid;
		const scanner_input = dlg.fields_dict.scanner_input.$input;
		setTimeout(() => scanner_input.focus(), 300);

		function show_error(msg) {
			dlg.fields_dict.error_html.$wrapper.html(`
				<div style="background:#FBE9E7;color:#B71C1C;padding:8px 12px;border-radius:6px;border:1px solid #EF9A9A;margin:6px 0;">
					<strong>⚠ ${esc(msg)}</strong>
				</div>`);
		}
		function clear_error() { dlg.fields_dict.error_html.$wrapper.html(""); }
		function update_counter() {
			const scanned = grid().get_data().filter(d => d.imei_no && d.imei_no.trim()).length;
			dlg.fields_dict.counter_html.$wrapper.html(`
				<div style="margin:8px 0;display:flex;gap:10px;align-items:center;">
					<span style="background:#EAF3DE;padding:4px 10px;border-radius:12px;">${__("Scanned")}: ${scanned}</span>
					<span style="background:#FAEEDA;padding:4px 10px;border-radius:12px;">${__("Remaining")}: ${allowed_qty - scanned}</span>
					<span style="margin-left:auto;">${scanned} / ${allowed_qty}</span>
				</div>`);
		}
		function on_other_row(val) {
			const other = other_rows.find(r => r.serials.includes(val));
			return other ? __("IMEI {0} already scanned in row {1} ({2})", [val, other.idx, other.item_code]) : "";
		}
		// A unit already in the system (anything but Delivered) cannot be received again.
		async function in_stock_already(val) {
			try {
				const found = await frappe.db.get_list("Serial No", { filters: { name: val }, fields: ["name", "status"], limit: 1 });
				return found.length > 0 && (found[0].status || "").trim() !== "Delivered";
			} catch (e) {
				return false;
			}
		}
		async function add_imei(val) {
			clear_error();
			val = (val || "").trim();
			if (!val) return false;
			const table = grid().get_data();
			if (table.some(d => (d.imei_no || "").trim() === val)) {
				show_error(__("IMEI {0} already entered in this list", [val]));
				return false;
			}
			const clash = on_other_row(val);
			if (clash) { show_error(clash); return false; }
			if (await in_stock_already(val)) {
				show_error(__("IMEI {0} already exists.", [val]));
				return false;
			}
			const empty = table.find(d => !d.imei_no || !d.imei_no.trim());
			if (!empty) { show_error(__("All IMEI slots are filled")); return false; }
			empty.imei_no = val;
			grid().refresh();
			update_counter();
			return true;
		}
		update_counter();

		scanner_input.on("keydown", async (e) => {
			if (e.key !== "Enter") return;
			e.preventDefault();
			const raw = (scanner_input.val() || "").trim();
			if (!raw) return;
			const values = raw.split(/[\n\r\t,;]+/).map(v => v.trim()).filter(Boolean);
			let filled = 0, failed = 0;
			for (const val of values) {
				if (await add_imei(val)) filled++; else failed++;
			}
			scanner_input.val("");
			scanner_input.focus();
			if (values.length > 1) {
				frappe.show_alert({ message: __("{0} added, {1} skipped", [filled, failed]), indicator: filled ? "green" : "orange" });
			}
		});
		// A pasted list lands in the box as-is; Enter then splits and adds it.
		scanner_input.on("paste", (e) => {
			e.preventDefault();
			clear_error();
			const data = (e.originalEvent.clipboardData || window.clipboardData).getData("text");
			if (data) scanner_input.val(data.trim());
		});
		// Pasting several IMEIs into one slot fills the slots in turn.
		grid().wrapper.on("paste", '.grid-row [data-fieldname="imei_no"] input', async function (e) {
			const clip = (e.originalEvent.clipboardData || window.clipboardData).getData("text");
			const imeis = (clip || "").split(/[\n\r\t,;]+/).map(v => v.trim()).filter(Boolean);
			if (imeis.length <= 1) return;
			e.preventDefault();
			clear_error();
			let filled = 0, failed = 0;
			for (const val of imeis) {
				if (await add_imei(val)) filled++; else failed++;
			}
			frappe.show_alert({ message: __("{0} added, {1} skipped", [filled, failed]), indicator: filled ? "green" : "orange" });
		});
		// Typed straight into a slot: same checks, the slot is emptied if refused.
		grid().wrapper.on("change", '.grid-row [data-fieldname="imei_no"] input', async function () {
			clear_error();
			const $input = $(this);
			const val = ($input.val() || "").trim();
			const idx = (parseInt($input.closest(".grid-row").attr("data-idx"), 10) || 0) - 1;
			if (idx < 0 || !val) { update_counter(); return; }
			const table = grid().get_data();
			let msg = table.some((d, i) => i !== idx && (d.imei_no || "").trim() === val)
				? __("IMEI {0} already entered in this list", [val]) : on_other_row(val);
			if (!msg && await in_stock_already(val)) msg = __("IMEI {0} already exists.", [val]);
			if (msg) {
				show_error(msg);
				$input.val("");
				if (table[idx]) table[idx].imei_no = "";
				grid().refresh();
			}
			update_counter();
		});
	}

	_submit_pr(panel) {
		if (!this.current_pr) return;
		const name = this.current_pr.name;
		frappe.confirm(
			__("Submit Purchase Receipt {0}? This posts the GRN and updates stock at this store.", [name]),
			() => {
				frappe.call({
					method: `${API}.pos_pr_submit`,
					args: { pr_name: name, pos_profile: PosState.pos_profile },
					freeze: true,
					freeze_message: __("Submitting GRN..."),
					callback: (r) => {
						if (!r.message) return;
						frappe.show_alert({
							message: __("GRN {0} submitted", [name]),
							indicator: "green",
						});
						this.current_pr = null;
						this._load_drafts(panel);
					},
				});
			}
		);
	}

	// ── Utilities ───────────────────────────────────────────────────
	_create_pr_from_po(panel, po_name) {
		frappe.confirm(
			__("Create a Draft Purchase Receipt from {0}? You can then Generate + Submit here.", [po_name]),
			() => {
				frappe.call({
					method: `${API}.create_pr_from_po`,
					args: { po_name, pos_profile: PosState.pos_profile },
					freeze: true,
					freeze_message: __("Creating Draft GRN..."),
					callback: (r) => {
						if (!r.message || !r.message.pr_name) return;
						frappe.show_alert({
							message: __("Created Draft {0}", [r.message.pr_name]),
							indicator: "green",
						});
						// Switch to Drafts tab and open the new PR
						panel.find(".ch-inbound-tabs .ch-pos-category-chip").removeClass("active");
						panel.find(".ch-inbound-tabs .ch-pos-category-chip[data-tab='drafts']").addClass("active");
						this._open_pr(panel, r.message.pr_name);
					},
				});
			}
		);
	}

	_empty_state({ icon, title, subtitle }) {
		return `
			<div class="ch-pos-empty-state" style="padding:40px">
				<div class="empty-icon"><i class="fa fa-${icon}"></i></div>
				<div class="empty-title">${title}</div>
				<div class="empty-subtitle">${subtitle}</div>
			</div>`;
	}
}

// Local currency formatter (falls back gracefully when the number is 0/null)
function format_currency(v) {
	const n = parseFloat(v || 0);
	if (!isFinite(n)) return "";
	return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
