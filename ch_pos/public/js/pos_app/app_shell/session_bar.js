/**
 * CH POS — Session Bar
 *
 * Persistent top bar showing store identity, operator, business date,
 * session status, and keyboard shortcut hints.
 */
import { PosState, EventBus } from "../state.js";

export class SessionBar {
	constructor(wrapper) {
		this.wrapper = wrapper;
		this._render();
		this._bind();
	}

	_render() {
		this.wrapper.html(`
			<div class="ch-session-bar">
				<div class="ch-session-left">
					<span class="ch-session-store">
						<i class="fa fa-store"></i>
						<span class="ch-session-store-name">${frappe.utils.escape_html(PosState.store || "")}</span>
					</span>
					<span class="ch-session-divider">|</span>
					<span class="ch-session-operator">
						<i class="fa fa-user-circle-o"></i>
						<span class="ch-session-user">${frappe.utils.escape_html(frappe.session.user_fullname || frappe.session.user)}</span>
					</span>
				</div>
				<div class="ch-session-center">
					<span class="ch-session-status-dot"></span>
					<span class="ch-session-status-text">${__("Session")}: <b class="ch-session-status-val">${frappe.utils.escape_html(PosState.session_status || "—")}</b></span>
					<span class="ch-session-divider">|</span>
					<span class="ch-session-date">
						<i class="fa fa-calendar-o"></i>
						<span class="ch-session-date-val">${PosState.business_date || frappe.datetime.nowdate()}</span>
					</span>
				</div>
				<div class="ch-session-right">
					<div class="ch-session-shortcuts-wrap">
						<span class="ch-session-shortcut" title="${__("Search")}">F2 ${__("Search")}</span>
						<span class="ch-session-shortcut" title="${__("Pay")}">F8 ${__("Pay")}</span>
						<span class="ch-session-shortcut" title="${__("Hold")}">F5 ${__("Hold")}</span>
						<span class="ch-session-shortcut" title="${__("Cancel")}">Esc ${__("Cancel")}</span>
					</div>
					<button type="button" class="ch-session-refresh-btn"
						title="${__("Refresh current screen (F9)")}"
						aria-label="${__("Refresh")}">
						<i class="fa fa-refresh"></i>
					</button>
					<div class="ch-session-profile-slot"></div>
				</div>
			</div>
		`);
		EventBus.emit("sessionbar:rendered", this.wrapper);
	}

	_bind() {
		// Update session status when it changes
		EventBus.on("session:status_changed", (status) => {
			this.wrapper.find(".ch-session-status-val").text(status || "—");
			const dot = this.wrapper.find(".ch-session-status-dot");
			dot.removeClass("status-open status-locked status-closed");
			if (status === "Open") dot.addClass("status-open");
			else if (status === "Locked") dot.addClass("status-locked");
			else dot.addClass("status-closed");
		});

		// Update store info after profile loads
		EventBus.on("profile:loaded", () => {
			this.wrapper.find(".ch-session-store-name").text(PosState.store || "");
			this.wrapper.find(".ch-session-date-val").text(PosState.business_date || frappe.datetime.nowdate());
			this.wrapper.find(".ch-session-status-val").text(PosState.session_status || "—");
		});

		// Global Refresh — re-renders the active workspace by re-emitting mode:switch.
		// Each module's workspace listens to "workspace:render" and rebuilds from data.
		// Modules can additionally listen to EventBus.on("workspace:refresh") for a
		// lighter refresh (e.g. just reload data without re-rendering chrome).
		this.wrapper.on("click", ".ch-session-refresh-btn", (e) => {
			const $btn = $(e.currentTarget);
			$btn.addClass("is-spinning");
			EventBus.emit("workspace:refresh", { mode: PosState.active_mode });
			if (PosState.active_mode) {
				EventBus.emit("mode:switch", PosState.active_mode);
			}
			setTimeout(() => $btn.removeClass("is-spinning"), 600);
		});

		// F9 keyboard shortcut for global refresh.
		$(document).on("keydown.ch-session-refresh", (ev) => {
			if (ev.key === "F9" && !ev.ctrlKey && !ev.altKey && !ev.metaKey) {
				// Ignore when typing in an input or when a modal/payment overlay is open.
				const tag = (ev.target && ev.target.tagName) || "";
				if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
				if ($(".modal.show").length || $(".ch-pay-overlay.ch-pay-visible").length) return;
				ev.preventDefault();
				this.wrapper.find(".ch-session-refresh-btn").trigger("click");
			}
		});
	}
}
