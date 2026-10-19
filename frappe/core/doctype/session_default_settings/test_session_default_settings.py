# Copyright (c) 2019, Frappe Technologies and Contributors
# License: MIT. See LICENSE
import json
from unittest.mock import patch

import frappe
from frappe.core.doctype.session_default_settings.session_default_settings import (
	clear_session_defaults,
	get_session_default_values,
	set_session_default_values,
)
from frappe.tests import IntegrationTestCase


class TestSessionDefaultSettings(IntegrationTestCase):
	def test_set_session_default_settings(self):
		frappe.set_user("Administrator")
		settings = frappe.get_single("Session Default Settings")
		settings.session_defaults = []
		settings.append("session_defaults", {"ref_doctype": "Role"})
		settings.save()

		set_session_default_values({"role": "Website Manager"})

		todo = frappe.get_doc(
			doctype="ToDo", description="test session defaults set", assigned_by="Administrator"
		).insert()
		self.assertEqual(todo.role, "Website Manager")

	def test_clear_session_defaults(self):
		clear_session_defaults()
		todo = frappe.get_doc(
			doctype="ToDo", description="test session defaults cleared", assigned_by="Administrator"
		).insert()
		self.assertNotEqual(todo.role, "Website Manager")


class TestSessionDefaultSettingsSafety(IntegrationTestCase):
	"""Exercise native permissions and DefaultValue storage on an isolated test site."""

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self.addCleanup(self._rollback_test_state)
		self.user = self._make_user("session-defaults-safety@example.com")
		self._configure_defaults("Country", "Language")
		for key in ("country", "Country", "language", "Language"):
			frappe.defaults.clear_user_default(key, user=self.user)
			frappe.defaults.clear_default(key, parent="__default")
		frappe.set_user(self.user)

	def _rollback_test_state(self):
		frappe.db.rollback()
		frappe.set_user("Administrator")
		frappe.clear_cache()

	def _make_user(self, email):
		return frappe.get_doc(
			doctype="User",
			email=email,
			first_name="Session Defaults",
			enabled=1,
			send_welcome_email=0,
			roles=[{"role": "Website Manager"}],
		).insert().name

	def _configure_defaults(self, *doctypes):
		settings = frappe.get_single("Session Default Settings")
		settings.set("session_defaults", [{"ref_doctype": doctype} for doctype in doctypes])
		settings.save(ignore_permissions=True)

	def _stored_rows(self, user=None):
		return frappe.get_all(
			"DefaultValue",
			filters={"parent": user or self.user},
			fields=["defkey", "defvalue"],
			order_by="defkey, defvalue",
		)

	def _stored_value(self, key, user=None):
		return frappe.db.get_value(
			"DefaultValue", {"parent": user or self.user, "defkey": key}, "defvalue"
		)

	def _fields(self, **kwargs):
		# Use native argument dispatch, as HTTP does: the old getter ignores the new
		# option, exposing fallback behavior instead of failing on an unknown kwarg.
		fields = json.loads(frappe.call(get_session_default_values, **kwargs))
		return {field["fieldname"]: field for field in fields}

	def _permit(self, doctype, name, **kwargs):
		return frappe.get_doc(
			doctype="User Permission",
			user=self.user,
			allow=doctype,
			for_value=name,
			apply_to_all_doctypes=1,
			**kwargs,
		).insert(ignore_permissions=True)

	def _make_todo(self, assigned_to):
		frappe.set_user("Administrator")
		self._configure_defaults("Country", "ToDo")
		todo = frappe.get_doc(
			doctype="ToDo",
			description="Session default document-access check",
			allocated_to=assigned_to,
			assigned_by=assigned_to,
		).insert()
		frappe.set_user(self.user)
		return todo

	def _failing_storage_write(self):
		# Synthetic fault injection only: both native writes still reach the real DB.
		# Assertions below inspect real rows, not mock calls or in-memory substitutes.
		native_set = frappe.defaults.set_user_default

		def write_then_fail(key, value, user=None, parenttype=None):
			native_set(key, value, user=user, parenttype=parenttype)
			if key == "language":
				# Prime the real native cache after the writes. A rollback that
				# only restores SQL rows must also invalidate this cached readback.
				frappe.defaults.get_defaults_for(self.user)
				raise RuntimeError("Injected session-default storage failure")

		return patch.object(frappe.defaults, "set_user_default", write_then_fail)

	def test_set_preserves_desk_dict_and_json_success_contract(self):
		for payload, expected in (
			({"country": "India"}, "India"),
			('{"country": "United States"}', "United States"),
		):
			with self.subTest(payload=payload):
				self.assertEqual(set_session_default_values(payload), "success")
				self.assertEqual(self._stored_value("country"), expected)

	def test_set_uses_configured_scrubbed_multiword_key(self):
		frappe.set_user("Administrator")
		permission = self._permit("Country", "India")
		self._configure_defaults("User Permission")

		self.assertEqual(set_session_default_values({"user_permission": permission.name}), "success")
		self.assertEqual(self._stored_value("user_permission", "Administrator"), permission.name)

	def test_set_preserves_unrelated_defaults_and_other_scopes(self):
		frappe.defaults.set_user_default("session_defaults_unrelated", "keep")
		frappe.defaults.set_user_default("language", "en")
		frappe.defaults.set_user_default("country", "United States", user="Administrator")
		frappe.defaults.set_global_default("country", "United States")
		admin_before = self._stored_rows("Administrator")
		global_before = self._stored_rows("__default")

		self.assertEqual(set_session_default_values({"country": "India"}), "success")

		self.assertEqual(self._stored_value("country"), "India")
		self.assertEqual(self._stored_value("language"), "en")
		self.assertEqual(self._stored_value("session_defaults_unrelated"), "keep")
		self.assertEqual(self._stored_rows("Administrator"), admin_before)
		self.assertEqual(self._stored_rows("__default"), global_before)

	def test_set_prevalidates_unconfigured_keys_before_writing(self):
		frappe.defaults.set_user_default("country", "United States")
		before = self._stored_rows()

		for key in ("company", "Country", "user", "target_user"):
			with self.subTest(key=key):
				with self.assertRaises(frappe.ValidationError):
					set_session_default_values({"country": "India", key: self.user})
				self.assertEqual(self._stored_rows(), before)

	def test_set_prevalidates_all_scalar_values_before_writing(self):
		frappe.defaults.set_user_default("country", "United States")
		before = self._stored_rows()

		for value in ([], ["en"], {}, {"name": "en"}, True, False, 0, 1, 1.5):
			with self.subTest(value=value):
				with self.assertRaises(frappe.ValidationError):
					set_session_default_values({"country": "India", "language": value})
				self.assertEqual(self._stored_rows(), before)

	def test_set_rejects_non_object_json(self):
		before = self._stored_rows()
		for payload in ("[]", '["country"]', "null", '"India"'):
			with self.subTest(payload=payload):
				with self.assertRaises(frappe.ValidationError):
					set_session_default_values(payload)
				self.assertEqual(self._stored_rows(), before)

	def test_set_requires_exact_existing_native_link_names(self):
		before = self._stored_rows()
		for value in ("india", " India", "India ", "IN", "_Missing Session Default Country"):
			with self.subTest(value=value):
				with self.assertRaises(frappe.ValidationError):
					set_session_default_values({"country": value})
				self.assertEqual(self._stored_rows(), before)

	def test_set_prevalidates_a_later_nonexistent_link(self):
		frappe.defaults.set_user_default("country", "United States")
		before = self._stored_rows()

		with self.assertRaises(frappe.ValidationError):
			set_session_default_values({"country": "India", "language": "missing-session-default-language"})

		self.assertEqual(self._stored_rows(), before)

	def test_set_enforces_native_user_permissions_before_any_write(self):
		self._permit("Country", "India")
		self.assertTrue(frappe.get_doc("Country", "India").has_permission("read"))
		self.assertFalse(frappe.get_doc("Country", "United States").has_permission("read"))
		before = self._stored_rows()

		with self.assertRaises(frappe.PermissionError):
			set_session_default_values({"language": "en", "country": "United States"})

		self.assertEqual(self._stored_rows(), before)
		self.assertEqual(set_session_default_values({"country": "India"}), "success")
		self.assertEqual(self._stored_value("country"), "India")

	def test_set_checks_document_access_not_just_doctype_access(self):
		todo = self._make_todo("Administrator")
		self.assertTrue(frappe.has_permission("ToDo", "read"))
		self.assertFalse(frappe.get_doc("ToDo", todo.name).has_permission("read"))
		before = self._stored_rows()

		with self.assertRaises(frappe.PermissionError):
			set_session_default_values({"country": "India", "todo": todo.name})

		self.assertEqual(self._stored_rows(), before)

	def test_set_clear_deletes_the_row_without_touching_other_defaults(self):
		frappe.defaults.set_user_default("language", "en")
		frappe.defaults.set_user_default("session_defaults_unrelated", "keep")
		frappe.defaults.set_global_default("country", "United States")
		global_before = self._stored_rows("__default")

		for stored_value in ("India", ""):
			for clear_value in ("", None):
				with self.subTest(stored_value=stored_value, clear_value=clear_value):
					# Empty storage exercises the native cstr(None) == "" edge.
					frappe.defaults.set_user_default("country", stored_value)
					self.assertEqual(self._stored_value("country"), stored_value)
					self.assertEqual(set_session_default_values({"country": clear_value}), "success")
					self.assertFalse(
						frappe.db.exists("DefaultValue", {"parent": self.user, "defkey": "country"})
					)
					self.assertEqual(self._stored_value("language"), "en")
					self.assertEqual(self._stored_value("session_defaults_unrelated"), "keep")
					self.assertEqual(self._stored_rows("__default"), global_before)

	def test_set_can_clear_a_selection_after_access_is_revoked(self):
		self.assertEqual(set_session_default_values({"country": "India"}), "success")
		self._permit("Country", "United States")
		self.assertFalse(frappe.get_doc("Country", "India").has_permission("read"))

		self.assertEqual(set_session_default_values({"country": ""}), "success")
		self.assertFalse(frappe.db.exists("DefaultValue", {"parent": self.user, "defkey": "country"}))

	def test_set_rejects_guest_even_for_clear_or_empty_payloads(self):
		frappe.set_user("Guest")
		before = self._stored_rows("Guest")

		for payload in ({"country": "India"}, {"country": None}, {}):
			with self.subTest(payload=payload):
				with self.assertRaises((frappe.PermissionError, frappe.AuthenticationError)):
					set_session_default_values(payload)
				self.assertEqual(self._stored_rows("Guest"), before)

	def test_set_rejects_a_missing_session_identity(self):
		for user in (None, ""):
			with self.subTest(user=user):
				frappe.set_user("Guest")
				frappe.session.user = user
				with self.assertRaises((frappe.PermissionError, frappe.AuthenticationError)):
					set_session_default_values({})

	def test_set_propagates_persistence_failure(self):
		with self._failing_storage_write():
			with self.assertRaises(RuntimeError):
				set_session_default_values({"country": "India", "language": "en"})

	def test_set_rolls_back_partial_writes_without_losing_prior_changes(self):
		frappe.defaults.set_user_default("country", "United States")
		frappe.defaults.set_user_default("language", "fr")
		frappe.defaults.set_user_default("session_defaults_unrelated", "keep")
		before = self._stored_rows()

		with self._failing_storage_write():
			try:
				set_session_default_values({"country": "India", "language": "en"})
			except RuntimeError:
				pass  # Propagation is tested separately; check the actual DB after failure.

		self.assertEqual(self._stored_rows(), before)
		cached_defaults = frappe.defaults.get_defaults_for(self.user)
		self.assertEqual(cached_defaults.get("country"), "United States")
		self.assertEqual(cached_defaults.get("language"), "fr")
		self.assertEqual(cached_defaults.get("session_defaults_unrelated"), "keep")
		self.assertEqual(self._fields(explicit_only=True)["country"]["default"], "United States")
		self.assertEqual(self._fields(explicit_only=True)["language"]["default"], "fr")

	def test_get_preserves_default_desk_json_and_link_metadata(self):
		frappe.defaults.set_global_default("country", "India")
		result = get_session_default_values()

		self.assertIsInstance(result, str)
		self.assertEqual(
			json.loads(result),
			[
				{
					"fieldname": "country",
					"fieldtype": "Link",
					"options": "Country",
					"label": "Default Country",
					"default": "India",
				},
				{
					"fieldname": "language",
					"fieldtype": "Link",
					"options": "Language",
					"label": "Default Language",
					"default": None,
				},
			],
		)
		self.assertEqual(self._fields(explicit_only=False)["country"]["default"], "India")

	def test_get_explicit_does_not_report_an_inherited_global_as_a_selection(self):
		frappe.defaults.set_global_default("country", "India")
		self.assertEqual(self._fields()["country"]["default"], "India")

		self.assertIsNone(self._fields(explicit_only=True)["country"]["default"])
		self.assertIsNone(self._stored_value("country"))

	def test_get_http_string_false_is_not_treated_as_explicit_only(self):
		frappe.defaults.set_global_default("country", "India")

		self.assertEqual(self._fields(explicit_only="false")["country"]["default"], "India")
		self.assertIsNone(self._fields(explicit_only="true")["country"]["default"])

	def test_get_explicit_uses_only_the_current_users_storage(self):
		frappe.defaults.set_user_default("country", "India")
		frappe.defaults.set_global_default("country", "Uganda")
		frappe.set_user("Administrator")
		other_user = self._make_user("session-defaults-other@example.com")
		frappe.set_user(other_user)

		self.assertIsNone(self._fields(explicit_only=True)["country"]["default"])
		self.assertEqual(set_session_default_values({"country": "United States"}), "success")
		self.assertEqual(self._fields(explicit_only=True)["country"]["default"], "United States")
		frappe.set_user(self.user)
		self.assertEqual(self._fields(explicit_only=True)["country"]["default"], "India")
		self.assertEqual(self._stored_value("country", other_user), "United States")

	def test_get_explicit_does_not_infer_a_user_permission_default(self):
		self._permit("Country", "India", is_default=1)
		self.assertEqual(frappe.defaults.get_user_default("Country"), "India")

		self.assertIsNone(self._fields(explicit_only=True)["country"]["default"])

	def test_get_explicit_does_not_choose_a_first_document(self):
		self.assertTrue(frappe.db.exists("Country", "India"))
		self.assertTrue(frappe.db.exists("Country", "United States"))

		self.assertIsNone(self._fields(explicit_only=True)["country"]["default"])

	def test_get_explicit_does_not_choose_the_first_of_multiple_stored_values(self):
		frappe.defaults.add_user_default("country", "India")
		frappe.defaults.add_user_default("country", "United States")

		self.assertIsNone(self._fields(explicit_only=True)["country"]["default"])

	def test_get_explicit_rechecks_user_permissions_after_revocation(self):
		set_session_default_values({"country": "India"})
		self.assertEqual(self._fields(explicit_only=True)["country"]["default"], "India")
		self._permit("Country", "United States")

		try:
			fields = self._fields(explicit_only=True)
		except frappe.PermissionError:
			pass  # Either native denial or an unset value must prevent disclosure.
		else:
			self.assertIsNone(fields["country"]["default"])
		self.assertEqual(self._stored_value("country"), "India")

	def test_get_explicit_rechecks_native_document_access_after_revocation(self):
		todo = self._make_todo(self.user)
		self.assertTrue(frappe.get_doc("ToDo", todo.name).has_permission("read"))
		set_session_default_values({"todo": todo.name})
		self.assertEqual(self._fields(explicit_only=True)["todo"]["default"], todo.name)
		frappe.db.set_value(
			"ToDo", todo.name, {"allocated_to": "Administrator", "assigned_by": "Administrator"}
		)
		self.assertFalse(frappe.get_doc("ToDo", todo.name).has_permission("read"))

		try:
			fields = self._fields(explicit_only=True)
		except frappe.PermissionError:
			pass
		else:
			self.assertIsNone(fields["todo"]["default"])

	def test_get_explicit_does_not_report_a_deleted_link(self):
		frappe.defaults.set_user_default("country", "_Missing Session Default Country")

		try:
			fields = self._fields(explicit_only=True)
		except frappe.DoesNotExistError:
			pass
		else:
			self.assertIsNone(fields["country"]["default"])

	def test_get_explicit_rejects_an_unauthenticated_session(self):
		for user in ("Guest", None, ""):
			with self.subTest(user=user):
				frappe.set_user("Guest")
				frappe.session.user = user
				with self.assertRaises((frappe.PermissionError, frappe.AuthenticationError)):
					self._fields(explicit_only=True)

	def test_logout_clears_only_configured_current_user_defaults(self):
		frappe.defaults.set_user_default("country", "India")
		frappe.defaults.set_user_default("language", "en")
		frappe.defaults.set_user_default("session_defaults_unrelated", "keep")
		frappe.defaults.set_user_default("country", "United States", user="Administrator")
		frappe.defaults.set_global_default("country", "United States")
		admin_before = self._stored_rows("Administrator")
		global_before = self._stored_rows("__default")

		clear_session_defaults()

		self.assertIsNone(self._stored_value("country"))
		self.assertIsNone(self._stored_value("language"))
		self.assertEqual(self._stored_value("session_defaults_unrelated"), "keep")
		self.assertEqual(self._stored_rows("Administrator"), admin_before)
		self.assertEqual(self._stored_rows("__default"), global_before)
