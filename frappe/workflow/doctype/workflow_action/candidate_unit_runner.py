# Copyright (c) 2026, Frappe Technologies and Contributors
# License: MIT. See LICENSE

"""Startup guards for the explicitly approved, site-free source-unit container."""

import hashlib
import importlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


def forbid_external_work(event, args):
	if event in {"socket.__new__", "socket.connect", "subprocess.Popen", "os.system", "os.posix_spawn"}:
		raise RuntimeError(f"source-unit runner forbids {event}")
	if event == "import" and (args[0] == "erpnext" or args[0].startswith("erpnext.")):
		raise RuntimeError("source-unit runner forbids ERPNext imports")


def forbid_site_calls(frame, event, arg):
	if (
		event == "call"
		and frame.f_globals.get("__name__") == "frappe"
		and frame.f_code.co_name in {"init", "connect"}
	):
		raise RuntimeError("source-unit runner forbids site initialization/connections")


def forbidden(*args, **kwargs):
	raise RuntimeError("source-unit runner forbids site initialization/connections")


def main():
	sys.addaudithook(forbid_external_work)
	sys.setprofile(forbid_site_calls)
	print(f"FROZEN_SOURCE={sys.argv[1]} PYTHON={sys.version}", flush=True)
	for declaration in sys.argv[2:]:
		expected_hash, relative_path = declaration.split(":", 1)
		path = Path("/candidate") / relative_path
		if not path.resolve().is_relative_to("/candidate"):
			raise RuntimeError("source path escaped the read-only candidate mount")
		actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
		if actual_hash != expected_hash:
			raise RuntimeError(f"source hash mismatch: {relative_path}")
		print(f"SOURCE_SHA256={actual_hash} {relative_path}", flush=True)
	frappe = importlib.import_module("frappe")
	if Path(frappe.__file__).resolve() != Path("/candidate/frappe/__init__.py"):
		raise RuntimeError("imported Frappe is not the candidate source")
	with patch.object(frappe, "init", forbidden), patch.object(frappe, "connect", forbidden):
		for module_name, path in (
			("frappe.model.workflow", "frappe/model/workflow.py"),
			(
				"frappe.workflow.doctype.workflow_action.workflow_action",
				"frappe/workflow/doctype/workflow_action/workflow_action.py",
			),
		):
			module = importlib.import_module(module_name)
			if Path(module.__file__).resolve() != Path("/candidate") / path:
				raise RuntimeError(f"wrong imported source: {module_name}")
			print(f"IMPORTED_SOURCE={module.__file__}", flush=True)
		suite = unittest.defaultTestLoader.loadTestsFromName(
			"frappe.workflow.doctype.workflow_action.test_workflow_action.TestWorkflowActionCandidateUnits"
		)
		result = unittest.TextTestRunner(verbosity=2).run(suite)
		if result.skipped:
			raise RuntimeError("source-unit qualification cannot contain skipped tests")
		return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
	sys.exit(main())
