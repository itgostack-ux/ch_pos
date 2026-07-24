"""Guards for outbound requests that carry credentials.

The caller must provide an exact hostname allowlist.  URL validation and the
request live in the same helper so a future caller cannot accidentally enable
redirects and forward an Authorization header to another host.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _


def parse_exact_host_allowlist(raw_hosts, *, label: str) -> set[str]:
	hosts = {
		entry.strip().lower().rstrip(".")
		for entry in str(raw_hosts or "").replace(",", "\n").splitlines()
		if entry.strip()
	}
	if not hosts:
		frappe.throw(
			_("Configure at least one allowed host for {0}.").format(label),
			frappe.ValidationError,
		)
	for host in hosts:
		if (
			"://" in host
			or "/" in host
			or "@" in host
			or ":" in host
			or any(character.isspace() for character in host)
		):
			frappe.throw(
				_("The {0} host allowlist contains an invalid hostname.").format(label),
				frappe.ValidationError,
			)
		try:
			ascii_host = host.encode("idna").decode("ascii")
		except UnicodeError:
			frappe.throw(_("The {0} host allowlist is invalid.").format(label))
		if ascii_host != host:
			frappe.throw(
				_("Use ASCII hostnames in the {0} host allowlist.").format(label),
				frappe.ValidationError,
			)
		try:
			ipaddress.ip_address(host)
		except ValueError:
			pass
		else:
			frappe.throw(
				_("IP literals are not permitted in the {0} host allowlist.").format(label),
				frappe.ValidationError,
			)
	return hosts


def validate_allowed_https_url(endpoint, allowed_hosts: set[str], *, label: str) -> str:
	endpoint = str(endpoint or "").strip()
	if not endpoint or "\\" in endpoint or any(character.isspace() for character in endpoint):
		frappe.throw(_("{0} URL is invalid.").format(label), frappe.ValidationError)
	try:
		parsed = urlsplit(endpoint)
		port = parsed.port
	except ValueError:
		frappe.throw(_("{0} URL is invalid.").format(label), frappe.ValidationError)
	hostname = (parsed.hostname or "").lower().rstrip(".")
	if (
		parsed.scheme.lower() != "https"
		or not hostname
		or parsed.username
		or parsed.password
		or parsed.fragment
		or port not in (None, 443)
	):
		frappe.throw(
			_("{0} must use HTTPS on port 443 without embedded credentials.").format(label),
			frappe.ValidationError,
		)
	if hostname not in allowed_hosts:
		frappe.throw(
			_("{0} host {1} is not allowlisted.").format(label, hostname),
			frappe.PermissionError,
		)

	# Refuse destinations that currently resolve to an internal network.  The
	# exact hostname allowlist is the primary boundary; this resolution check
	# also prevents an allowlisted typo/custom host from becoming a basic SSRF
	# primitive.
	try:
		addresses = {
			row[4][0]
			for row in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
		}
	except (socket.gaierror, OSError):
		frappe.throw(_("{0} host could not be resolved safely.").format(label))
	if not addresses:
		frappe.throw(_("{0} host did not resolve to an address.").format(label))
	for address in addresses:
		try:
			parsed_address = ipaddress.ip_address(address)
		except ValueError:
			frappe.throw(_("{0} resolved to an invalid address.").format(label))
		if not parsed_address.is_global:
			frappe.throw(
				_("{0} cannot target a private, loopback, or link-local address.").format(label),
				frappe.PermissionError,
			)
	return endpoint


def post_json_with_bearer(
	endpoint,
	*,
	allowed_hosts: set[str],
	label: str,
	api_key: str,
	payload: dict,
	timeout: int,
	max_response_bytes: int = 1048576,
):
	"""POST JSON without ever forwarding credentials across a redirect."""
	return post_json_to_allowed_https(
		endpoint,
		allowed_hosts=allowed_hosts,
		label=label,
		headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
		payload=payload,
		timeout=timeout,
		max_response_bytes=max_response_bytes,
	)


def post_json_to_allowed_https(
	endpoint,
	*,
	allowed_hosts: set[str],
	label: str,
	headers: dict,
	payload: dict,
	timeout: int,
	max_response_bytes: int = 1048576,
):
	"""POST JSON to a validated public destination with a bounded response."""
	endpoint = validate_allowed_https_url(endpoint, allowed_hosts, label=label)
	response = requests.post(
		endpoint,
		headers=headers,
		json=payload,
		timeout=timeout,
		allow_redirects=False,
		stream=True,
	)
	if 300 <= response.status_code < 400:
		response.close()
		frappe.throw(_("{0} redirects are not permitted.").format(label), frappe.ValidationError)
	limit = max(1024, min(int(max_response_bytes or 1048576), 10485760))
	body = bytearray()
	for chunk in response.iter_content(chunk_size=16384):
		if not chunk:
			continue
		body.extend(chunk)
		if len(body) > limit:
			response.close()
			frappe.throw(_("{0} response exceeded the configured size limit.").format(label))
	response._content = bytes(body)
	response._content_consumed = True
	return response
