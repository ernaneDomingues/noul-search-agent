"""Etapa 2 (#02): open-artifact radar — extraction, evasive statements, HTTP verification, tool/CLI."""
import contextlib
import io
import json
import unittest
import urllib.error
from unittest import mock

import _support

from noul.providers import artifact_radar as ar


def setUpModule():
    global c, t, cli
    _support.isolate_environment()
    import noul_cli as cli
    import noul_core as c
    import noul_tools as t


class FakeResp:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return b""

    def getcode(self):
        return self.status


def fake_open(statuses):
    """statuses: {(url, method): status | Exception}; records calls."""
    calls = []

    def _open(req, timeout):
        calls.append((req.full_url, req.get_method(), req.headers.get("Range"), timeout))
        res = statuses.get((req.full_url, req.get_method()), 200)
        if isinstance(res, Exception):
            raise res
        if res >= 400:
            raise urllib.error.HTTPError(req.full_url, res, "err", {}, None)
        return FakeResp(res)

    return _open, calls


ABSTRACT = (
    "Code is at https://github.com/acme/fastlm.git/tree/main. Weights: huggingface.co/acme/fastlm-7b, "
    "data: https://huggingface.co/datasets/acme/corpus and https://zenodo.org/records/123456. "
    "See also (https://doi.org/10.5281/zenodo.42)."
)


class ExtractionTests(unittest.TestCase):
    def test_classifies_and_canonicalizes(self):
        got = ar.extract_artifact_urls(ABSTRACT)
        self.assertEqual(got, [
            {"url": "https://github.com/acme/fastlm", "kind": "code"},
            {"url": "https://huggingface.co/acme/fastlm-7b", "kind": "weights"},
            {"url": "https://huggingface.co/datasets/acme/corpus", "kind": "dataset"},
            {"url": "https://zenodo.org/records/123456", "kind": "dataset"},
            {"url": "https://doi.org/10.5281/zenodo.42", "kind": "dataset"},
        ])

    def test_other_hosts_and_non_artifacts(self):
        cases = {
            "https://gitlab.com/g/p": "code", "https://bitbucket.org/a/b": "code", "https://codeberg.org/a/b": "code",
            "https://civitai.com/models/4201/x": "weights", "https://www.kaggle.com/models/google/gemma": "weights",
            "https://pytorch.org/hub/pytorch_vision_resnet": "weights", "https://kaggle.com/datasets/u/d": "dataset",
            "https://figshare.com/articles/dataset/x/1": "dataset", "https://osf.io/abcd2": "dataset",
            "https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/X": "dataset",
        }
        for url, kind in cases.items():
            with self.subTest(url=url):
                self.assertEqual(ar.classify_url(url)[0], kind)
        for url in ("https://github.com/features/copilot", "https://github.com/acme", "https://huggingface.co/spaces/a/b",
                    "https://huggingface.co/docs/x", "https://doi.org/10.1000/xyz", "https://example.com/a/b"):
            with self.subTest(url=url):
                self.assertEqual(ar.classify_url(url), (None, None))

    def test_dedup_and_trailing_punctuation(self):
        got = ar.extract_artifact_urls("See github.com/a/b. And https://github.com/a/b; [github.com/a/b]")
        self.assertEqual(got, [{"url": "https://github.com/a/b", "kind": "code"}])

    def test_evasive_statements(self):
        for s in ("Data are available upon reasonable request.", "Code will be released upon publication",
                  "Weights are available from the corresponding author", "Dados disponíveis mediante solicitação"):
            with self.subTest(s=s):
                self.assertTrue(ar.has_evasive_statement(s))
        self.assertFalse(ar.has_evasive_statement("The code is available at github.com/a/b"))


class VerificationTests(unittest.TestCase):
    def test_head_then_ranged_get_fallback(self):
        url = "https://github.com/a/b"
        opener, calls = fake_open({(url, "HEAD"): 405, (url, "GET"): 206})
        with mock.patch.object(ar, "_open", opener), mock.patch.object(c, "_validate_public_url"):
            res = ar.verify_url(url)
        self.assertEqual(res, {"url": url, "verified": True, "status_code": 206})
        self.assertEqual([(m, r, to) for _, m, r, to in calls], [("HEAD", None, 5), ("GET", "bytes=0-1024", 5)])

    def test_ssrf_guard_blocks_before_any_request(self):
        opener, calls = fake_open({})
        with mock.patch.object(ar, "_open", opener):
            res = ar.verify_url("http://127.0.0.1/a/b")
        self.assertFalse(res["verified"])
        self.assertTrue(res["error"].startswith("blocked"))
        self.assertEqual(calls, [])

    def test_scan_marks_dead_links_and_keeps_unreachable(self):
        paper = {"title": "FastLM", "abstract": ABSTRACT + " Data available upon request."}
        statuses = {
            ("https://github.com/acme/fastlm", "HEAD"): 404,
            ("https://github.com/acme/fastlm", "GET"): 404,
            ("https://huggingface.co/acme/fastlm-7b", "HEAD"): urllib.error.URLError("timeout"),
            ("https://huggingface.co/datasets/acme/corpus", "HEAD"): 200,
        }
        opener, _ = fake_open(statuses)
        with mock.patch.object(ar, "_open", opener), mock.patch.object(c, "_validate_public_url"):
            res = ar.scan_artifacts_from_paper(paper)
        self.assertFalse(res["has_code"])
        self.assertIsNone(res["code_url"])
        self.assertEqual((res["has_weights"], res["artifacts"]["weights_verified"]), (True, False))
        self.assertEqual((res["has_dataset"], res["dataset_url"]), (True, "https://huggingface.co/datasets/acme/corpus"))
        self.assertTrue(res["artifacts"]["dataset_verified"])
        self.assertTrue(res["evasive_availability_statement"])
        self.assertEqual(paper["has_dataset"], True)

    def test_offline_scan_and_url_cap(self):
        text = " ".join(f"github.com/u/r{i}" for i in range(30))
        res = ar.scan_artifacts_from_paper({"abstract": text}, verify_http=False, max_urls=20)
        self.assertEqual(len(res["artifact_urls"]), 20)
        self.assertEqual((res["has_code"], res["artifacts"]["code_verified"]), (True, False))


PAPER = {
    "id": "arxiv:2403.00001", "title": "FastLM", "authors": [], "year": 2024, "journal": "arXiv", "doi": None,
    "citations": 0, "abstract": "Code: https://github.com/acme/fastlm", "evidence": "empirical",
    "open_access": True, "sources": ["arxiv"],
}


class PipelineToolCliTests(unittest.TestCase):
    def setUp(self):
        c.cache_set("paper:" + PAPER["id"], dict(PAPER))

    def test_parsers_flag_artifacts_offline(self):
        p = c._parse_s2_paper({"paperId": "s1", "title": "X", "abstract": "Data: https://osf.io/xyz12"})
        self.assertTrue(p["has_dataset"])
        self.assertFalse(p["artifacts"]["dataset_verified"])
        self.assertTrue(t._slim(p)["has_dataset"])

    def test_tool_verifies_and_updates_cache(self):
        opener, calls = fake_open({})
        with mock.patch.object(ar, "_open", opener), mock.patch.object(c, "_validate_public_url"):
            out = t.call("scan_paper_artifacts", {"id": PAPER["id"]})
        self.assertEqual((out["has_code"], out["code_url"], out["artifacts"]["code_verified"]),
                         (True, "https://github.com/acme/fastlm", True))
        self.assertEqual(len(calls), 1)
        self.assertTrue(c.cache_get("paper:" + PAPER["id"])["artifacts"]["code_verified"])

    def test_cli_no_verify(self):
        buf = io.StringIO()
        with mock.patch.object(ar, "_open", side_effect=AssertionError("no network")), \
                contextlib.redirect_stdout(buf):
            code = cli.main(["artifacts", PAPER["id"], "--no-verify"])
        out = json.loads(buf.getvalue())
        self.assertEqual((code, out["has_code"], out["verified_http"]), (0, True, False))


if __name__ == "__main__":
    unittest.main()
