/**
 * CH POS — the four item-by-item stock lists
 *
 *   Stock We Requested   (Request Stock)  stock requests raised from this store
 *   Stock Coming In      (Request Stock)  Delivery Challans coming to this store
 *   Requested From Us    (Transfers)      transfer requests asking this store
 *                                         to send stock, waiting for approval
 *   Stock Sent Out       (Transfers)      Delivery Challans this store has sent
 *
 * One screen draws all four: a filter bar (search, From / To date, location,
 * status), a table whose headings sort either way, and pages of 25.
 *
 * On the two challan lists the Delivery Challan number is a link: it opens
 * what is on that challan — its items — and an item there opens its IMEIs.
 *
 * They are lists to read. Raising a request, receiving, raising a challan and
 * packing stay on the Request Stock and Transfers screens themselves.
 */
import { PosState, EventBus } from "../../state.js";

const text = (key, label) => ({ key, label, value: (r) => r[key] });
const qty  = (key, label) => ({ key, label, value: (r) => flt(r[key]), number: true });
const DATE = { key: "date", label: __("Date"), value: (r) => r.raised_at || r.date, date: true };
const TRANSFER_LIST = "ch_pos.api.pos_api.get_transfer_item_list";

const VIEWS = {
	my_requests: {
		method: "ch_pos.api.pos_api.get_my_stock_requests",
		title: __("Stock We Requested"),
		hint: __("Every stock request raised from this store, item by item"),
		icon: "fa-user", tint: "background:#dbeafe;color:#2563eb",
		places: ["delivery_location"], place_label: __("Delivery Location"),
		to_receive: true,
		search: __("Request, item, status…"),
		empty: __("No stock request has been raised from this store yet"),
		columns: [
			text("request", __("Request ID")), DATE, text("item_name", __("Item Name")),
			qty("qty", __("Requested Qty")), qty("approved_qty", __("Approved")),
			qty("rejected_qty", __("Rejected")), qty("received_qty", __("Received")),
			qty("pending_qty", __("Pending")), text("delivery_location", __("Delivery Location")),
			text("raised_by", __("Raised By")), text("latest_status", __("Status")),
		],
	},
	transfer_others: {
		method: TRANSFER_LIST, view: "others",
		title: __("Requested From Us"),
		hint: __("Every transfer another store or the back office has asked this store for, item by item"),
		icon: "fa-hand-paper-o", tint: "background:#fef3c7;color:#b45309",
		places: ["requested_from", "delivery_location"], place_label: __("Location"),
		search: __("Request, item, status…"),
		empty: __("No transfer request from another store is waiting"),
		columns: [
			text("transfer", __("Request ID")), DATE, text("item_name", __("Item Name")),
			qty("qty", __("Requested Qty")), qty("approved_qty", __("Approved")),
			qty("rejected_qty", __("Rejected")), text("requested_from", __("Requested From")),
			text("delivery_location", __("Delivery Location")),
			text("raised_by", __("Raised By")), text("latest_status", __("Status")),
		],
	},
	transfer_incoming_dc: {
		method: TRANSFER_LIST, view: "incoming",
		title: __("Stock Coming In — Incoming Delivery Challans"),
		hint: __("Every Delivery Challan coming to this store, item by item"),
		icon: "fa-arrow-down", tint: "background:#dcfce7;color:#15803d",
		places: ["from_location"], place_label: __("From Location"),
		search: __("Challan, item, IMEI, status…"),
		empty: __("No Delivery Challan has come to this store yet"),
		columns: [
			text("delivery_challan", __("Delivery Challan")), DATE, text("item_name", __("Item Name")),
			qty("qty", __("Qty")), { ...qty("received_qty", __("Received")), of: "qty" },
			text("serials", __("IMEI / Serial")), text("from_location", __("From Location")),
			text("delivery_mode", __("Delivery Mode")), text("delivered_by", __("Delivered By")),
			text("latest_status", __("Status")),
		],
	},
	transfer_outgoing_dc: {
		method: TRANSFER_LIST, view: "outgoing",
		title: __("Stock Sent Out — Outgoing Delivery Challans"),
		hint: __("Every Delivery Challan this store has sent out, item by item"),
		icon: "fa-arrow-up", tint: "background:#dbeafe;color:#2563eb",
		places: ["delivery_location"], place_label: __("Delivery Location"),
		search: __("Challan, item, IMEI, status…"),
		empty: __("This store has not sent out any Delivery Challan yet"),
		columns: [
			text("delivery_challan", __("Delivery Challan")), DATE,
			text("transfer", __("Transfer Request")), text("item_name", __("Item Name")),
			qty("qty", __("Qty")), { ...qty("received_qty", __("Received")), of: "qty" },
			text("serials", __("IMEI / Serial")),
			text("delivery_location", __("Delivery Location")), text("delivery_mode", __("Delivery Mode")),
			text("raised_by", __("Raised By")), text("latest_status", __("Status")),
		],
	},
};

const PAGE_SIZE = 25;

// A status reads at a glance by its colour: done, waiting on someone, refused,
// and everything still moving.
const DONE    = ["Received", "Delivered", "Transferred", "Approved", "Completed"];
const WAITING = ["Delivered At Store", "receive it now", "Pending Approval", "Waiting for approval",
	"Partially Received", "Pending"];
const REFUSED = ["Rejected", "Cancelled", "Force Closed"];
const status_tone = (status) => {
	const s = String(status || "");
	if (REFUSED.some((x) => s.includes(x))) return "red";
	if (WAITING.some((x) => s.includes(x))) return "amber";
	if (DONE.some((x) => s.includes(x))) return "green";
	return "blue";
};

const STYLE = `
.ch-tl-head{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.ch-tl-filters{background:#fff;border:1px solid #e5e9f0;border-radius:12px;padding:12px 16px;
	display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:14px}
.ch-tl-f{display:flex;flex-direction:column;gap:4px;margin:0}
.ch-tl-f>span{font-size:11px;font-weight:600;color:#64748b;text-transform:uppercase;letter-spacing:.05em}
.ch-tl-filters .form-control{height:36px;border:1px solid #d6dce6;border-radius:8px;background:#fff;
	font-size:13px;box-shadow:none}
.ch-tl-filters .btn{height:36px;border-radius:8px;font-weight:600}
.ch-tl-card{background:#fff;border:1px solid #e5e9f0;border-radius:12px;overflow:hidden}
.ch-tl-scroll{overflow-x:auto}
.ch-tl-table{width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap;margin:0}
.ch-tl-table th{background:#f8fafc;text-align:left;padding:11px 14px;font-size:11.5px;font-weight:600;
	text-transform:uppercase;letter-spacing:.05em;color:#475569;border-bottom:1px solid #e5e9f0;
	cursor:pointer;user-select:none}
.ch-tl-table td{padding:10px 14px;border-bottom:1px solid #eef1f6;vertical-align:middle;color:#0f172a}
.ch-tl-table tbody tr:nth-child(even) td{background:#fbfcfe}
.ch-tl-table tbody tr:hover td{background:#f1f5f9}
.ch-tl-table tbody tr:last-child td{border-bottom:0}
.ch-tl-table .text-right{text-align:right}
.ch-tl-id{font-weight:600;color:#0f172a}
a.ch-tl-id{color:#2563eb}
.ch-tl-muted{color:#64748b}
.ch-tl-imei{display:inline-block;font-family:var(--font-family-monospace,monospace);font-size:12px;
	background:#f1f5f9;border-radius:6px;padding:2px 7px;margin:1px 3px 1px 0;color:#334155}
.ch-tl-tag{display:inline-block;border:1px solid #d6dce6;border-radius:6px;padding:1px 8px;font-size:12px;
	color:#475569;background:#fff}
.ch-tl-tag.courier{border-color:#c7d2fe;color:#4338ca;background:#eef2ff}
.ch-tl-pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:3px 10px;
	font-size:12px;font-weight:600}
.ch-tl-pill:before{content:"";width:7px;height:7px;border-radius:50%;background:currentColor}
.ch-tl-pill.green{background:#dcfce7;color:#15803d}.ch-tl-pill.amber{background:#fef3c7;color:#b45309}
.ch-tl-pill.blue{background:#dbeafe;color:#1d4ed8}.ch-tl-pill.red{background:#fee2e2;color:#b91c1c}
.ch-tl-foot{display:flex;align-items:center;gap:10px;padding:10px 14px;border-top:1px solid #e5e9f0;
	font-size:12.5px;color:#64748b;background:#f8fafc}
.ch-tl-pages{margin-left:auto;display:flex;gap:6px}
.ch-tl-pages button{border:1px solid #d6dce6;border-radius:6px;padding:3px 10px;background:#fff;
	color:#334155;font-size:12.5px;min-width:30px}
.ch-tl-pages button.on{background:#2563eb;border-color:#2563eb;color:#fff}
.ch-tl-pages button:disabled{opacity:.45;cursor:default}
`;

export class TransferListsWorkspace {
	constructor() {
		EventBus.on("workspace:render", (ctx) => {
			if (!VIEWS[ctx.mode]) return;
			this.render(ctx.panel, ctx.mode);
		});
	}

	render(panel, mode) {
		this.panel = panel;
		this.mode = mode;
		this.spec = VIEWS[mode];
		this.rows = [];
		this.page = 1;
		this.to_receive = false;
		// Newest first until a heading is clicked.
		this.sort = { key: "date", dir: -1 };
		const spec = this.spec;
		if (!document.getElementById("ch-tl-style")) {
			$(`<style id="ch-tl-style">${STYLE}</style>`).appendTo(document.head);
		}
		const field = (label, control) => `<label class="ch-tl-f"><span>${label}</span>${control}</label>`;
		panel.html(`
			<div class="ch-pos-mode-panel">
				<div class="ch-tl-head">
					<div class="ch-mode-header" style="margin:0;flex:1">
						<h4>
							<span class="mode-icon" style="${spec.tint}">
								<i class="fa ${spec.icon}"></i>
							</span>
							${spec.title}
						</h4>
						<span class="ch-mode-hint">${spec.hint}</span>
					</div>
					<button class="btn btn-default btn-sm ch-tl-refresh" style="border-radius:8px;font-weight:600">
						<i class="fa fa-refresh"></i> ${__("Refresh")}
					</button>
				</div>
				<div class="ch-tl-filters">
					<label class="ch-tl-f" style="flex:1;min-width:220px"><span>${__("Search")}</span>
						<input type="text" class="form-control ch-tl-search" placeholder="${spec.search}"></label>
					${field(__("From Date"), `<input type="date" class="form-control ch-tl-from" style="width:150px">`)}
					${field(__("To Date"), `<input type="date" class="form-control ch-tl-to" style="width:150px">`)}
					${field(spec.place_label, `<select class="form-control ch-tl-place" style="width:190px">
						<option value="">${__("All locations")}</option></select>`)}
					${field(__("Status"), `<select class="form-control ch-tl-status" style="width:190px">
						<option value="">${__("All statuses")}</option></select>`)}
					${spec.to_receive ? `<button class="btn btn-default ch-tl-to-receive"
						title="${__("Only what has been delivered and is waiting to be received at this store")}">
						${__("To receive")} <span class="ch-tl-to-receive-count"></span></button>` : ""}
					<button class="btn btn-default ch-tl-clear">${__("Clear")}</button>
				</div>
				<div class="ch-tl-body"></div>
			</div>
		`);

		const redraw = () => { this.page = 1; this._draw(); };
		panel.off(".chTransferLists");
		panel.on("input.chTransferLists", ".ch-tl-search", redraw);
		panel.on("click.chTransferLists", ".ch-tl-refresh", () => this._load());
		// Dates are asked of the server, so they reach past the latest rows;
		// location and status narrow what has come back.
		panel.on("change.chTransferLists", ".ch-tl-from, .ch-tl-to", () => this._load());
		panel.on("change.chTransferLists", ".ch-tl-place, .ch-tl-status", redraw);
		panel.on("click.chTransferLists", ".ch-tl-clear", () => {
			panel.find(".ch-tl-search, .ch-tl-from, .ch-tl-to, .ch-tl-place, .ch-tl-status").val("");
			this.to_receive = false;
			this._load();
		});
		// One click to what is at the door and not yet booked in.
		panel.on("click.chTransferLists", ".ch-tl-to-receive", () => {
			this.to_receive = !this.to_receive;
			redraw();
		});
		// A heading sorts by its column; clicking it again reverses the order.
		panel.on("click.chTransferLists", ".ch-tl-sort", (e) => {
			const key = String($(e.currentTarget).data("key"));
			this.sort = this.sort.key === key ? { key, dir: -this.sort.dir } : { key, dir: 1 };
			redraw();
		});
		panel.on("click.chTransferLists", ".ch-tl-page", (e) => {
			this.page = parseInt($(e.currentTarget).data("page"), 10) || 1;
			this._draw();
		});
		// A challan's number opens what is on it.
		panel.on("click.chTransferLists", ".ch-tl-open", (e) => {
			e.preventDefault();
			this._show_challan(String($(e.currentTarget).data("challan")));
		});
		this._load();
	}

	/** The item rows the server sends, folded into one row per challan. */
	_group_documents(rows) {
		const docs = new Map();
		rows.forEach((r) => {
			let d = docs.get(r.delivery_challan);
			if (!d) {
				d = {
					delivery_challan: r.delivery_challan, transfer: r.transfer,
					latest_status: r.latest_status, delivery_mode: r.delivery_mode,
					delivered_by: r.delivered_by, created: r.created, boxes: flt(r.boxes),
					from_location: r.from_location, delivery_location: r.delivery_location,
					route: `${r.from_location || "—"} → ${r.delivery_location || "—"}`,
					raised_by: r.raised_by, qty: 0, items: [],
				};
				docs.set(r.delivery_challan, d);
			}
			d.qty += flt(r.qty);
			d.items.push(r);
		});
		return [...docs.values()];
	}

	/** Challan -> its items -> an item's IMEIs, in one dialog with a way back. */
	_show_challan(challan) {
		const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
		const doc = this._group_documents(this.rows).find((d) => d.delivery_challan === challan);
		if (!doc) return;
		const dialog = new frappe.ui.Dialog({
			title: __("Transit Details - {0}", [challan]),
			size: "extra-large",
			fields: [{ fieldtype: "HTML", fieldname: "body" }],
		});
		const $w = dialog.fields_dict.body.$wrapper;
		const num = (v) => (flt(v) % 1 ? flt(v).toFixed(2) : String(flt(v)));
		const when = (v) => (v ? frappe.datetime.str_to_user(v) : "");
		const table = (head, body) => `
			<div style="overflow-x:auto">
			<table class="table table-bordered" style="font-size:13px;margin:0;white-space:nowrap">
				<thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
		// The same seven columns close both layers: an item, then each of its IMEIs.
		const TAIL_HEAD = `<th class="text-right">${__("Transferred Qty")}</th>
			<th class="text-right">${__("Accepted Qty")}</th>
			<th class="text-right">${__("Pending Qty")}</th>
			<th>${__("Transfer Status")}</th><th>${__("Latest In Time")}</th>
			<th>${__("Inward Done By")}</th><th>${__("Transfer Duration")}</th>`;
		const tail = (sent, accepted, pending, status, in_time, by, duration) => `
			<td class="text-right">${num(sent)}</td><td class="text-right">${num(accepted)}</td>
			<td class="text-right">${num(pending)}</td><td>${esc(status)}</td>
			<td>${esc(when(in_time))}</td><td>${esc(by)}</td><td>${esc(duration)}</td>`;

		const show_items = () => {
			dialog.set_title(__("Transit Details - {0}", [challan]));
			$w.html(`
				<div style="margin-bottom:10px;color:var(--text-muted);font-size:12.5px">
					${esc(doc.route)} · ${esc(doc.latest_status)}${
						doc.transfer ? ` · ${__("Transfer Request")} ${esc(doc.transfer)}` : ""}
				</div>
				${table(`<th>${__("Item Code")}</th><th>${__("Item Name")}</th>${TAIL_HEAD}`,
					doc.items.map((it, i) => `<tr>
						<td><a href="#" class="ch-tl-item" data-i="${i}" style="font-weight:600">${esc(it.item_code)}</a></td>
						<td style="white-space:normal;min-width:180px">${esc(it.item_name)}</td>
						${tail(it.transferred_qty, it.accepted_qty, it.pending_qty, it.item_status,
							it.in_time, it.inward_by, it.duration)}</tr>`).join(""))}`);
		};
		const show_serials = (it) => {
			dialog.set_title(__("IMEI Details - {0}", [it.item_code]));
			const devices = it.devices || [];
			$w.html(`
				<div style="margin-bottom:10px">
					<a href="#" class="ch-tl-back"><i class="fa fa-arrow-left"></i> ${__("Back to items")}</a>
					<span style="margin-left:10px;color:var(--text-muted);font-size:12.5px">${esc(it.item_name)}</span>
				</div>
				${devices.length
					? table(`<th style="width:50px">#</th><th>${__("IMEI Number")}</th>${TAIL_HEAD}`,
						devices.map((d, i) => `<tr><td>${i + 1}</td>
							<td style="font-family:var(--font-family-monospace,monospace)">${esc(d.serial)}</td>
							${tail(d.qty, d.accepted_qty, d.pending_qty, d.status,
								d.in_time, d.inward_by, d.duration)}</tr>`).join(""))
					: `<div class="text-muted" style="padding:20px;text-align:center">${
						__("No IMEI is recorded for this item on the challan.")}</div>`}`);
		};
		$w.on("click", ".ch-tl-item", (e) => {
			e.preventDefault();
			show_serials(doc.items[parseInt($(e.currentTarget).data("i"), 10)]);
		});
		$w.on("click", ".ch-tl-back", (e) => { e.preventDefault(); show_items(); });
		show_items();
		dialog.show();
	}

	_load() {
		const mode = this.mode;
		const body = this.panel.find(".ch-tl-body");
		body.html(`<div style="padding:40px;text-align:center">
			<i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.3"></i></div>`);
		const args = {
			pos_profile: PosState.pos_profile,
			from_date: this.panel.find(".ch-tl-from").val() || null,
			to_date: this.panel.find(".ch-tl-to").val() || null,
		};
		if (this.spec.view) args.view = this.spec.view;
		frappe.call({
			method: this.spec.method,
			args,
			callback: (r) => {
				if (this.mode !== mode) return;      // the user has moved to another list
				this.rows = (r && r.message) || [];
				this.page = 1;
				this._fill_choices(".ch-tl-place", __("All locations"),
					this.rows.flatMap((x) => this.spec.places.map((k) => x[k])));
				this._fill_choices(".ch-tl-status", __("All statuses"), this.rows.map((x) => x.latest_status));
				this._draw();
			},
			error: () => body.html(`<div class="ch-pos-empty-state" style="padding:40px">
				<div class="empty-title">${__("Could not load this list")}</div></div>`),
		});
	}

	/** Offer, in a filter, the values found on the rows in hand. */
	_fill_choices(selector, all_label, values) {
		const select = this.panel.find(selector);
		const chosen = select.val() || "";
		const choices = [...new Set(values)].filter(Boolean)
			.sort((a, b) => String(a).localeCompare(String(b)));
		if (chosen && !choices.includes(chosen)) choices.unshift(chosen);
		const esc = frappe.utils.escape_html;
		select.html(`<option value="">${all_label}</option>` + choices.map((x) =>
			`<option value="${esc(x)}">${esc(x)}</option>`).join(""));
		select.val(chosen);
	}

	_draw() {
		const spec = this.spec;
		const esc  = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
		const num  = (v) => (flt(v) % 1 ? flt(v).toFixed(2) : String(flt(v)));
		const body = this.panel.find(".ch-tl-body");
		const q    = (this.panel.find(".ch-tl-search").val() || "").trim().toLowerCase();
		const place  = this.panel.find(".ch-tl-place").val() || "";
		const status = this.panel.find(".ch-tl-status").val() || "";
		const waiting = this.rows.filter((r) => r.to_receive).length;
		this.panel.find(".ch-tl-to-receive-count").text(`(${waiting})`);
		this.panel.find(".ch-tl-to-receive")
			.toggleClass("btn-primary", !!this.to_receive).toggleClass("btn-default", !this.to_receive);
		const rows = this.rows.filter((r) => (!this.to_receive || r.to_receive)
			&& (!place || spec.places.some((k) => r[k] === place))
			&& (!status || r.latest_status === status)
			&& (!q || spec.columns.map((c) => r[c.key]).concat([r.item_code, r.rejection_reason, r.doc_status])
				.join(" ").toLowerCase().includes(q)));

		const col = spec.columns.find((c) => c.key === this.sort.key) || spec.columns[1];
		const blank = (v) => (v === null || v === undefined || v === "" ? 1 : 0);
		rows.sort((a, b) => {
			const x = col.value(a), y = col.value(b);
			// An empty cell goes last whichever way the column is sorted.
			if (blank(x) !== blank(y)) return blank(x) - blank(y);
			const cmp = col.number
				? x - y
				: String(x).localeCompare(String(y), undefined, { numeric: true, sensitivity: "base" });
			return cmp * this.sort.dir;
		});

		if (!rows.length) {
			body.html(`<div class="ch-tl-card"><div class="ch-pos-empty-state" style="padding:40px">
				<div class="empty-icon"><i class="fa ${spec.icon}"></i></div>
				<div class="empty-title">${this.rows.length
					? __("Nothing matches these filters") : spec.empty}</div>
			</div></div>`);
			return;
		}

		const pages = Math.ceil(rows.length / PAGE_SIZE);
		this.page = Math.min(Math.max(this.page, 1), pages);
		const start = (this.page - 1) * PAGE_SIZE;
		const shown = rows.slice(start, start + PAGE_SIZE);

		const cell = (c, r) => {
			if (c.of) {
				// Received against what was sent: amber while short, green when complete.
				const got = flt(r[c.key]), total = flt(r[c.of]);
				const color = total && got >= total ? "#15803d" : "#b45309";
				return `<td class="text-right" style="font-weight:600;color:${color}">${num(got)} / ${num(total)}</td>`;
			}
			if (c.number) {
				const v = flt(r[c.key]);
				const color = !v ? "inherit"
					: c.key === "rejected_qty" ? "#dc2626"
					: ["approved_qty", "received_qty"].includes(c.key) ? "#15803d"
					: c.key === "pending_qty" ? "#b45309" : "inherit";
				return `<td class="text-right" style="color:${color}">${num(v)}</td>`;
			}
			if (c.date) return `<td class="ch-tl-muted">${r.date ? esc(frappe.datetime.str_to_user(r.date)) : ""}</td>`;
			if (c.key === "item_name") {
				return `<td style="white-space:normal;min-width:220px">${esc(r.item_name)}</td>`;
			}
			if (c.key === "latest_status") {
				// Why it was refused sits under the status, where the eye already is.
				return `<td>${r.latest_status
					? `<span class="ch-tl-pill ${status_tone(r.latest_status)}">${esc(r.latest_status)}</span>` : "—"}${
					r.rejection_reason ? `<div style="font-size:11.5px;color:#b91c1c;margin-top:3px;white-space:normal;max-width:220px">
						${__("Reason")}: ${esc(r.rejection_reason)}</div>` : ""}</td>`;
			}
			if (c.key === "serials") {
				const serials = String(r.serials || "").split(",").map((x) => x.trim()).filter(Boolean);
				return `<td style="white-space:normal;max-width:280px">${serials.length
					? serials.map((x) => `<span class="ch-tl-imei">${esc(x)}</span>`).join("") : "—"}</td>`;
			}
			if (c.key === "delivery_mode") {
				return `<td>${r.delivery_mode
					? `<span class="ch-tl-tag${r.delivery_mode === "Courier" ? " courier" : ""}">${esc(r.delivery_mode)}</span>`
					: "—"}</td>`;
			}
			if (c.key === "delivery_challan" && r.delivery_challan) {
				return `<td><a href="#" class="ch-tl-id ch-tl-open" data-challan="${esc(r.delivery_challan)}"
					title="${__("Show the items on this challan")}">${esc(r.delivery_challan)}</a></td>`;
			}
			// The first column is the document the row belongs to.
			const first = c === spec.columns[0];
			return `<td${first ? ' class="ch-tl-id"' : ""}>${esc(r[c.key]) || "—"}</td>`;
		};

		// Page numbers: first, last, and the ones around the page in view.
		const near = [...new Set([1, pages, this.page - 1, this.page, this.page + 1])]
			.filter((n) => n >= 1 && n <= pages).sort((a, b) => a - b);
		const page_btn = (n, label, off) => `<button class="ch-tl-page${n === this.page && !label ? " on" : ""}"
			data-page="${n}" ${off ? "disabled" : ""}>${label || n}</button>`;
		const pager = pages > 1 ? `<div class="ch-tl-pages">
			${page_btn(this.page - 1, "‹", this.page === 1)}
			${near.map((n, i) => (i && n - near[i - 1] > 1 ? `<span style="align-self:center">…</span>` : "") + page_btn(n)).join("")}
			${page_btn(this.page + 1, "›", this.page === pages)}</div>` : "";

		body.html(`
			<div class="ch-tl-card">
				<div class="ch-tl-scroll">
				<table class="ch-tl-table">
					<thead><tr>${spec.columns.map((c) => `
						<th class="ch-tl-sort${c.number ? " text-right" : ""}" data-key="${c.key}"
							title="${__("Sort by {0}", [c.label])}">
							${c.label}
							<i class="fa ${this.sort.key === c.key
								? (this.sort.dir > 0 ? "fa-sort-asc" : "fa-sort-desc")
								: "fa-sort"}" style="opacity:${this.sort.key === c.key ? 1 : 0.35};margin-left:4px"></i>
						</th>`).join("")}
					</tr></thead>
					<tbody>${shown.map((r) => `<tr>${spec.columns.map((c) => cell(c, r)).join("")}</tr>`).join("")}
					</tbody>
				</table>
				</div>
				<div class="ch-tl-foot">
					<span class="ch-tl-count">${__("Showing {0}–{1} of {2} item(s)",
						[start + 1, start + shown.length, rows.length])}</span>
					${pager}
				</div>
			</div>`);
	}
}
