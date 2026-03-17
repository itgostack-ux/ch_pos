/**
 * CH POS — Session Opening Screen
 *
 * Shown before POS loads. Cashier must:
 * 1. Select POS Profile (if not auto-resumed)
 * 2. Enter opening cash amount
 * 3. Get manager PIN approval
 *
 * Only after successful session creation does the POS app load.
 */
import { PosState, EventBus } from "../state.js";

const INDIAN_DENOMINATIONS = [2000, 500, 200, 100, 50, 20, 10, 5, 2, 1];

export class SessionOpeningScreen {
	constructor() {
		this._dialog = null;
	}

	/**
	 * Show opening screen. Resolves when session is ready.
	 * @param {Array} open_entries - ERPNext open entries (for resume detection)
	 * @returns {Promise<{session_name, business_date, store}>}
	 */
	show(open_entries) {
		return new Promise((resolve, reject) => {
			this._resolve = resolve;
			// Check if there's an active CH POS Session to resume
			if (open_entries && open_entries.length === 1) {
				this._check_existing_session(open_entries[0], resolve);
			} else {
				this._show_profile_and_opening(open_entries, resolve);
			}
		});
	}

	_check_existing_session(entry, resolve) {
		frappe.call({
			method: "ch_pos.api.session_api.get_session_status",
			args: { pos_profile: entry.pos_profile },
			callback: (r) => {
				const data = r.message || {};
				if (data.has_session) {
					// Active session exists — auto resume
					resolve({
						session_name: data.session_name,
						business_date: data.business_date,
						store: data.store,
						pos_profile: entry.pos_profile,
						company: entry.company,
						opening_entry: entry,
					});
				} else if (data.unclosed_session) {
					// Unclosed session — must close first
					this._show_must_close(data, entry.pos_profile, resolve);
				} else {
					// No session — show opening screen for this profile
					this._show_opening_form(entry.pos_profile, entry.company, resolve);
				}
			},
		});
	}

	_show_must_close(data, pos_profile, resolve) {
		frappe.msgprint({
			title: __("Unclosed Session"),
			message: __("Session {0} from {1} (cashier: {2}) is still open. It must be closed before a new session can start.", [
				`<b>${data.unclosed_session}</b>`,
				data.unclosed_date,
				data.unclosed_user,
			]),
			indicator: "orange",
			primary_action: {
				label: __("Close Session"),
				action: () => {
					// Switch to closing dashboard for the unclosed session
					PosState._unclosed_session = data.unclosed_session;
					EventBus.emit("session:force_close", data.unclosed_session);
				},
			},
		});
	}

	_show_profile_and_opening(open_entries, resolve) {
		open_entries = open_entries || [];
		const open_map = {};
		open_entries.forEach((e) => { open_map[e.pos_profile] = e; });

		const fields = [];

		if (open_entries.length) {
			const names = open_entries.map((e) => `<b>${e.pos_profile}</b>`).join(", ");
			fields.push({
				fieldname: "open_info",
				fieldtype: "HTML",
				options: `<div class="alert alert-info" style="margin-bottom:10px">
					${__("Open ERPNext sessions")}: ${names}
				</div>`,
			});
		}

		fields.push(
			{
				fieldname: "pos_profile",
				fieldtype: "Link",
				label: __("POS Profile"),
				options: "POS Profile",
				reqd: 1,
				default: open_entries.length ? open_entries[0].pos_profile : undefined,
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "opening_cash",
				fieldtype: "Currency",
				label: __("Opening Cash (₹)"),
				reqd: 1,
				default: 0,
				description: __("Count the cash in the drawer and enter the total"),
			},
			{ fieldtype: "Section Break", label: __("Manager Approval") },
			{
				fieldname: "manager_pin",
				fieldtype: "Password",
				label: __("Manager PIN"),
				description: __("4-6 digit PIN for opening approval"),
			},
		);

		const dlg = new frappe.ui.Dialog({
			title: __("Open POS Session"),
			fields,
			size: "large",
			primary_action_label: __("Open Session"),
			primary_action: (values) => {
				const profile = values.pos_profile;
				// First check if profile has active CH session
				frappe.call({
					method: "ch_pos.api.session_api.get_session_status",
					args: { pos_profile: profile },
					callback: (r) => {
						const data = r.message || {};
						if (data.has_session) {
							dlg.hide();
							resolve({
								session_name: data.session_name,
								business_date: data.business_date,
								store: data.store,
								pos_profile: profile,
								company: open_map[profile]?.company,
								opening_entry: open_map[profile],
							});
						} else if (data.unclosed_session) {
							frappe.msgprint(__("Close session {0} first", [data.unclosed_session]));
						} else {
							this._create_session(dlg, profile, values, open_map, resolve);
						}
					},
				});
			},
		});

		// Show expected float hint when profile changes
		dlg.fields_dict.pos_profile.$input.on("change", () => {
			const pp = dlg.get_value("pos_profile");
			if (pp) {
				frappe.xcall("ch_pos.api.session_api.get_session_status", { pos_profile: pp })
					.then((data) => {
						if (data.has_session) {
							dlg.set_df_property("opening_cash", "description",
								__("Session already exists — will auto-resume"));
						}
					});
			}
		});

		dlg.show();
		this._dialog = dlg;
	}

	_show_opening_form(pos_profile, company, resolve) {
		const fields = [
			{
				fieldname: "info",
				fieldtype: "HTML",
				options: `<div class="text-muted" style="margin-bottom:12px">
					${__("Profile")}: <b>${pos_profile}</b>
				</div>`,
			},
			{
				fieldname: "opening_cash",
				fieldtype: "Currency",
				label: __("Opening Cash (₹)"),
				reqd: 1,
				default: 0,
				description: __("Count cash in drawer before starting"),
			},
			{ fieldtype: "Section Break", label: __("Manager Approval") },
			{
				fieldname: "manager_pin",
				fieldtype: "Password",
				label: __("Manager PIN"),
				description: __("4-6 digit PIN for opening approval"),
			},
		];

		const dlg = new frappe.ui.Dialog({
			title: __("Open POS Session"),
			fields,
			primary_action_label: __("Start Session"),
			primary_action: (values) => {
				this._create_session(dlg, pos_profile, values, {}, resolve, company);
			},
		});
		dlg.show();
		this._dialog = dlg;
	}

	_create_session(dlg, pos_profile, values, open_map, resolve, company) {
		dlg.disable_primary_action();
		frappe.call({
			method: "ch_pos.api.session_api.open_session",
			args: {
				pos_profile: pos_profile,
				opening_cash: values.opening_cash || 0,
				manager_pin: values.manager_pin || null,
			},
			callback: (r) => {
				if (r.message) {
					dlg.hide();
					frappe.show_alert({
						message: __("Session opened — Business Date: {0}", [r.message.business_date]),
						indicator: "green",
					});
					resolve({
						session_name: r.message.session_name,
						business_date: r.message.business_date,
						store: r.message.store,
						pos_profile: pos_profile,
						company: company || open_map[pos_profile]?.company,
						opening_entry: { pos_profile, company: company || open_map[pos_profile]?.company },
					});
				}
			},
			error: () => {
				dlg.enable_primary_action();
			},
		});
	}

	destroy() {
		if (this._dialog) {
			this._dialog.hide();
			this._dialog = null;
		}
	}
}
