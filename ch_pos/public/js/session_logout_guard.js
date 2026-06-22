/**
 * Session Logout Guard — Queue Queue Request Enforcement
 * Prevents user logout when pending (unbilled/unrejected) tokens exist.
 */

(function() {
	// Register session-end guard early in app lifecycle
	frappe.ready(() => {
		frappe.ui.form.on("User", {
			setup: function(frm) {
				// This allows us to intercept logout attempts
			},
		});

		// Hook into logout button clicks
		$(document).on("click", ".navbar-logout, [data-label='Log Out'], [data-label='Logout']", function(e) {
			e.preventDefault();
			e.stopPropagation();
			ch_pos.session.check_pending_tokens_before_logout();
			return false;
		});

		// Also hook into frappe's own logout mechanism
		frappe.session.user_logged_in_original = frappe.session.user_logged_in;
		frappe.logout = ch_pos.session.safe_logout;
	});

	// Namespace
	window.ch_pos = window.ch_pos || {};
	window.ch_pos.session = {};

	/**
	 * Check for pending tokens and warn user before logout.
	 */
	ch_pos.session.check_pending_tokens_before_logout = async function() {
		try {
			const result = await frappe.call({
				method: "ch_pos.api.session_validation.get_pending_tokens_for_store",
				callback: (r) => {
					const pending_info = r.message;

					if (pending_info.count === 0) {
						// No pending tokens, safe to logout
						ch_pos.session.do_logout();
						return;
					}

					// Show warning dialog
					frappe.confirm(
						`<div class="alert alert-danger">
							<strong>Pending Queue Tokens!</strong><br>
							<p>${pending_info.count} token(s) are still pending and must be handled before logout:</p>
							<ul>
								${pending_info.tokens.map(t => 
									`<li><strong>${t.name}</strong> (${t.status}) — ${t.customer_name || 'N/A'}</li>`
								).join('')}
							</ul>
							<p><small>Please bill or close out all tokens at the Store Queue before logging out.</small></p>
						</div>`,
						{
							title: "Cannot Logout — Queue Tokens Pending",
							on_yes: () => {
								// User acknowledges but still wants to stay
								frappe.show_alert({ message: "Please handle pending tokens", indicator: "red" });
							},
						}
					);
				},
				error: (r) => {
					console.error("Error checking pending tokens:", r);
					// On error, allow logout (don't block)
					ch_pos.session.do_logout();
				},
			});
		} catch (e) {
			console.error("Exception in pending token check:", e);
			ch_pos.session.do_logout();
		}
	};

	/**
	 * Safe logout — checks for pending tokens first.
	 */
	ch_pos.session.safe_logout = async function() {
		// First check if we need to validate
		const needs_validation = await frappe.db.get_value(
			"User",
			frappe.session.user,
			["name"]
		).then((r) => {
			// User exists, check if they're a POS user
			return frappe.db.exists("CH POS User", { user: frappe.session.user })
				.then((exists) => exists);
		}).catch(() => false);

		if (needs_validation) {
			ch_pos.session.check_pending_tokens_before_logout();
		} else {
			// Non-POS user, safe to logout
			window.location.href = frappe.urllib.get_full_url("/app/logout");
		}
	};

	/**
	 * Actually perform logout.
	 */
	ch_pos.session.do_logout = function() {
		window.location.href = frappe.urllib.get_full_url("/app/logout");
	};

	/**
	 * Dashboard widget: Show pending token count (optional).
	 * Can be displayed on user's desk.
	 */
	ch_pos.session.refresh_pending_token_widget = function() {
		frappe.call({
			method: "ch_pos.api.session_validation.get_pending_token_count_for_user",
			callback: (r) => {
				const count = r.message || 0;
				if (count > 0) {
					frappe.show_alert({
						message: `${count} queue token(s) pending — please handle before logout`,
						indicator: "warning",
					});
				}
			},
		});
	};

	// Optional: Refresh on page focus (user returns from another app)
	$(window).on("focus", () => {
		if (frappe.session.user && cur_frm === undefined) {
			// Only check if on desk/home, not in a form
			ch_pos.session.refresh_pending_token_widget();
		}
	});

})();
