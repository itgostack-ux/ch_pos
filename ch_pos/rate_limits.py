"""Atomic, fixed-window counters for security-sensitive POS operations."""

from __future__ import annotations

import hashlib

import frappe


def _redis_key(namespace: str, identity: object) -> bytes:
	identity_digest = hashlib.sha256(str(identity).encode()).hexdigest()
	return frappe.cache().make_key(f"ch-pos-limit:v2:{namespace}:{identity_digest}")


def increment_fixed_window(namespace: str, identity: object, window_seconds: int) -> int:
	"""Atomically reserve one attempt and return its 1-based window count.

	``SET ... NX EX`` initializes the expiring counter exactly once. Concurrent
	workers then use Redis ``INCRBY`` so no increment can overwrite another.
	"""
	window_seconds = max(1, int(window_seconds))
	cache = frappe.cache()
	key = _redis_key(namespace, identity)
	cache.set(key, 0, nx=True, ex=window_seconds)
	count = int(cache.incrby(key, 1))
	if count == 1:
		cache.expire(key, window_seconds)
	return count


def clear_fixed_window(namespace: str, identity: object) -> None:
	frappe.cache().delete(_redis_key(namespace, identity))
