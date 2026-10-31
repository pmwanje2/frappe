"""Write responses must not disclose values computed by validation.

The DB-free tests import the real endpoint functions, serializer and field-level
permission helper. Only persistence, metadata and workflow configuration are
substituted. The integration tests additionally exercise real storage, Version
creation and after-commit callbacks on a disposable bench site.
"""

import json
import unittest
from contextlib import ExitStack
from types import MethodType, SimpleNamespace
from unittest.mock import patch

import frappe
import frappe.api.v1 as v1
import frappe.api.v2 as v2
import frappe.client
import frappe.handler
import frappe.model.workflow as workflow
import frappe.translate
from frappe.model.docstatus import DocStatus
from frappe.model.document import Document
from frappe.model.meta import Meta
from frappe.tests import IntegrationTestCase

ENDPOINTS = (
	"client.insert",
	"client.save",
	"client.set_value",
	"client.submit",
	"client.cancel",
	"v1.create_doc",
	"v1.update_doc",
	"v2.create_doc",
	"workflow.desk",
	"workflow.v2",
)
PRIVATE = "server-computed existing access"


class WriteResponseDocument(Document):
	@property
	def computed_rows(self):
		return self.rows

	@property
	def private_virtual(self):
		return PRIVATE

	def validate(self):
		self.private_analysis = PRIVATE


def call_write(endpoint, doc):
	"""Invoke actual HTTP endpoint handlers, not copies of their implementation."""
	data = {
		"doctype": doc.doctype,
		"name": doc.name,
		"modified": doc.get("modified"),
		"requested_access": "requested",
	}
	if endpoint in ("client.insert", "v1.create_doc", "v2.create_doc"):
		data.pop("name")
	frappe.local.request = SimpleNamespace(method="POST")
	frappe.local.form_dict = frappe._dict()
	if endpoint == "client.insert":
		return frappe.client.insert(data)
	if endpoint == "client.save":
		return frappe.client.save(data)
	if endpoint == "client.set_value":
		return frappe.client.set_value(doc.doctype, doc.name, "requested_access", "requested")
	if endpoint == "client.submit":
		return frappe.client.submit(data)
	if endpoint == "client.cancel":
		return frappe.client.cancel(doc.doctype, doc.name)
	if endpoint.startswith("v1."):
		frappe.local.form_dict.data = json.dumps(data)
		if endpoint == "v1.create_doc":
			return v1.create_doc(doc.doctype)
		return v1.update_doc(doc.doctype, doc.name)
	if endpoint == "v2.create_doc":
		frappe.local.form_dict.update(data)
		return v2.create_doc(doc.doctype)
	frappe.local.form_dict.update(doc=json.dumps(data), action="Lodge")
	if endpoint == "workflow.desk":
		return frappe.handler.execute_cmd("frappe.model.workflow.apply_workflow")
	if endpoint == "workflow.v2":
		return v2.handle_rpc_call("frappe.model.workflow.apply_workflow")
	raise AssertionError(endpoint)


def response_dict(result):
	# Match Document.__json__, which is used for v1 and workflow responses.
	return result.__json__() if isinstance(result, Document) else result


class TestWriteResponsePermissions(unittest.TestCase):
	"""Site-free boundary regression; no production service is contacted."""

	def setUp(self):
		self.context = ExitStack()
		self.addCleanup(self.context.close)
		for key, value in (
			("session", frappe._dict(user="requester@example.test")),
			("request", SimpleNamespace(method="POST")),
			("form_dict", frappe._dict()),
			("flags", frappe._dict()),
			("lang", "en"),
		):
			self.context.enter_context(patch.object(frappe.local, key, value, create=True))
		fields = [
			frappe._dict(fieldname="requested_access", fieldtype="Data", permlevel=0),
			frappe._dict(fieldname="private_analysis", fieldtype="Data", permlevel=1),
			frappe._dict(fieldname="workflow_state", fieldtype="Data", permlevel=0),
			frappe._dict(fieldname="optional_comment", fieldtype="Data", permlevel=0),
		]
		self.meta = SimpleNamespace(
			name="Test Write Response",
			fields=fields,
			istable=0,
			_fields={df.fieldname: df for df in fields},
			_table_doctypes={},
			_non_computed_table_doctypes={},
			permissions=[
				frappe._dict(role="Requester", read=1, permlevel=0),
				frappe._dict(role="Approver", read=1, permlevel=1),
			],
			get_table_fields=lambda **kwargs: [],
			get_masked_fields=lambda **kwargs: [],
			get_valid_fields=lambda: ["name", "docstatus", *(df.fieldname for df in fields)],
		)
		self.roles = ["Requester"]
		self.context.enter_context(
			patch.object(frappe, "get_roles", side_effect=lambda user=None: self.roles)
		)
		self.context.enter_context(patch.object(frappe, "get_meta", return_value=self.meta))
		self.context.enter_context(patch.object(frappe, "is_table", return_value=False))
		self.context.enter_context(patch.object(frappe, "get_installed_apps", return_value=["frappe"]))
		self.context.enter_context(patch.object(frappe.translate, "get_all_translations", return_value={}))
		self.context.enter_context(patch.object(frappe.handler, "get_server_script_map", return_value={}))
		self.context.enter_context(patch.object(v2, "get_server_script_map", return_value={}))
		self.context.enter_context(
			patch.object(frappe, "override_whitelisted_method", side_effect=lambda m: m)
		)
		state = frappe._dict(state="Lodged", doc_status=0, update_field=None)
		transition = frappe._dict(action="Lodge", next_state="Lodged", allow_self_approval=1)
		self.context.enter_context(
			patch.object(
				workflow,
				"get_workflow",
				return_value=SimpleNamespace(workflow_state_field="workflow_state", states=[state]),
			)
		)
		self.context.enter_context(patch.object(workflow, "get_transitions", return_value=[transition]))
		self.context.enter_context(patch.object(Document, "load_from_db", return_value=None))
		# v16.54 v1.update_doc pre-checks write access; authorization is outside this response-privacy suite.
		self.context.enter_context(patch.object(Document, "check_permission", return_value=None))
		self.context.enter_context(patch.object(Document, "add_comment", return_value=None))
		self.context.enter_context(patch.object(Document, "insert", autospec=True, side_effect=self.persist))
		self.context.enter_context(patch.object(Document, "save", autospec=True, side_effect=self.persist))
		self.context.enter_context(patch.object(Document, "submit", autospec=True, side_effect=self.persist))
		self.context.enter_context(patch.object(Document, "cancel", autospec=True, side_effect=self.persist))

	def new_document(self):
		doc = WriteResponseDocument.__new__(WriteResponseDocument)
		doc.flags = frappe._dict()
		doc.init_from_dict(
			dict(
				doctype="Test Write Response",
				name="WRITE-1",
				docstatus=DocStatus.DRAFT,
				requested_access="requested",
				workflow_state="Draft",
				optional_comment=None,
			),
		)
		return doc

	def persist(self, doc):
		doc.validate()
		self.stored = doc.as_dict()
		# Retain the SAVED instance, as after-commit callbacks and queued jobs do.
		self.saved = doc
		return doc

	def run_endpoint(self, endpoint):
		doc = self.new_document()
		with (
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(frappe, "new_doc", return_value=doc),
		):
			result = call_write(endpoint, doc)
		return response_dict(result)

	def test_requester_write_responses_omit_server_computed_private_field(self):
		for endpoint in ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				result = self.run_endpoint(endpoint)
				self.assertEqual(result["requested_access"], "requested")
				self.assertNotIn("private_analysis", result)

	def test_level_one_reader_keeps_private_field(self):
		self.roles.append("Approver")
		for endpoint in ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				self.assertEqual(self.run_endpoint(endpoint)["private_analysis"], PRIVATE)

	def test_filter_does_not_mutate_storage_or_saved_instance(self):
		for endpoint in ENDPOINTS:
			with self.subTest(endpoint=endpoint):
				self.run_endpoint(endpoint)
				self.assertEqual(self.stored["private_analysis"], PRIVATE)
				self.assertEqual(self.saved.private_analysis, PRIVATE)

	def test_library_workflow_call_returns_full_document(self):
		doc = self.new_document()
		with patch.object(frappe, "get_doc", return_value=doc):
			result = workflow.apply_workflow(doc, "Lodge")
		self.assertIs(result, doc)
		self.assertEqual(result.private_analysis, PRIVATE)

	def test_dictionary_responses_preserve_readable_null_fields(self):
		for user, roles in (
			("requester@example.test", ["Requester"]),
			("approver@example.test", ["Requester", "Approver"]),
			("Administrator", []),
		):
			frappe.local.session.user = user
			self.roles = roles
			for endpoint in (*ENDPOINTS[:5], "v2.create_doc"):
				with self.subTest(user=user, endpoint=endpoint):
					result = self.run_endpoint(endpoint)
					self.assertIn("optional_comment", result)
					self.assertIsNone(result["optional_comment"])

	def test_parent_and_child_fields_are_omitted_without_mutating_saved_rows(self):
		self.assert_child_response_fields()

	def test_computed_child_table_uses_filtered_parent_fields(self):
		@property
		def computed_rows(self):
			return self.rows if getattr(self, "private_count", 0) else []

		with patch.object(WriteResponseDocument, "computed_rows", computed_rows):
			self.assert_child_response_fields(computed_rows_visible=False)

	def assert_child_response_fields(self, *, computed_rows_visible=True):
		count = frappe._dict(fieldname="private_count", fieldtype="Int", permlevel=1)
		self.meta.fields.append(count)
		self.meta._fields[count.fieldname] = count
		table = frappe._dict(fieldname="rows", fieldtype="Table", options="Test Response Child", permlevel=0)
		self.meta.fields.append(table)
		self.meta._fields["rows"] = table
		self.meta._table_doctypes["rows"] = table.options
		computed_table = frappe._dict(
			fieldname="computed_rows", fieldtype="Table", options=table.options, is_virtual=1, permlevel=0
		)
		self.meta.fields.append(computed_table)
		self.meta._fields["computed_rows"] = computed_table
		self.meta._table_doctypes["computed_rows"] = table.options
		self.meta.get_table_fields = lambda **kwargs: [table, computed_table]
		self.meta.default_fields = ["name", "docstatus"]
		self.meta.get_valid_columns = self.meta.get_valid_fields
		self.meta.get = lambda key, default=None: getattr(self.meta, key, default)
		for name in (
			"get_permissions",
			"get_permlevel_access",
			"get_permitted_fieldnames",
			"get_fieldnames_with_value",
		):
			setattr(self.meta, name, MethodType(getattr(Meta, name), self.meta))
		child_fields = [
			frappe._dict(fieldname="public_null", fieldtype="Data", permlevel=0),
			frappe._dict(fieldname="private_analysis", fieldtype="Data", permlevel=1),
			frappe._dict(fieldname="private_count", fieldtype="Int", permlevel=1),
		]
		child_meta = SimpleNamespace(
			fields=child_fields,
			_fields={df.fieldname: df for df in child_fields},
			_table_doctypes={},
			_non_computed_table_doctypes={},
			get_valid_fields=lambda: [df.fieldname for df in child_fields],
			get_table_fields=lambda **kwargs: [],
			get_masked_fields=lambda **kwargs: [],
		)
		with (
			patch.object(frappe, "only_has_select_perm", return_value=False),
			patch.object(
				frappe,
				"get_meta",
				side_effect=lambda doctype: child_meta if doctype == table.options else self.meta,
			),
		):
			for endpoint in ENDPOINTS:
				with self.subTest(endpoint=endpoint):
					child = Document.__new__(Document)
					child.flags = frappe._dict()
					child.init_from_dict(
						dict(
							doctype=table.options,
							name="ROW-1",
							public_null=None,
							private_analysis=PRIVATE,
							private_count=42,
						),
					)
					doc = self.new_document()
					doc.private_count = 42
					doc.rows = [child]
					with patch.object(self, "new_document", return_value=doc):
						result = self.run_endpoint(endpoint)
					self.assertEqual(child.private_analysis, PRIVATE)
					self.assertEqual(child.private_count, 42)
					self.assertEqual(self.saved.private_count, 42)
					self.assertEqual(self.stored["private_count"], 42)
					self.assertEqual(self.stored["rows"][0]["private_count"], 42)
					self.assertEqual(self.saved.computed_rows, self.saved.rows)
					self.assertEqual(self.stored["computed_rows"][0]["private_count"], 42)
					row = {"doctype": table.options}
					if endpoint in (*ENDPOINTS[:5], "v2.create_doc"):
						row["public_null"] = None
					self.assertEqual(
						{"parent_count": result.get("private_count", "omitted"), "rows": result["rows"]},
						{"parent_count": "omitted", "rows": [row]},
					)
					self.assertEqual(result["computed_rows"], [row] if computed_rows_visible else [])

	def test_virtual_field_uses_native_readable_fieldnames(self):
		field = frappe._dict(fieldname="private_virtual", fieldtype="Data", is_virtual=1, permlevel=1)
		self.meta.fields.append(field)
		self.meta._fields[field.fieldname] = field
		self.meta.default_fields = ["name", "docstatus"]
		self.meta.get_valid_columns = self.meta.get_valid_fields
		self.meta.get = lambda key, default=None: getattr(self.meta, key, default)
		for name in (
			"get_permissions",
			"get_permlevel_access",
			"get_permitted_fieldnames",
			"get_fieldnames_with_value",
		):
			setattr(self.meta, name, MethodType(getattr(Meta, name), self.meta))
		with (
			patch.object(frappe, "only_has_select_perm", return_value=False),
			patch("frappe.utils.safe_exec.get_safe_globals", return_value={}),
		):
			for fieldname, options in (("private_virtual", None), ("private_expression", repr(PRIVATE))):
				self.meta._fields.pop(field.fieldname)
				field.update(fieldname=fieldname, options=options)
				self.meta._fields[fieldname] = field
				for user, roles, visible in (
					("requester@example.test", ["Requester"], False),
					("approver@example.test", ["Requester", "Approver"], True),
					("Administrator", ["Requester", "Approver"], True),
				):
					frappe.local.session.user = user
					self.roles = roles
					for endpoint in ENDPOINTS:
						with self.subTest(field=fieldname, user=user, endpoint=endpoint):
							result = self.run_endpoint(endpoint)
							if visible:
								self.assertEqual(result.get(fieldname), PRIVATE)
							else:
								self.assertNotIn(fieldname, result)

	def test_overridden_workflow_endpoint_still_filters_its_response(self):
		@frappe.whitelist()
		def workflow_override(doc, action):
			return workflow.apply_workflow(doc, action)

		with (
			patch.object(frappe, "override_whitelisted_method", return_value="test_app.workflow_override"),
			patch.object(frappe, "get_attr", return_value=workflow_override),
		):
			for endpoint in ("workflow.desk", "workflow.v2"):
				with self.subTest(endpoint=endpoint):
					self.assertNotIn("private_analysis", self.run_endpoint(endpoint))
					self.assertEqual(self.saved.private_analysis, PRIVATE)


class TestWriteResponsePermissionsIntegration(IntegrationTestCase):
	"""Requires a disposable initialized bench, never a live customer site."""

	def test_write_responses_preserve_storage_versions_and_after_commit(self):
		from frappe.core.doctype.doctype.test_doctype import new_doctype

		self.enterContext(patch.object(frappe.local, "request", frappe._dict(method="POST"), create=True))
		self.enterContext(patch.object(frappe.local, "form_dict", frappe._dict(), create=True))
		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, "Administrator")
		dt = new_doctype(
			is_submittable=1,
			track_changes=1,
			fields=[
				{"fieldname": "requested_access", "label": "Requested Access", "fieldtype": "Data"},
				{
					"fieldname": "private_analysis",
					"label": "Private Analysis",
					"fieldtype": "Data",
					"permlevel": 1,
				},
				{"fieldname": "workflow_state", "label": "Workflow State", "fieldtype": "Data"},
			],
			permissions=[
				{"role": "All", "read": 1, "write": 1, "create": 1, "submit": 1, "cancel": 1},
				{"role": "System Manager", "read": 1, "permlevel": 1},
			],
		).insert()
		self.addCleanup(frappe.delete_doc, "DocType", dt.name, force=True)
		self.addCleanup(frappe.set_user, "Administrator")
		callbacks = []

		class AuditedWriteResponseDocument(WriteResponseDocument):
			def on_change(self):
				frappe.db.after_commit.add(lambda: callbacks.append(self.private_analysis))

		with patch.dict(frappe.controllers[frappe.local.site], {dt.name: AuditedWriteResponseDocument}):
			for endpoint in ENDPOINTS:
				for user, roles in (
					("test@example.com", ["All"]),
					("test1@example.com", ["All", "System Manager"]),
				):
					with self.subTest(endpoint=endpoint, user=user):
						frappe.set_user("Administrator")
						doc = frappe.get_doc(
							doctype=dt.name, requested_access="before", workflow_state="Draft"
						).insert()
						if endpoint == "client.cancel":
							doc.submit()
						# Force a fresh private-field diff for the native Version audit.
						if endpoint != "client.cancel":
							frappe.db.set_value(
								dt.name, doc.name, "private_analysis", "before", update_modified=False
							)
						callbacks.clear()
						frappe.set_user(user)
						state = frappe._dict(state="Lodged", doc_status=0, update_field=None)
						transition = frappe._dict(action="Lodge", next_state="Lodged", allow_self_approval=1)
						with (
							patch.object(frappe, "get_roles", return_value=roles),
							patch.object(
								workflow,
								"get_workflow",
								return_value=SimpleNamespace(
									workflow_state_field="workflow_state", states=[state]
								),
							),
							patch.object(workflow, "get_transitions", return_value=[transition]),
						):
							result = response_dict(call_write(endpoint, doc))
						if "System Manager" in roles:
							self.assertEqual(result["private_analysis"], PRIVATE)
						else:
							self.assertNotIn("private_analysis", result)
						name = result["name"]
						self.assertEqual(frappe.db.get_value(dt.name, name, "private_analysis"), PRIVATE)
						frappe.db.after_commit.run()
						self.assertTrue(callbacks)
						self.assertTrue(all(value == PRIVATE for value in callbacks))
						if endpoint not in (
							"client.insert",
							"v1.create_doc",
							"v2.create_doc",
							"client.cancel",
						):
							versions = frappe.get_all(
								"Version", filters={"ref_doctype": dt.name, "docname": name}, pluck="data"
							)
							self.assertTrue(
								any(
									["private_analysis", "before", PRIVATE]
									in json.loads(data).get("changed", [])
									for data in versions
								)
							)
		frappe.set_user("Administrator")
