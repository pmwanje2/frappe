import time
from contextvars import Context
from threading import Thread, current_thread
from unittest.mock import MagicMock, patch
from uuid import uuid4

from redis.exceptions import ConnectionError

import frappe
from frappe.core.doctype.doctype.test_doctype import new_doctype
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.tests.test_api import FrappeAPITestCase
from frappe.tests.utils import whitelist_for_tests
from frappe.utils.caching import redis_cache, request_cache, site_cache

CACHE_TTL = 4
external_service = MagicMock(return_value=30)
register_with_external_service = MagicMock(return_value=True)


@request_cache
def request_specific_api(a: list | tuple | dict | int, b: int) -> int:
	# API that takes very long to return a result
	todays_value = external_service()
	if not isinstance(a, int | float):
		a = 1
	return a**b * todays_value


@whitelist_for_tests(allow_guest=True)
@site_cache
def ping() -> str:
	register_with_external_service(frappe.local.site)
	return "pong"


@whitelist_for_tests(allow_guest=True)
@site_cache(ttl=CACHE_TTL)
def ping_with_ttl() -> str:
	register_with_external_service(frappe.local.site)
	return "pong"


class TestCachingUtils(IntegrationTestCase):
	def test_request_cache(self):
		retval = []
		hashable_values = [
			range(10),
			frappe.get_last_doc("DocType"),
			True,
			None,
		]

		unhashable_values = [
			[1, 2, 3, 4],
			{"abc": "test-key"},
			frappe._dict(),
		]

		def same_output_received():
			return len(set(retval)) == 1

		# ensure that external service was called only once
		# thereby return value of request_specific_api is cached
		retval.extend(request_specific_api(120, 23) for _ in range(5))
		external_service.assert_called_once()
		self.assertTrue(same_output_received())

		# hash() function does not differentiate between int & float
		# Giving same values for both
		retval.append(request_specific_api(120.0, 23))
		external_service.assert_called_once()
		self.assertTrue(same_output_received())

		# ensure that function is executed when call isn't already cached
		retval.clear()
		retval.extend(request_specific_api(120, 13) for _ in range(10))
		self.assertEqual(external_service.call_count, 2)
		self.assertTrue(same_output_received())

		# ensure single call if key is hashable
		for arg in hashable_values:
			external_service.reset_mock()
			for _ in range(2):
				request_specific_api(arg, 13)

			self.assertEqual(external_service.call_count, 1)

		# multiple calls if key cannot be generated
		for arg in unhashable_values:
			external_service.reset_mock()
			for _ in range(2):
				request_specific_api(arg, 13)

			self.assertEqual(external_service.call_count, 2)


class TestSiteCache(FrappeAPITestCase):
	def test_site_cache(self):
		module = __name__
		api_with_ttl = f"{module}.ping_with_ttl"
		api_without_ttl = f"{module}.ping"

		for _ in range(5):
			self.get(f"/api/method/{api_with_ttl}")
			self.get(f"/api/method/{api_without_ttl}")

		self.assertEqual(register_with_external_service.call_count, 2)
		time.sleep(CACHE_TTL)
		self.get(f"/api/method/{api_with_ttl}")
		self.assertEqual(register_with_external_service.call_count, 3)


class TestRedisCache(FrappeAPITestCase):
	def test_redis_cache(self):
		function_call_count = 0

		@redis_cache(ttl=CACHE_TTL)
		def calculate_area(radius: float) -> float:
			nonlocal function_call_count
			function_call_count += 1
			return 3.14 * radius**2

		self.assertEqual(calculate_area(10), 314)
		self.assertEqual(function_call_count, 1)
		self.assertEqual(calculate_area(10), 314)
		self.assertEqual(function_call_count, 1)

		time.sleep(CACHE_TTL * 1.5)
		frappe.local.cache.clear()
		self.assertEqual(calculate_area(10), 314)
		self.assertEqual(function_call_count, 2)

		calculate_area.clear_cache()
		self.assertEqual(calculate_area(10), 314)
		self.assertEqual(function_call_count, 3)
		calculate_area.clear_cache()

	def test_redis_cache_without_params(self):
		function_call_count = 0

		@redis_cache
		def calculate_area(radius: float) -> float:
			nonlocal function_call_count
			function_call_count += 1
			return 3.14 * radius**2

		calculate_area.clear_cache()
		self.assertEqual(calculate_area(10), 314)
		self.assertEqual(function_call_count, 1)

		calculate_area.clear_cache()
		self.assertEqual(calculate_area(10), 314)
		self.assertEqual(function_call_count, 2)

		calculate_area.clear_cache()

	def test_redis_cache_diff_args(self):
		function_call_count = 0

		@redis_cache(ttl=CACHE_TTL)
		def calculate_area(radius: float) -> float:
			nonlocal function_call_count
			function_call_count += 1
			return 3.14 * radius**2

		self.assertEqual(calculate_area(10), 314)
		self.assertEqual(function_call_count, 1)
		self.assertEqual(calculate_area(100), 31400)
		self.assertEqual(function_call_count, 2)

		self.assertEqual(calculate_area(5), 25 * 3.14)
		self.assertEqual(function_call_count, 3)

		calculate_area(10)
		# from cache now
		self.assertEqual(function_call_count, 3)

		calculate_area(radius=10)
		# args, kwargs are treated differently
		self.assertEqual(function_call_count, 4)

		calculate_area(radius=10)
		# kwargs should hit cache too
		self.assertEqual(function_call_count, 4)

	def test_global_clear_cache(self):
		function_call_count = 0

		@redis_cache()
		def calculate_area(radius: float) -> float:
			nonlocal function_call_count
			function_call_count += 1
			return 3.14 * radius**2

		calculate_area(10)
		calculate_area(10)
		calculate_area(10)
		self.assertEqual(function_call_count, 1)

		# This is supposed to clear cache for the active site
		frappe.clear_cache()
		calculate_area(10)
		self.assertEqual(function_call_count, 2)

	def test_user_cache(self):
		function_call_count = 0
		PI = 3.1415
		ENGINEERING_PI = _E = 3

		@redis_cache(user=True)
		def calculate_area(radius: float) -> float:
			nonlocal function_call_count
			PI_APPROX = ENGINEERING_PI if frappe.session.user == "Engineer" else PI
			function_call_count += 1
			return PI_APPROX * radius**2

		with self.set_user("Engineer"):
			self.assertEqual(calculate_area(1), ENGINEERING_PI)
			self.assertEqual(function_call_count, 1)

		with self.set_user("Mathematician"):
			self.assertEqual(calculate_area(1), PI)
			self.assertEqual(function_call_count, 2)

		with self.set_user("Engineer"):
			self.assertEqual(calculate_area(1), ENGINEERING_PI)
			self.assertEqual(function_call_count, 2)

		with self.set_user("Mathematician"):
			self.assertEqual(calculate_area(1), PI)
			self.assertEqual(function_call_count, 2)


class TestRedisCacheNativeConcurrency(UnitTestCase):
	def setUp(self):
		self.token = uuid4().hex
		self.site = frappe.local.site
		self.sites_path = frappe.local.sites_path
		self.db_name = frappe.local.conf.db_name
		self.user = frappe.session.user
		frappe.local.cache.clear()

	def in_request(self, callback, *, db_name=None, user=None):
		def request():
			frappe.init(self.site, sites_path=self.sites_path)
			if db_name:
				frappe.local.conf = frappe.local.conf.copy()
				frappe.local.conf.db_name = db_name
			frappe.set_user(user or self.user)
			try:
				return callback()
			finally:
				frappe.destroy()

		return Context().run(request)

	def test_miss_rereads_value_written_by_another_request(self):
		calls = []
		expected = [{"module_name": "Accounts", "app": "erpnext"}]

		@redis_cache(ttl=CACHE_TTL)
		def modules(token):
			calls.append(token)
			return expected

		self.addCleanup(modules.clear_cache)
		original_get = frappe.cache.get
		reader = current_thread()
		interleaved = False
		worker_results, worker_errors = [], []

		def worker_request():
			try:
				worker_results.append(self.in_request(lambda: modules(self.token)))
			except BaseException as exc:
				worker_errors.append(exc)

		def get_after_another_request_writes(key, *args, **kwargs):
			nonlocal interleaved
			value = original_get(key, *args, **kwargs)
			if current_thread() is reader and not interleaved and value is None:
				interleaved = True
				worker = Thread(target=worker_request)
				worker.start()
				worker.join(timeout=5)
				self.assertFalse(worker.is_alive(), "the second request did not finish")
			return value

		with patch.object(frappe.cache, "get", side_effect=get_after_another_request_writes):
			first = modules(self.token)
			second = modules(self.token)

		self.assertTrue(interleaved)
		self.assertEqual(worker_errors, [])
		self.assertEqual(worker_results, [expected])
		self.assertEqual(calls, [self.token])
		self.assertEqual((first, second), (expected, expected))

	def test_cached_none_is_a_hit_in_same_and_new_requests(self):
		calls = []

		@redis_cache(ttl=CACHE_TTL)
		def no_value(token):
			calls.append(token)
			return None

		self.addCleanup(no_value.clear_cache)
		self.assertIsNone(no_value(self.token))
		self.assertIsNone(no_value(self.token))
		self.assertIsNone(self.in_request(lambda: no_value(self.token)))
		self.assertEqual(calls, [self.token])

	def test_miss_computes_with_ttl_and_non_none_local_hit_avoids_get(self):
		calls = []

		@redis_cache(ttl=CACHE_TTL)
		def value(token):
			calls.append(token)
			return [token]

		self.addCleanup(value.clear_cache)
		with patch.object(frappe.cache, "get", wraps=frappe.cache.get) as get:
			self.assertEqual(value(self.token), [self.token])
			get_count = get.call_count
			self.assertEqual(value(self.token), [self.token])
			self.assertEqual(get.call_count, get_count)

		keys = frappe.cache.get_keys(f"{value.__module__}.{value.__qualname__}::")
		self.assertEqual(len(keys), 1)
		self.assertGreater(frappe.cache.pttl(keys[0]), 0)
		self.assertLessEqual(frappe.cache.pttl(keys[0]), CACHE_TTL * 1000)
		self.assertEqual(calls, [self.token])

	def test_user_scoping(self):
		calls = []

		@redis_cache(user=True)
		def value(token):
			calls.append(frappe.session.user)
			return frappe.session.user

		for user in ("Cache User A", "Cache User B"):
			self.addCleanup(lambda user=user: self.in_request(value.clear_cache, user=user))
		self.assertEqual(self.in_request(lambda: value(self.token), user="Cache User A"), "Cache User A")
		self.assertEqual(self.in_request(lambda: value(self.token), user="Cache User B"), "Cache User B")
		self.assertEqual(self.in_request(lambda: value(self.token), user="Cache User A"), "Cache User A")
		self.assertEqual(calls, ["Cache User A", "Cache User B"])

	def test_site_and_shared_scoping(self):
		for shared in (False, True):
			with self.subTest(shared=shared):
				calls = []

				@redis_cache(shared=shared)
				def value(token):
					calls.append(frappe.local.conf.db_name)
					return frappe.local.conf.db_name

				other_db = f"cache-scope-{self.token}"
				self.addCleanup(value.clear_cache)
				self.addCleanup(lambda value=value: self.in_request(value.clear_cache, db_name=other_db))
				first = value(self.token)
				other = self.in_request(lambda: value(self.token), db_name=other_db)
				self.assertEqual(first, self.db_name)
				self.assertEqual(other, self.db_name if shared else other_db)
				self.assertEqual(len(calls), 1 if shared else 2)
				value.clear_cache()

	def test_clear_cache_recomputes(self):
		calls = []

		@redis_cache
		def value(token):
			calls.append(token)
			return len(calls)

		self.addCleanup(value.clear_cache)
		self.assertEqual(value(self.token), 1)
		value.clear_cache()
		self.assertEqual(value(self.token), 2)
		self.assertEqual(calls, [self.token, self.token])

	def test_get_connection_error_still_computes(self):
		@redis_cache
		def value(token):
			return [token]

		self.addCleanup(value.clear_cache)
		with patch.object(frappe.cache, "get", side_effect=ConnectionError("local fixture GET unavailable")):
			self.assertEqual(value(self.token), [self.token])


class TestDocumentCache(FrappeAPITestCase):
	TEST_DOCTYPE = "User"
	TEST_DOCNAME = "Administrator"
	TEST_FIELD = "middle_name"

	def setUp(self) -> None:
		self.test_value = frappe.generate_hash()

	def test_caching(self):
		frappe.get_cached_doc(self.TEST_DOCTYPE, self.TEST_DOCNAME)

		with self.assertQueryCount(0):
			doc = frappe.get_cached_doc(self.TEST_DOCTYPE, self.TEST_DOCNAME)

		doc.db_set(self.TEST_FIELD, self.test_value)
		new_doc = frappe.get_cached_doc(self.TEST_DOCTYPE, self.TEST_DOCNAME)

		self.assertIsNot(doc, new_doc)  # Shouldn't be same object from frappe.local
		self.assertEqual(new_doc.get(self.TEST_FIELD), self.test_value)  # Cache invalidated and fetched
		frappe.db.rollback()

		doc_after_rollback = frappe.get_cached_doc(self.TEST_DOCTYPE, self.TEST_DOCNAME)
		self.assertIsNot(new_doc, doc_after_rollback)
		# Cache invalidated after rollback
		self.assertNotEqual(doc_after_rollback.get(self.TEST_FIELD), self.test_value)

		with self.assertQueryCount(0):
			frappe.get_cached_doc(self.TEST_DOCTYPE, self.TEST_DOCNAME)

	def test_cache_invalidation_set_value(self):
		doc = frappe.get_cached_doc(self.TEST_DOCTYPE, self.TEST_DOCNAME)

		frappe.db.set_value(
			self.TEST_DOCTYPE,
			{"name": ("like", "%Admin%")},
			self.TEST_FIELD,
			self.test_value,
		)

		new_doc = frappe.get_cached_doc(self.TEST_DOCTYPE, self.TEST_DOCNAME)
		self.assertIsNot(doc, new_doc)
		self.assertEqual(new_doc.get(self.TEST_FIELD), self.test_value)

		with self.assertQueryCount(0):
			frappe.get_cached_doc(self.TEST_DOCTYPE, self.TEST_DOCNAME)


class TestRedisWrapper(FrappeAPITestCase):
	def test_delete_keys(self):
		prefix = "test_del_"

		for i in range(5):
			frappe.cache.set_value(f"{prefix}{i}", 1)

		self.assertEqual(len(frappe.cache.get_keys(prefix)), 5)
		frappe.cache.delete_keys(prefix)
		self.assertEqual(len(frappe.cache.get_keys(prefix)), 0)

	def test_hash(self):
		key = "test_hash"

		# Confirm that there's no data initially
		exists = frappe.cache.exists(key)
		self.assertFalse(exists)

		# Insert 5 key-value pairs
		for i in range(5):
			frappe.cache.hset(key, f"key_{i}", f"value_{i}")

		# Check that we have 5 values
		values = frappe.cache.hgetall(key)
		self.assertEqual(len(values), 5)

		# Check that each value matches
		for i in range(5):
			value = frappe.cache.hget(key, f"key_{i}")
			self.assertEqual(value, f"value_{i}")

		# Check the keys themselves
		keys = frappe.cache.hkeys(key)
		for i in range(5):
			self.assertIn(f"key_{i}".encode(), keys)

		# Delete a single key and check that we still have the remaining 4
		frappe.cache.hdel(key, "key_1")
		values = frappe.cache.hgetall(key)
		self.assertEqual(len(values), 4)

		# Delete 2 keys and check that we still have the remaining 2
		frappe.cache.hdel(key, ["key_2", "key_3"])
		values = frappe.cache.hgetall(key)
		self.assertEqual(len(values), 2)

		# Delete the hash itself and confirm that there's no data
		frappe.cache.delete_value(key)
		exists = frappe.cache.exists(key)
		self.assertFalse(exists)

	def test_user_cache_clear(self):
		from frappe.cache_manager import user_cache_keys

		# Set some keys that a user's cache would usually have
		user1 = frappe.utils.random_string(10)
		user2 = frappe.utils.random_string(10)
		for key in user_cache_keys:
			frappe.cache.hset(key, user1, key)
			frappe.cache.hset(key, user2, key)

		frappe.clear_cache(user=user1)

		# Check that the keys for user1 are gone
		for key in set(user_cache_keys) - {"home_page"}:
			self.assertFalse(frappe.cache.hexists(key, user1))
			self.assertTrue(frappe.cache.hexists(key, user2))

	def test_doctype_cache_clear(self):
		from frappe.cache_manager import doctype_cache_keys

		# Set some keys that a user's cache would usually have
		doctype1 = new_doctype(frappe.utils.random_string(10))
		doctype2 = new_doctype(frappe.utils.random_string(10))
		for key in doctype_cache_keys:
			frappe.cache.hset(key, doctype1.name, key)
			frappe.cache.hset(key, doctype2.name, key)

		frappe.clear_cache(doctype=doctype1.name)

		# Check that the keys for doctype1 are gone
		for key in doctype_cache_keys:
			self.assertFalse(frappe.cache.hexists(key, doctype1.name))
			self.assertTrue(frappe.cache.hexists(key, doctype2.name))

	def test_backward_compat_cache(self):
		self.assertEqual(frappe.cache, frappe.cache())


class TestHttpCache(FrappeAPITestCase):
	def test_http_headers(self):
		resp = self.get(
			self.method("frappe.client.is_document_amended"),
			{"sid": self.sid, "doctype": "User", "docname": "Guest"},
		)
		self.assertEqual(resp.cache_control.max_age, 600)
		self.assertTrue(resp.cache_control.private)


class TestValidColumnsCache(IntegrationTestCase):
	def test_clear_doctype_cache(self):
		from frappe.cache_manager import clear_doctype_cache

		frappe.get_single("System Settings")
		clear_doctype_cache("System Settings")
		self.assertNotIn("System Settings", frappe.local.valid_columns)

		frappe.get_single("System Settings")
		self.assertIn("System Settings", frappe.local.valid_columns)
		self.assertIsInstance(frappe.local.valid_columns["System Settings"], list)

	def test_clear_cache(self):
		from frappe.cache_manager import clear_cache

		frappe.get_single("System Settings")
		self.assertIn("System Settings", frappe.local.valid_columns)
		self.assertIsInstance(frappe.local.valid_columns["System Settings"], list)

		clear_cache()
		self.assertEqual(frappe.local.valid_columns, {})
