/**
 * CH POS — Service Inbox
 *
 * Requests that arrived before the customer did: a website form, the app, a
 * WhatsApp message, a call somebody logged. They are not tickets yet -- no
 * device has been handed over -- so they live apart from the repair queue.
 *
 * The counter's job here is small and specific: see who is waiting on a reply,
 * record what was said, and when the person walks in, carry what they already
 * told us into the intake instead of asking them to say it twice.
 *
 * Same server endpoints as the desk hub, so the two views can never disagree.
 */
import { PosState, EventBus } from "../../state.js";

const API = "gofix.gofix_services.page.service_inbox.service_inbox";
const INBOX = "gofix.gofix_services.inbox";

export class ServiceInboxWorkspace {
	constructor() {
		this._rows = [];
		this._selected = null;
		this._status = "Open";
		this._channel = "";
		this._search = "";
		this._ctx = null;
		EventBus.on("workspace:render", (ctx) => {
			if (ctx.mode !== "service_inbox") return;
			this.render(ctx.panel);
		});
	}

	async render(panel) {
		this.panel = panel;
		panel.html(`
			<div class="ch-pos-mode-panel ch-si">
				<div class="ch-si-bar">
					<select class="ch-si-f" data-f="_status"
					        aria-label="${__("Filter by status")}"></select>
					<select class="ch-si-f" data-f="_channel"
					        aria-label="${__("Filter by channel")}"></select>
					<input type="search" class="ch-si-search"
					       aria-label="${__("Search by phone or name")}"
					       placeholder="${__("Phone or name")}">
					<span class="ch-si-spacer"></span>
					<button class="ch-si-btn is-icon ch-si-refresh" title="${__("Refresh")}"
					        aria-label="${__("Refresh")}"><i class="fa fa-refresh"></i></button>
					<button class="ch-si-btn is-primary ch-si-log">
						<i class="fa fa-plus"></i> ${__("Log A Request")}</button>
				</div>
				<div class="ch-si-stats"></div>
				<div class="ch-si-body">
					<div class="ch-si-list"></div>
					<div class="ch-si-detail"></div>
				</div>
			</div>`);

		this._ctx = await frappe.xcall(`${API}.get_context_data`, {
			company: PosState.active_company || "",
		});
		this._paint_filters();
		this._bind(panel);
		this.load();
	}

	_paint_filters() {
		const sel = (f, opts) => {
			const $s = this.panel.find(`.ch-si-f[data-f="${f}"]`);
			$s.html(opts.map(([v, l]) =>
				`<option value="${v}" ${this[f] === v ? "selected" : ""}>${l}</option>`).join(""));
		};
		sel("_status", [["Open", __("Open")]].concat(
			(this._ctx.statuses || []).map((s) => [s, __(s)])));
		sel("_channel", [["", __("All Channels")]].concat(
			(this._ctx.channels || []).map((c) => [c, __(c)])));
	}

	_bind(panel) {
		panel.on("change", ".ch-si-f", (e) => {
			this[$(e.currentTarget).data("f")] = e.currentTarget.value;
			this.load();
		});
		// Typing a phone number should not fire a query per keystroke.
		panel.on("keydown", ".ch-si-search", (e) => {
			if (e.key === "Enter") { this._search = e.currentTarget.value; this.load(); }
		});
		panel.on("blur", ".ch-si-search", (e) => {
			if ((this._search || "") !== e.currentTarget.value) {
				this._search = e.currentTarget.value; this.load();
			}
		});
		panel.on("click", ".ch-si-refresh", () => this.load());
		panel.on("click", ".ch-si-log", () => this._log_dialog());
		panel.on("click", ".ch-si-card", (e) => this._open($(e.currentTarget).data("name")));
		panel.on("keydown", ".ch-si-card", (e) => {
			if (e.key === "Enter" || e.key === " ") {
				e.preventDefault();
				this._open($(e.currentTarget).data("name"));
			}
		});
		panel.on("click", ".ch-si-stat[data-status]", (e) => {
			const s = $(e.currentTarget).data("status");
			if (!s) return;
			this._status = s;
			this.panel.find('.ch-si-f[data-f="_status"]').val(s);
			this.load();
		});
	}

	async load() {
		this.panel.find(".ch-si-list").html(
			`<div class="ch-si-empty"><i class="fa fa-circle-o-notch fa-spin"></i>${
				__("Loading…")}</div>`);
		const r = await frappe.xcall(`${API}.get_requests`, {
			company: PosState.active_company || "",
			status: this._status, channel: this._channel, search: this._search,
		});
		this._rows = r.rows || [];
		this._paint_stats(r.counts || {});
		this._paint_list();
		if (this._selected && !this._rows.find((x) => x.name === this._selected)) {
			this._selected = null;
		}
		if (this._selected) this._open(this._selected);
		else this.panel.find(".ch-si-detail").html(`
			<div class="ch-si-empty">
				<i class="fa fa-comments-o"></i>
				<b>${__("Nothing selected")}</b>
				${__("Pick a request to see what the customer told us.")}
			</div>`);
	}

	_paint_stats(c) {
		const tile = (label, n, key, tone) => `
			<div class="ch-si-stat ${tone || ""} ${this._status === key ? "active" : ""}"
			     data-status="${key || ""}">
				<b>${n || 0}</b><span>${label}</span></div>`;
		this.panel.find(".ch-si-stats").html([
			tile(__("Open"), c.Open, "Open", "hot"),
			tile(__("New"), c.New, "New"),
			tile(__("Contacted"), c.Contacted, "Contacted"),
			tile(__("Scheduled"), c.Scheduled, "Scheduled"),
			tile(__("Converted"), c.Converted, "Converted", "good"),
			tile(__("Today"), c.Today, ""),
		].join(""));
	}

	_paint_list() {
		const $l = this.panel.find(".ch-si-list");
		if (!this._rows.length) {
			$l.html(`
				<div class="ch-si-empty">
					<i class="fa fa-inbox"></i>
					<b>${__("Nothing waiting")}</b>
					${__("Requests from the website, the app, WhatsApp or a phone call land here.")}
				</div>`);
			return;
		}
		const esc = frappe.utils.escape_html;
		$l.html(this._rows.map((r) => {
			const age = r.age_hours >= 24
				? __("{0}d", [Math.floor(r.age_hours / 24)])
				: __("{0}h", [Math.round(r.age_hours)]);
			const bits = [r.device_brand, r.device_model, r.issue_category]
				.filter(Boolean).join(" · ");
			return `
			<div class="ch-si-card${this._selected === r.name ? " sel" : ""}${
				r.awaiting_response && r.age_hours > 4 ? " stale" : ""}"
			     data-name="${r.name}" role="button" tabindex="0">
				<div class="ch-si-row1">
					<span class="ch-si-chan" data-c="${esc(r.channel || "")}">${esc(r.channel || "")}</span>
					<span class="ch-si-age">${age}</span>
				</div>
				<div class="ch-si-who">${esc(r.customer_name || __("Unknown caller"))}
					<span class="ch-si-phone">${esc(r.contact_number || "")}</span></div>
				${bits ? `<div class="ch-si-bits">${esc(bits)}</div>` : ""}
				${r.issue_description
					? `<div class="ch-si-said">${esc(r.issue_description)}</div>` : ""}
				<div class="ch-si-row2">
					<span class="ch-si-status s-${esc(r.status)}">${esc(r.status)}</span>
					${r.service_request ? `<span class="ch-si-conv">${esc(r.service_request)}</span>` : ""}
				</div>
			</div>`;
		}).join(""));
	}

	async _open(name) {
		this._selected = name;
		this.panel.find(".ch-si-card").removeClass("sel");
		this.panel.find(`.ch-si-card[data-name="${name}"]`).addClass("sel");
		const $d = this.panel.find(".ch-si-detail");
		$d.html(`<div class="ch-si-empty">${__("Loading…")}</div>`);

		const d = await frappe.xcall(`${API}.get_request`, { name });
		const esc = frappe.utils.escape_html;
		// Only the facts we actually hold. An empty row teaches nobody anything
		// and makes a sparse request look like a broken screen.
		const fact = (l, v) => v
			? `<div class="ch-si-fact"><dt>${l}</dt><dd>${esc(String(v))}</dd></div>` : "";
		const facts = [
			fact(__("Channel"), d.channel),
			fact(__("Received"), (d.received_at || "").slice(0, 16)),
			fact(__("Phone"), d.contact_number),
			fact(__("Email"), d.email),
			fact(__("Known customer"), d.customer),
			fact(__("Category"), d.device_category),
			fact(__("Brand"), d.device_brand),
			fact(__("Model"), d.device_model),
			fact(__("IMEI / Serial"), d.serial_no),
			fact(__("Issue"), d.issue_category),
			fact(__("Preferred slot"), d.preferred_datetime),
			fact(__("Heard about us via"), d.referral_source),
		].join("");

		const notes = (d.notes || []).length
			? `<ul class="ch-si-notes">${d.notes.map((n) => `
				<li class="ch-si-note">
					<span class="ch-si-note-meta">${esc((n.note_datetime || "").slice(0, 16))}
						· ${esc(n.noted_by || "")}</span>
					<div class="ch-si-note-body">${esc(n.note)}</div>
				</li>`).join("")}</ul>`
			: `<p class="ch-si-note-body" style="color:var(--pos-text-muted)">${
				__("Nothing recorded yet.")}</p>`;

		$d.html(`
			<div class="ch-si-head">
				<div>
					<div class="ch-si-title">${esc(d.customer_name || __("Unknown caller"))}
						<span class="ch-si-status s-${esc(d.status)}">${esc(d.status)}</span></div>
					<div class="ch-si-sub">${esc(d.name)} · ${esc(d.contact_number || "")}</div>
				</div>
				<div class="ch-si-actions"></div>
			</div>
			${d.issue_description
				? `<div class="ch-si-quote">${esc(d.issue_description)}</div>` : ""}
			<div class="ch-si-section">${__("What the customer told us")}</div>
			<dl class="ch-si-facts">${facts}</dl>
			<div class="ch-si-section">${__("Conversation")}</div>
			${notes}
			${d.service_request
				? `<div class="ch-si-converted"><i class="fa fa-check-circle"></i>
					${__("Booked in as {0}", [esc(d.service_request)])}</div>` : ""}`);

		this._paint_actions(d);
	}

	_paint_actions(d) {
		const $a = this.panel.find(".ch-si-actions");
		const btn = (label, cls, fn) =>
			$(`<button class="ch-si-btn ${cls}">${label}</button>`).on("click", fn).appendTo($a);

		btn(__("Add Note"), "", () => this._note_dialog(d));
		if (["Converted", "Closed", "Spam", "Duplicate"].includes(d.status)) return;

		// The point of having this inside the till: the customer is standing
		// here, so the intake opens already filled in.
		btn(__("Book The Device In"), "is-primary", () => this._book_in(d));
		btn(__("Close"), "", () => this._close_dialog(d));
	}

	_book_in(d) {
		// Straight into the repair intake in this same POS session -- no page
		// change, no re-login, nothing retyped.
		try {
			sessionStorage.setItem("gofix_inbox_handoff",
				JSON.stringify({ request: d.name, phone: d.contact_number }));
		} catch (e) { /* route_options below still carry it */ }
		frappe.route_options = { inbox_request: d.name, phone: d.contact_number };
		EventBus.emit("mode:set", "repair");
		EventBus.emit("mode:switch", "repair");
		frappe.show_alert({
			message: __("Booking in {0} — their details are filled in", [
				d.customer_name || d.contact_number]),
			indicator: "green",
		});
	}

	_note_dialog(d) {
		const dl = new frappe.ui.Dialog({
			title: __("Record What Was Said"),
			fields: [
				{ fieldname: "note", fieldtype: "Small Text", label: __("Note"), reqd: 1 },
				{ fieldname: "channel", fieldtype: "Select", label: __("Via"),
				  options: (this._ctx.channels || []).join("\n"), default: d.channel },
			],
			primary_action_label: __("Save"),
			primary_action: (v) => frappe.xcall(`${API}.add_note`, {
				name: d.name, note: v.note, channel: v.channel,
			}).then(() => { dl.hide(); this.load(); }),
		});
		dl.show();
	}

	_close_dialog(d) {
		const dl = new frappe.ui.Dialog({
			title: __("Close This Request"),
			fields: [
				{ fieldname: "status", fieldtype: "Select", label: __("Outcome"),
				  options: "Closed\nSpam\nDuplicate", default: "Closed", reqd: 1 },
				{ fieldname: "reason", fieldtype: "Small Text", label: __("Why"), reqd: 1 },
			],
			primary_action_label: __("Close"),
			primary_action: (v) => frappe.xcall(`${INBOX}.set_status`, {
				inbox: d.name, status: v.status, reason: v.reason,
			}).then(() => { dl.hide(); this._selected = null; this.load(); }),
		});
		dl.show();
	}

	_log_dialog() {
		const dl = new frappe.ui.Dialog({
			title: __("Log A Request"),
			fields: [
				{ fieldname: "channel", fieldtype: "Select", label: __("Channel"),
				  options: (this._ctx.channels || []).join("\n"), default: "Phone Call", reqd: 1 },
				{ fieldname: "contact_number", fieldtype: "Data", label: __("Contact Number"), reqd: 1 },
				{ fieldname: "customer_name", fieldtype: "Data", label: __("Name") },
				{ fieldtype: "Column Break" },
				{ fieldname: "device_brand", fieldtype: "Link", options: "Brand", label: __("Brand") },
				{ fieldname: "issue_category", fieldtype: "Link", options: "Issue Category",
				  label: __("Issue") },
				{ fieldname: "preferred_datetime", fieldtype: "Datetime", label: __("Preferred Slot") },
				{ fieldtype: "Section Break" },
				{ fieldname: "issue_description", fieldtype: "Small Text",
				  label: __("What did they say?") },
			],
			primary_action_label: __("Log It"),
			primary_action: (v) => frappe.xcall(`${INBOX}.push_request`, {
				...v, company: PosState.active_company || "",
			}).then((r) => {
				dl.hide();
				frappe.show_alert({ message: r.message, indicator: "green" });
				this._selected = r.name;
				this.load();
			}),
		});
		dl.show();
	}
}
