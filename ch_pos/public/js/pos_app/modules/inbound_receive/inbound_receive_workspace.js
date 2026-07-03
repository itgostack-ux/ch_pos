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
						${__("Inbound Receive")}
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
				body.html(`<div class="ch-inbound-list">${rows.map(pr => this._pr_card(pr)).join("")}</div>`);
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
				body.html(`<div class="ch-inbound-list">${rows.map(po => this._po_card(po)).join("")}</div>`);
			},
		});
	}

	// ── List cards ──────────────────────────────────────────────────
	_pr_card(pr) {
		const name = frappe.utils.escape_html(pr.name);
		const supplier = frappe.utils.escape_html(pr.supplier_name || pr.supplier || "");
		const wh = frappe.utils.escape_html(pr.set_warehouse || "");
		const pending = parseInt(pr.pending_generate_rows || 0, 10) || 0;
		const pending_badge = pending > 0
			? `<span class="ch-pos-badge ch-pos-badge-warning">${__("{0} rows need Generate", [pending])}</span>`
			: `<span class="ch-pos-badge ch-pos-badge-success">${__("Ready to Submit")}</span>`;
		const gt = pr.grand_total
			? `${frappe.utils.escape_html(pr.currency || "")} ${format_currency(pr.grand_total)}`
			: "";

		return `
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-sm)">
				<div class="section-body" style="padding:12px 16px">
					<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
						<div>
							<div style="font-weight:700;font-size:var(--pos-fs-sm)">${name}</div>
							<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
								${frappe.datetime.str_to_user(pr.posting_date)} · ${supplier || "—"}
							</div>
						</div>
						<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end">
							${pending_badge}
							<button class="btn btn-xs btn-primary ch-inbound-open-pr" data-name="${name}" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-check-square-o"></i> ${__("Generate & Receive")}
							</button>
							<button class="btn btn-xs btn-outline-secondary ch-inbound-view-desk" data-name="${name}" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-external-link"></i>
							</button>
						</div>
					</div>
					<div style="display:flex;align-items:center;gap:6px;font-size:var(--pos-fs-xs);color:var(--pos-text-secondary);flex-wrap:wrap">
						<span style="font-size:11px;color:var(--pos-text-muted)">${__("To")}</span>
						<span style="padding:3px 8px;background:var(--pos-surface-sunken);border-radius:var(--pos-radius-sm)">${wh || "—"}</span>
						<span style="font-size:11px;color:var(--pos-text-muted);margin-left:10px">${__("Items")}</span>
						<span>${parseInt(pr.item_count || 0, 10)}</span>
						${gt ? `<span style="font-size:11px;color:var(--pos-text-muted);margin-left:10px">${__("Value")}</span><span>${gt}</span>` : ""}
					</div>
				</div>
			</div>`;
	}

	_po_card(po) {
		const name = frappe.utils.escape_html(po.name);
		const supplier = frappe.utils.escape_html(po.supplier_name || po.supplier || "");
		const wh = frappe.utils.escape_html(po.set_warehouse || "");
		const per = parseFloat(po.per_received || 0);
		const per_badge = per > 0
			? `<span class="ch-pos-badge ch-pos-badge-info">${__("Received {0}%", [per.toFixed(0)])}</span>`
			: `<span class="ch-pos-badge ch-pos-badge-muted">${__("Not yet received")}</span>`;
		const ds_badge = parseInt(po.custom_is_drop_ship || 0, 10)
			? `<span class="ch-pos-badge ch-pos-badge-warning" style="margin-left:6px">${__("Drop-Ship")}</span>`
			: "";
		const gt = po.grand_total
			? `${frappe.utils.escape_html(po.currency || "")} ${format_currency(po.grand_total)}`
			: "";

		return `
			<div class="ch-pos-section-card" style="margin-bottom:var(--pos-space-sm)">
				<div class="section-body" style="padding:12px 16px">
					<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:8px">
						<div>
							<div style="font-weight:700;font-size:var(--pos-fs-sm)">${name}${ds_badge}</div>
							<div style="font-size:var(--pos-fs-2xs);color:var(--pos-text-muted)">
								${frappe.datetime.str_to_user(po.transaction_date)} · ${supplier || "—"}
							</div>
						</div>
						<div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end">
							${per_badge}
							<button class="btn btn-xs btn-primary ch-inbound-create-pr" data-name="${name}" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-plus"></i> ${__("Create GRN")}
							</button>
							<button class="btn btn-xs btn-outline-secondary ch-inbound-view-po" data-name="${name}" style="border-radius:var(--pos-radius-sm)">
								<i class="fa fa-external-link"></i>
							</button>
						</div>
					</div>
					<div style="display:flex;align-items:center;gap:6px;font-size:var(--pos-fs-xs);color:var(--pos-text-secondary);flex-wrap:wrap">
						<span style="font-size:11px;color:var(--pos-text-muted)">${__("Destination")}</span>
						<span style="padding:3px 8px;background:var(--pos-surface-sunken);border-radius:var(--pos-radius-sm)">${wh || "—"}</span>
						<span style="font-size:11px;color:var(--pos-text-muted);margin-left:10px">${__("Items")}</span>
						<span>${parseInt(po.item_count || 0, 10)}</span>
						${gt ? `<span style="font-size:11px;color:var(--pos-text-muted);margin-left:10px">${__("Value")}</span><span>${gt}</span>` : ""}
					</div>
				</div>
			</div>`;
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
		const wh = frappe.utils.escape_html(row.warehouse || "");
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

	_open_imei_dialog(panel, row_name) {
		if (!this.current_pr) return;
		const row = (this.current_pr.items || []).find(r => r.name === row_name);
		if (!row) return;

		const qty = parseInt(row.qty || 0, 10);
		const existing = (row.serials || []).join("\n");

		const dlg = new frappe.ui.Dialog({
			title: __("Scan / Enter IMEIs — {0}", [row.item_name || row.item_code]),
			fields: [
				{
					fieldtype: "HTML",
					fieldname: "hint",
					options: `<div class="alert alert-info" style="margin-bottom:8px">
						${__("Enter or scan one IMEI per line. Need <b>{0}</b> serials for Qty {0}.", [qty])}
					</div>`,
				},
				{
					fieldtype: "Small Text",
					fieldname: "serial_list",
					label: __("IMEI / Serial Numbers"),
					default: existing,
					reqd: 1,
				},
			],
			primary_action_label: __("Save Serials"),
			primary_action: (values) => {
				const list = (values.serial_list || "")
					.split(/\r?\n/)
					.map(s => s.trim())
					.filter(Boolean);

				if (list.length !== qty) {
					frappe.msgprint({
						title: __("Count mismatch"),
						message: __("Entered {0} serial(s) but Qty is {1}.", [list.length, qty]),
						indicator: "red",
					});
					return;
				}
				if (new Set(list).size !== list.length) {
					frappe.msgprint({
						title: __("Duplicates"),
						message: __("The list has duplicate serials — please de-duplicate."),
						indicator: "red",
					});
					return;
				}

				frappe.call({
					method: `${API}.pos_pr_set_imei_serials`,
					args: {
						pr_name: this.current_pr.name,
						row_name,
						serials: list,
						pos_profile: PosState.pos_profile,
					},
					freeze: true,
					freeze_message: __("Saving serials..."),
					callback: (r) => {
						if (!r.message) return;
						dlg.hide();
						frappe.show_alert({
							message: __("Saved {0} serial(s) for row {1}", [list.length, row.idx]),
							indicator: "green",
						});
						this._open_pr(panel, this.current_pr.name);
					},
				});
			},
		});
		dlg.show();
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
