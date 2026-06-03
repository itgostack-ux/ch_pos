/**
 * CH POS — Manager PIN UX cleanup (TC_018).
 *
 * Frappe's default Password control shows a strength meter and the help
 * text "Include symbols, numbers and capital letters in the password".
 * Manager PINs are 4–6 numeric digits by policy; the generic password
 * strength tooltip contradicts the rule and confuses operators.
 *
 * We monkey-patch ControlPassword so that, for any field whose name or
 * label looks like a PIN, we:
 *   1. Skip the strength scoring API call.
 *   2. Replace the help text with the corporate PIN rule.
 *   3. Add a numeric inputmode + length cap (4–6 digits).
 */

(function () {
    "use strict";

    if (!window.frappe || !frappe.ui || !frappe.ui.form || !frappe.ui.form.ControlPassword) {
        return;
    }

    const ControlPassword = frappe.ui.form.ControlPassword;

    function _is_pin_field(df) {
        if (!df) return false;
        const fname = (df.fieldname || "").toLowerCase();
        const label = (df.label || "").toLowerCase();
        return /(^|_)pin(_|$)/.test(fname) || /\bpin\b/.test(label);
    }

    const _orig_make_input = ControlPassword.prototype.make_input;
    ControlPassword.prototype.make_input = function () {
        _orig_make_input.apply(this, arguments);
        if (_is_pin_field(this.df)) {
            // Disable Frappe's default strength meter — the message it shows
            // ("Include symbols, numbers and capital letters") contradicts
            // the corporate 4–6 digit numeric PIN policy.
            this.enable_password_checks = false;

            // Numeric keyboard on mobile + cap length client-side.
            try {
                this.$input.attr("inputmode", "numeric");
                this.$input.attr("maxlength", "6");
                this.$input.attr("pattern", "[0-9]{4,6}");
                this.$input.attr("autocomplete", "one-time-code");
            } catch (e) { /* non-fatal */ }

            // Replace the strength help-box with a clear rule.
            try {
                if (this.message && this.message.length) {
                    this.message
                        .removeClass("hidden")
                        .text(__("Enter your 4–6 digit numeric Manager PIN."));
                }
                if (this.indicator && this.indicator.length) {
                    this.indicator.addClass("hidden");
                }
            } catch (e) { /* non-fatal */ }
        }
    };

    const _orig_get_strength = ControlPassword.prototype.get_password_strength;
    ControlPassword.prototype.get_password_strength = function (value) {
        if (_is_pin_field(this.df)) {
            return; // Bypass strength meter entirely for PIN fields.
        }
        return _orig_get_strength.apply(this, arguments);
    };
})();
