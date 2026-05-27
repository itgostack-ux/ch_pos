/**
 * CH POS — Camera Barcode/IMEI Scanner (Phase 2)
 *
 * Uses ZXing-JS (lazy-loaded from CDN, falls back to keyboard wedge).
 * `open_camera_scan(onResult)` opens a fullscreen modal with a live
 * camera preview and invokes `onResult(code)` on the first stable decode.
 *
 * Browser permission policy: requires HTTPS or http://127.0.0.1 to access
 * `getUserMedia`. On unsupported environments we surface a clean toast
 * and let the user fall back to the IMEI text input.
 *
 * Reuse-first: we do NOT bundle the library — loaded once via
 * `frappe.require` from jsdelivr, cached by the SW for offline use.
 */
import { EventBus } from "../state.js";

const ZXING_CDN = "https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js";

let _zxing_loading = null;

function _load_zxing() {
	if (window.ZXing) return Promise.resolve(window.ZXing);
	if (_zxing_loading) return _zxing_loading;
	_zxing_loading = new Promise((resolve, reject) => {
		const s = document.createElement("script");
		s.src = ZXING_CDN;
		s.async = true;
		s.onload = () => (window.ZXing ? resolve(window.ZXing) : reject(new Error("ZXing not exposed")));
		s.onerror = () => reject(new Error("Failed to load ZXing from CDN"));
		document.head.appendChild(s);
	});
	return _zxing_loading;
}

function _supports_camera() {
	return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
}

/**
 * Open a fullscreen camera scanner.
 * @param {(code: string) => void} on_result  Called once with the decoded text.
 * @returns {Promise<string|null>}  Resolves with the code, or null if cancelled.
 */
export function open_camera_scan(on_result) {
	return new Promise((resolve) => {
		if (!_supports_camera()) {
			frappe.show_alert({
				message: __("Camera not available on this device. Use the IMEI text box."),
				indicator: "orange",
			});
			resolve(null);
			return;
		}

		const overlay = $(`
			<div class="ch-cam-scan-overlay" style="
				position:fixed;inset:0;background:rgba(0,0,0,0.92);z-index:9999;
				display:flex;flex-direction:column;align-items:center;justify-content:center;">
				<div style="position:absolute;top:14px;right:18px;">
					<button class="btn btn-default ch-cam-close" style="background:#fff">
						<i class="fa fa-times"></i> ${__("Close")}
					</button>
				</div>
				<div style="color:#fff;font-size:15px;margin-bottom:14px;">
					<i class="fa fa-barcode"></i> ${__("Point the camera at a barcode / IMEI")}
				</div>
				<video class="ch-cam-video" autoplay muted playsinline
					style="max-width:90vw;max-height:60vh;border-radius:12px;background:#000;"></video>
				<div class="ch-cam-status text-muted" style="margin-top:14px;color:#cbd5e1;font-size:13px;">
					${__("Loading scanner…")}
				</div>
			</div>
		`);
		$("body").append(overlay);

		let reader = null;
		let stream = null;
		let resolved = false;

		const cleanup = () => {
			try { reader && reader.reset(); } catch (e) {}
			try { stream && stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
			overlay.remove();
		};

		const done = (code) => {
			if (resolved) return;
			resolved = true;
			cleanup();
			if (code && typeof on_result === "function") on_result(code);
			resolve(code || null);
		};

		overlay.on("click", ".ch-cam-close", () => done(null));

		_load_zxing()
			.then((ZX) => {
				const hints = new Map();
				const formats = [
					ZX.BarcodeFormat.CODE_128,
					ZX.BarcodeFormat.CODE_39,
					ZX.BarcodeFormat.EAN_13,
					ZX.BarcodeFormat.EAN_8,
					ZX.BarcodeFormat.UPC_A,
					ZX.BarcodeFormat.UPC_E,
					ZX.BarcodeFormat.QR_CODE,
					ZX.BarcodeFormat.DATA_MATRIX,
				];
				hints.set(ZX.DecodeHintType.POSSIBLE_FORMATS, formats);
				hints.set(ZX.DecodeHintType.TRY_HARDER, true);

				reader = new ZX.BrowserMultiFormatReader(hints);
				const video = overlay.find("video.ch-cam-video")[0];
				overlay.find(".ch-cam-status").text(__("Starting camera…"));

				reader
					.decodeFromConstraints(
						{ video: { facingMode: { ideal: "environment" } } },
						video,
						(result, err) => {
							if (result) {
								const text = (result.getText() || "").trim();
								if (text) {
									EventBus.emit("camera:scan", text);
									done(text);
								}
							}
						},
					)
					.then(() => {
						overlay.find(".ch-cam-status").text(
							__("Hold steady — scanning…"),
						);
					})
					.catch((err) => {
						console.error("[ch_pos] camera_scanner start failed", err);
						overlay.find(".ch-cam-status").text(
							__("Camera permission denied or unavailable."),
						);
					});
			})
			.catch((err) => {
				console.error("[ch_pos] zxing load failed", err);
				overlay.find(".ch-cam-status").text(
					__("Scanner library unavailable — use the IMEI text box."),
				);
			});
	});
}
