# Copyright (c) 2026, Frappe Technologies and Contributors
# License: MIT. See LICENSE

"""Site-free assertions for security-motivated exact dependency pins."""

import tomllib
import unittest
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


class TestSecurityDependencyPins(unittest.TestCase):
	def dependencies(self):
		return tomllib.loads(PYPROJECT.read_text())["project"]["dependencies"]

	def test_pypdf_is_pinned_to_advisory_fixed_release(self):
		# 6.21.3/6.21.4 fix high-severity pypdf advisories; 6.21.2 and older are affected.
		pins = [dep for dep in self.dependencies() if dep.lower().startswith("pypdf")]
		self.assertEqual(pins, ["pypdf==6.21.4"])


if __name__ == "__main__":
	unittest.main()
