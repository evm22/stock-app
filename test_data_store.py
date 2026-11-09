"""
test_data_store.py -- offline tests for data_store.py (no network).

The local backend is tested on a temp folder; the GitHub backend is tested
with a fake HTTP session, so nothing ever reaches GitHub.

Run:  python test_data_store.py
"""

import base64
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))

import data_store

TOKEN = "github_pat_SECRET_SHOULD_NEVER_LEAK"


class FakeResponse:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body or {}
        self.text = text

    def json(self):
        return self._body


class FakeSession:
    """Scripted GitHub: returns queued responses and records every call."""

    def __init__(self, gets, puts):
        self.gets, self.puts = list(gets), list(puts)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(("GET", url, headers))
        return self.gets.pop(0)

    def put(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("PUT", url, json))
        return self.puts.pop(0)


def _file(text, sha):
    return FakeResponse(200, {"content": base64.b64encode(text.encode()).decode(),
                              "encoding": "base64", "sha": sha, "size": len(text)})


class TestBackendChoice(unittest.TestCase):
    def test_local_without_config(self):
        self.assertEqual(data_store.backend_name(), "local")
        self.assertEqual(data_store.backend_name("evm22/x", None), "local")
        self.assertEqual(data_store.backend_name(None, TOKEN), "local")

    def test_github_with_config(self):
        self.assertEqual(data_store.backend_name("evm22/x", TOKEN), "github")


class TestLocalBackend(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_missing_file_returns_default(self):
        self.assertEqual(data_store.read_json("data/p.json", default={"a": 1}, local_dir=self.dir), {"a": 1})
        self.assertIsNone(data_store.read_text("data/none.csv", local_dir=self.dir))

    def test_json_round_trip_keeps_hebrew(self):
        data = {"name": "טבע", "q": 300, "items": [1, 2]}
        data_store.write_json("data/portfolio.json", data, local_dir=self.dir)
        self.assertEqual(data_store.read_json("data/portfolio.json", local_dir=self.dir), data)
        with open(os.path.join(self.dir, "data", "portfolio.json"), encoding="utf-8") as fh:
            self.assertIn("טבע", fh.read())  # readable, not \\u escapes

    def test_text_overwrite(self):
        data_store.write_text("a.txt", "one", local_dir=self.dir)
        data_store.write_text("a.txt", "two", local_dir=self.dir)
        self.assertEqual(data_store.read_text("a.txt", local_dir=self.dir), "two")

    def test_refuses_path_outside_folder(self):
        with self.assertRaises(data_store.DataStoreError):
            data_store.write_text("../escape.txt", "x", local_dir=self.dir)

    def test_bad_json_raises_clear_error(self):
        data_store.write_text("bad.json", "{not json", local_dir=self.dir)
        with self.assertRaises(data_store.DataStoreError):
            data_store.read_json("bad.json", local_dir=self.dir)


class TestGithubBackend(unittest.TestCase):
    REPO = "evm22/stock-app-data"

    def test_read_decodes_base64(self):
        s = FakeSession([_file('{"x": 1}', "sha1")], [])
        self.assertEqual(data_store.read_json("data/p.json", repo=self.REPO, token=TOKEN, session=s), {"x": 1})
        method, url, headers = s.calls[0]
        self.assertIn("/repos/evm22/stock-app-data/contents/data/p.json", url)
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")

    def test_read_missing_returns_default(self):
        s = FakeSession([FakeResponse(404)], [])
        self.assertEqual(data_store.read_json("x.json", default=[], repo=self.REPO, token=TOKEN, session=s), [])

    def test_write_new_file_has_no_sha(self):
        s = FakeSession([FakeResponse(404)], [FakeResponse(201)])
        data_store.write_json("x.json", {"a": 1}, "msg", repo=self.REPO, token=TOKEN, session=s)
        put = [c for c in s.calls if c[0] == "PUT"][0][2]
        self.assertNotIn("sha", put)
        self.assertEqual(json.loads(base64.b64decode(put["content"])), {"a": 1})
        self.assertEqual(put["message"], "msg")

    def test_write_conflict_retries_once_with_fresh_sha(self):
        s = FakeSession([_file("old", "sha1"), _file("newer", "sha2")],
                        [FakeResponse(409), FakeResponse(200)])
        data_store.write_text("x.txt", "mine", repo=self.REPO, token=TOKEN, session=s)
        puts = [c[2] for c in s.calls if c[0] == "PUT"]
        self.assertEqual([p["sha"] for p in puts], ["sha1", "sha2"])

    def test_write_second_conflict_raises(self):
        s = FakeSession([_file("old", "sha1"), _file("newer", "sha2")],
                        [FakeResponse(409), FakeResponse(409)])
        with self.assertRaises(data_store.DataStoreError):
            data_store.write_text("x.txt", "mine", repo=self.REPO, token=TOKEN, session=s)

    def test_errors_never_contain_the_token(self):
        for status in (401, 403, 500):
            s = FakeSession([FakeResponse(status, text=f"bad {TOKEN}")], [])
            with self.assertRaises(data_store.DataStoreError) as ctx:
                data_store.read_text("x.txt", repo=self.REPO, token=TOKEN, session=s)
            self.assertNotIn(TOKEN, str(ctx.exception))
            self.assertIn(str(status), str(ctx.exception))


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(unittest.TestLoader().loadTestsFromModule(sys.modules[__name__]))
    sys.exit(0 if result.wasSuccessful() else 1)
