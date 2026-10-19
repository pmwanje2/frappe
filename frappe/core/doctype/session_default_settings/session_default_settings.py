# Copyright (c) 2019, Frappe Technologies and contributors
# License: MIT. See LICENSE

import json
import uuid
from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document


class SessionDefaultSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.core.doctype.session_default.session_default import SessionDefault
		from frappe.types import DF

		session_defaults: DF.Table[SessionDefault]
	# end: auto-generated types

	pass


def _require_authenticated_user():
	user = getattr(getattr(frappe, "session", None), "user", None)
	if not user or user == "Guest":
		raise frappe.PermissionError
	return user


def _configured_defaults():
	settings = frappe.get_single("Session Default Settings")
	return {
		frappe.scrub(row.ref_doctype): row.ref_doctype
		for row in settings.session_defaults
	}


def _explicit_default(doctype, key, user):
	value = frappe.defaults.get_defaults_for(user).get(key)
	if not isinstance(value, str) or not value:
		return None

	doc = frappe.get_doc(doctype, value)
	doc.check_permission("read")
	return value


@frappe.whitelist()
def get_session_default_values(explicit_only=False):
	user = _require_authenticated_user() if explicit_only else None
	settings = frappe.get_single("Session Default Settings")
	fields = []
	for default_values in settings.session_defaults:
		reference_doctype = frappe.scrub(default_values.ref_doctype)
		default = (
			_explicit_default(default_values.ref_doctype, reference_doctype, user)
			if explicit_only
			else frappe.defaults.get_user_default(reference_doctype)
		)
		fields.append(
			{
				"fieldname": reference_doctype,
				"fieldtype": "Link",
				"options": default_values.ref_doctype,
				"label": _("Default {0}").format(_(default_values.ref_doctype)),
				"default": default,
			}
		)
	return json.dumps(fields)


@frappe.whitelist()
def set_session_default_values(default_values: str | dict[str, Any]):
	user = _require_authenticated_user()
	default_values = frappe.parse_json(default_values)
	if not isinstance(default_values, dict):
		raise frappe.ValidationError("Session defaults must be an object")
	configured = _configured_defaults()
	for key, value in default_values.items():
		if not isinstance(key, str) or key not in configured:
			raise frappe.ValidationError("Session default is not configured")
		if value is not None and not isinstance(value, str):
			raise frappe.ValidationError("Session default values must be text or null")
		if value not in (None, ""):
			frappe.get_doc(configured[key], value).check_permission("read")

	savepoint = "session_defaults_" + uuid.uuid4().hex
	frappe.db.savepoint(savepoint)
	for entry in default_values:
		try:
			frappe.defaults.set_user_default(entry, default_values.get(entry))
		except Exception:
			try:
				frappe.db.rollback(save_point=savepoint)
			finally:
				frappe.defaults.clear_defaults_cache(user)
			raise
	return "success"


# called on hook 'on_logout' to clear defaults for the session
def clear_session_defaults():
	settings = frappe.get_single("Session Default Settings").session_defaults
	for entry in settings:
		frappe.defaults.clear_user_default(frappe.scrub(entry.ref_doctype))
