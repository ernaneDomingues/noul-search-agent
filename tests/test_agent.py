import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV = {}


def setUpModule():
    global c, m, t
    temp = tempfile.TemporaryDirectory(prefix="noul-tests-")
    unittest.addModuleCleanup(temp.cleanup)
    base = Path(temp.name)
    for folder in ("home", "cache", "workspace", "appdata"):
        (base / folder).mkdir()
    ENV.update({
        k: v for k, v in os.environ.items()
        if not k.startswith("NOUL_") and k not in ("DB_PATH", "UNPAYWALL_EMAIL")
    })
    ENV.update({
        "HOME": str(base / "home"),
        "USERPROFILE": str(base / "home"),
        "APPDATA": str(base / "appdata"),
        "LOCALAPPDATA": str(base / "appdata"),
        "NOUL_HOME": str(base / "cache"),
        "NOUL_DB_PATH": str(base / "cache/cache.db"),
        "NOUL_WORKSPACE": str(base / "workspace"),
    })
    env_patch = mock.patch.dict(os.environ, ENV, clear=True)
    env_patch.start()
    unittest.addModuleCleanup(env_patch.stop)

    import noul_core as c
    import noul_mcp as m
    import noul_tools as t


def rpc(*msgs):
    p = subprocess.run(
        [sys.executable, str(ROOT / "noul_mcp.py")],
        input="\n".join(json.dumps(m) for m in msgs),
        capture_output=True,
        text=True,
        env=ENV,
        cwd=ROOT,
        timeout=60,
    )
    return [json.loads(x) for x in p.stdout.splitlines() if x.strip()]


def test_mcp_handshake_and_tools():
    out = rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "bogus"},
        {"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
    )
    assert [o["id"] for o in out] == [1, 2, 3, 4, 5]
    tool_names = {tool["name"] for tool in out[1]["result"]["tools"]}
    assert tool_names >= {
        "search_papers",
        "get_paper",
        "get_fulltext",
        "query_fulltexts",
        "citation_graph",
        "related_papers",
        "download_papers",
        "save_research_dossier",
        "list_research_dossiers",
        "get_research_dossier",
        "export_papers",
    }
    assert out[2]["result"]["isError"] is True
    assert out[3]["error"]["code"] == -32601
    assert "resources" in out[4]["result"]


def test_installer_roundtrip():
    def run(*a):
        return subprocess.run([sys.executable, str(ROOT / "install.py"), *a], env=ENV, capture_output=True, text=True)

    h = Path(ENV["HOME"])
    (h / ".codex").mkdir(exist_ok=True)
    (h / ".codex/config.toml").write_text('model = "x"\n', encoding="utf-8")
    assert run("--all").returncode == 0 and run("--all").returncode == 0  # idempotent
    assert "__NOUL_DIR__" not in (h / ".claude/agents/noul-researcher.md").read_text(encoding="utf-8")
    assert json.loads((h / ".copilot/mcp-config.json").read_text(encoding="utf-8"))["mcpServers"]["noul"]["command"] == sys.executable
    assert (h / ".claude/commands/noul-search-config.md").exists()
    toml = (h / ".codex/config.toml").read_text(encoding="utf-8")
    assert toml.count("[mcp_servers.noul]") == 1 and 'model = "x"' in toml
    assert (h / ".gemini/extensions/noul/gemini-extension.json").exists()
    assert (h / ".gemini/config/skills/noul-search/SKILL.md").exists()
    assert (h / ".gemini/antigravity/mcp_config.json").exists()
    assert run("--all", "--uninstall").returncode == 0
    assert not (h / ".claude/skills/noul-search").exists()
    assert not (h / ".gemini/config/skills/noul-search").exists()
    assert (h / ".codex/config.toml").read_text(encoding="utf-8").strip() == 'model = "x"'
    assert "noul" not in json.loads((h / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]


def test_dossier_download_and_multi_paper_workflow():
    tmp_dir = Path(tempfile.mkdtemp(dir=ENV["NOUL_WORKSPACE"]))
    p1 = {
        "id": "doi:10.1000_test1",
        "title": "A Randomized Controlled Trial of Creatine on Working Memory",
        "authors": ["Alice Smith", "Bob Jones"],
        "year": 2024,
        "journal": "Journal of Cognitive Enhancement",
        "doi": "10.1000/test1",
        "citations": 42,
        "abstract": "Participants received 5g/day of creatine monohydrate for 6 weeks in a double-blind RCT. Memory scores improved significantly.",
        "evidence": "rct",
        "impact": "medium",
        "tier": "high",
        "open_access": True,
        "pdf_url": "https://example.org/test1.pdf",
        "sources": ["openalex"],
    }
    p2 = {
        "id": "arxiv:2401.00001",
        "title": "Retrieval-Augmented Generation Benchmark Across Scientific Domains",
        "authors": ["Carlos Silva"],
        "year": 2025,
        "journal": "arXiv",
        "doi": None,
        "arxiv_id": "2401.00001",
        "citations": 19,
        "abstract": "We evaluate RAG architectures on empirical benchmarks in computer science, economics, and biomedicine.",
        "evidence": "empirical",
        "impact": "medium",
        "tier": "high",
        "open_access": True,
        "pdf_url": "https://arxiv.org/pdf/2401.00001.pdf",
        "sources": ["arxiv"],
    }
    c.cache_set("paper:" + p1["id"], p1)
    c.cache_set("paper:" + p2["id"], p2)

    # 1. Test query_fulltexts with abstract fallback / cached fulltext
    c.cache_set("ft:" + p1["id"], {
        "text": "Methods: Subjects ingested 5 grams per day of creatine monohydrate or placebo for 42 days. No adverse renal effects.",
        "url": p1["pdf_url"],
        "source": "openalex",
    })
    with mock.patch.object(c, "_http_get", return_value=b"Full text unavailable") as fetch:
        q_res = t.query_fulltexts([p1["id"], p2["id"]], question="dosage grams per day")
    fetch.assert_called_once_with(p2["pdf_url"], timeout=20, binary=True, public_only=True)
    assert len(q_res["results"]) == 2
    assert q_res["results"][0]["content_source"] == "fulltext"
    assert q_res["results"][1]["content_source"] == "abstract_only"
    assert any("5 grams" in ch for ch in q_res["results"][0]["excerpts"])

    # 2. Test save_research_dossier, list_research_dossiers, get_research_dossier
    dos_dir = tmp_dir / "research"
    saved = t.save_research_dossier(
        topic="Creatine and Working Memory",
        synthesis_markdown="## Evidence Meter\nSupportive 100%\n\nCreatine improves working memory [1].",
        paper_ids=[p1["id"], p2["id"]],
        output_dir=str(dos_dir),
    )
    assert Path(saved["readme_path"]).exists()
    assert Path(saved["bibtex_path"]).exists()

    listed = t.list_research_dossiers(output_dir=str(dos_dir))
    assert any(d["slug"] == "creatine-and-working-memory" for d in listed["dossiers"])

    loaded = t.get_research_dossier("creatine-and-working-memory", output_dir=str(dos_dir))
    assert "Evidence Meter" in loaded["markdown"]
    assert len(loaded["papers"]) == 2

    # 3. Test download_papers with mocked PDF binary response
    pdf_dir = tmp_dir / "papers"
    with mock.patch.object(c, "_http_get", return_value=b"%PDF-1.4 mock pdf content bytes" * 50):
        dl = t.download_papers([p1["id"]], output_dir=str(pdf_dir))
        assert dl["downloaded_count"] == 1
        assert Path(dl["files"][0]["filepath"]).exists()

    # 4. Test export_papers (bibtex, ris, csv)
    bib = t.export_papers([p1["id"], p2["id"]], "bibtex")
    assert "@article{" in bib and "10.1000/test1" in bib
    ris = t.export_papers([p1["id"]], "ris")
    assert "TY  - JOUR" in ris
    csv_out = t.export_papers([p1["id"]], "csv")
    assert "10.1000/test1" in csv_out


@contextmanager
def http_server(**kwargs):
    srv = m.make_server("127.0.0.1", 0, **kwargs)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv.server_address[1]
    finally:
        srv.shutdown()
        srv.server_close()


def http_request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    conn.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp.status, (json.loads(raw) if raw else None), resp


def assert_http_error(code, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except c.HTTPError as e:
        assert e.code == code, f"expected HTTP {code}, got {e.code}: {e}"
    else:
        raise AssertionError(f"{getattr(fn, '__name__', fn)} did not raise HTTPError({code})")


def test_http_security_cors_and_write_block():
    rpc_call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "download_papers", "arguments": {"ids": ["doi:10.1000/x"]}}}
    with http_server(token="", cors_origins=("https://chat.openai.com",), allow_write=False) as port:
        assert http_request(port, "GET", "/tools", headers={"Origin": "https://evil.example"})[0] == 403
        assert http_request(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                            headers={"Origin": "https://evil.example"})[0] == 403
        status, _, resp = http_request(port, "GET", "/healthz", headers={"Origin": "https://chat.openai.com"})
        assert status == 200 and resp.getheader("Access-Control-Allow-Origin") == "https://chat.openai.com"
        assert http_request(port, "GET", "/healthz", headers={"Host": "evil.example"})[0] == 403  # DNS rebinding

        for tool, args in (
            ("save_research_dossier", {"topic": "x", "synthesis_markdown": "y"}),
            ("download_papers", {"ids": ["doi:10.1000/x"]}),
            ("configure_noul", {"email": "someone@university.edu"}),
        ):
            status, body, _ = http_request(port, "POST", f"/tools/{tool}", args)
            assert status == 403, (tool, status, body)
        listed = {x["name"] for x in http_request(port, "GET", "/tools")[1]["tools"]}
        assert "search_papers" in listed and not listed & m.WRITE_TOOLS
        status, body, _ = http_request(port, "POST", "/mcp", rpc_call)
        assert status == 200 and body["result"]["isError"] is True
        status, body, _ = http_request(port, "POST", "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert status == 202 and body is None

    with http_server(token="s3cret", cors_origins=(), allow_write=False) as port:
        assert http_request(port, "GET", "/tools")[0] == 401
        assert http_request(port, "GET", "/tools", headers={"Authorization": "Bearer wrong"})[0] == 401
        assert http_request(port, "GET", "/tools", headers={"Authorization": "Bearer s3cret"})[0] == 200
        assert http_request(port, "GET", "/healthz")[0] == 200

    outside = Path(ENV["NOUL_WORKSPACE"]).parent / "noul-outside-workspace"
    with mock.patch.dict(os.environ, {"NOUL_ALLOW_ANY_PATH": ""}):
        assert_http_error(403, t.save_research_dossier, "x", "y", output_dir=str(outside))
        assert_http_error(403, t.download_papers, ["doi:10.1000/x"], output_dir=str(outside))
        assert_http_error(403, t.list_research_dossiers, output_dir="../escape")
    assert not outside.exists()


def test_october_arxiv_id_not_confused_with_doi():
    assert c._norm_doi("2410.12345") is None
    assert c._norm_doi("arxiv:2410.12345") is None
    assert c._norm_doi("10.1038/s41586-020-2649-2") == "10.1038/s41586-020-2649-2"
    assert c._norm_doi("https://doi.org/10.1038/s41586-020-2649-2") == "10.1038/s41586-020-2649-2"

    urls = []

    def fake_json(url, *a, **kw):
        urls.append(url)
        return {"paperId": "f" * 40, "externalIds": {"ArXiv": "2410.12345"}, "title": "An October preprint",
                "year": 2024, "authors": [{"name": "A. Author"}], "citationCount": 0}

    with mock.patch.object(c, "_http_json", side_effect=fake_json):
        paper = c.resolve_paper("2410.12345")
    assert urls and "ARXIV:2410.12345" in urls[0], urls
    assert not any("doi.org" in u for u in urls), urls
    assert paper["title"] == "An October preprint"


def test_journal_impact_no_false_q1():
    for venue in ("Journal of Food Science", "Procedia Computer Science", "Stem Cells International"):
        impact, tier = c._estimate_impact(0, 2024, venue)
        assert tier not in ("top", "Q1") and "Q1" not in tier, (venue, impact, tier)
        assert impact != "high", (venue, impact)
    assert c._estimate_impact(0, 2024, "Nature") == ("high", "top")


def test_classifier_negative_contexts():
    assert c.classify_evidence("A cross-sectional study. Future meta-analyses are needed.") == "cohort"
    assert c.classify_evidence("", "A cross-sectional study. Future meta-analyses are needed.") == "cohort"
    assert c.classify_evidence("A non-randomized clinical trial of X") != "rct"
    assert c.classify_evidence("A randomized controlled trial of X") == "rct"
    assert c.classify_evidence("Creatine and cognition: a systematic review and meta-analysis") == "meta-analysis"
    assert c.classify_evidence("A cohort study", "Earlier randomized controlled trials reported benefits.") == "cohort"
    assert c.classify_evidence("Experiments in mice", "Previous meta-analyses assessed human outcomes.") == "preclinical"
    assert c.classify_evidence("An intervention study", pub_types=["Randomized Controlled Trial"]) == "rct"


def test_ssrf_protection():
    for url in (
        "http://127.0.0.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://10.0.0.5/internal",
        "file:///etc/passwd",
    ):
        try:
            c._validate_public_url(url)
        except c.HTTPError:
            continue
        raise AssertionError(f"{url} was not blocked")
    addresses = [
        (c.socket.AF_INET, c.socket.SOCK_STREAM, c.socket.IPPROTO_TCP, "", ("8.8.8.8", 443)),
        (c.socket.AF_INET, c.socket.SOCK_STREAM, c.socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
    ]
    with mock.patch.object(c.socket, "getaddrinfo", return_value=addresses):
        assert_http_error(400, c._validate_public_url, "https://example.org/paper.pdf")
    with mock.patch.object(c.socket, "getaddrinfo", return_value=addresses[:1]):
        assert c._validate_public_url("https://example.org/paper.pdf") == "https://example.org/paper.pdf"
    request = c.urllib.request.Request("https://example.org/paper.pdf")
    assert_http_error(400, c._PublicRedirectHandler().redirect_request,
                      request, None, 302, "Found", {}, "http://127.0.0.1/private")


def test_config_and_deterministic_evidence_meter():
    cfg_file = Path(c.get_config()["config_file"])
    try:
        assert not cfg_file.exists()
        before = t.configure_noul()
        assert before["email"] is None or before["email_source"] != "config"
        assert not cfg_file.exists(), "reading the configuration must not write it"

        cfg = t.configure_noul(email="email:user@university.edu")
        assert cfg["email"] == "user@university.edu" and cfg["email_source"] == "config"
        assert cfg["unpaywall_enabled"] is True and cfg_file.exists()
        assert_http_error(400, t.configure_noul, email="not-an-email")
        assert_http_error(400, t.call, "configure_noul", {"bogus": 1})
    finally:
        t.configure_noul(clear=True)
    assert not cfg_file.exists()

    c.cache_set("paper:doi:10.1000_meter-ma", {"id": "doi:10.1000_meter-ma", "title": "MA", "evidence": "meta-analysis"})
    items = [
        {"id": "doi:10.1000_meter-ma", "stance": "supports", "evidence": "other"},  # cached design wins
        {"id": "x2", "stance": "contrary", "evidence": "preclinical"},
        {"id": "x3", "stance": "mixed"},
        {"id": "doi:10.1000_meter-ma", "stance": "contrary"},  # duplicate
        {"id": "x4", "stance": "banana"},
    ]
    first = t.evidence_meter(items)
    assert first == t.evidence_meter(items)  # deterministic
    assert first["total_papers"] == 3
    assert first["raw_counts"] == {"supportive": 1, "inconclusive": 1, "contrary": 1}
    assert first["raw_percentages"] == {"supportive": 33.3, "inconclusive": 33.3, "contrary": 33.3}
    assert first["weighted_percentages"] == {"supportive": 66.7, "inconclusive": 20.0, "contrary": 13.3}
    assert first["breakdown"][0]["evidence"] == "meta-analysis"
    assert [s.get("reason") for s in first["skipped"]] == ["duplicate id", "unknown stance 'banana'"]
    assert_http_error(400, t.evidence_meter, [])


class AgentTestSuite(unittest.TestCase):
    def setUp(self):
        network = mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Unexpected network I/O"))
        blocked = network.start()
        self.addCleanup(network.stop)
        self.addCleanup(blocked.assert_not_called)

    def test_p0_5_config_email_and_database_precedence(self):
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {
            "NOUL_HOME": cache, "NOUL_EMAIL": "", "UNPAYWALL_EMAIL": "",
            "NOUL_DB_PATH": str(Path(cache) / "noul.db"), "DB_PATH": str(Path(cache) / "legacy.db"),
        }):
            with mock.patch.object(c, "_http_json", return_value={}) as fetch:
                c._search_openalex("offline")
                c._search_crossref("offline")
                self.assertTrue(all("mailto" not in call.args[0] for call in fetch.call_args_list))
            c.cache_set("precedence", {"value": "noul"})
            self.assertEqual(c.cache_get("precedence"), {"value": "noul"})
            self.assertFalse((Path(cache) / "legacy.db").exists())
            with mock.patch.dict(os.environ, {"NOUL_DB_PATH": "", "NOUL_EMAIL": "env@university.edu"}):
                c.cache_set("precedence", {"value": "legacy"})
                self.assertEqual(c.cache_get("precedence"), {"value": "legacy"})
                self.assertEqual(c.get_config()["email_source"], "env")
                c.set_config(email="config@university.edu")
                self.assertEqual(c.get_config()["email"], "config@university.edu")
                self.assertEqual(c.set_config(clear=True)["email"], "env@university.edu")
            with mock.patch.dict(os.environ, {"NOUL_EMAIL": "research@noul.local"}):
                self.assertIsNone(c.get_config()["email"])

    def test_p0_6_external_core_requires_explicit_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            agent = root / "agent"
            external = root / "noul-search-app"
            agent.mkdir()
            (external / "noul").mkdir(parents=True)
            for name in ("noul_core.py", "noul_tools.py"):
                shutil.copyfile(ROOT / name, agent / name)
            (external / "noul/__init__.py").write_text("", encoding="utf-8")
            (external / "noul/core.py").write_text("EV_W = {'other': 0.3}\nSOURCES = ()\n", encoding="utf-8")
            probe = (
                "import os, noul_tools as t; print(t.c.__name__); "
                "assert 'DB_PATH' not in os.environ; assert 'UNPAYWALL_EMAIL' not in os.environ"
            )
            for explicit, expected in ((None, "noul_core"), (str(external), "noul.core")):
                env = {**ENV}
                if explicit:
                    env["NOUL_APP_DIR"] = explicit
                result = subprocess.run([sys.executable, "-c", probe], cwd=agent, env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)

    def test_p0_8_sqlite_connections_close_on_success_and_error(self):
        with c._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?)", ("close-test", '{"committed": true}', 0))
        self.assertEqual(c.cache_get("close-test", max_age=0), {"committed": True})
        with self.assertRaises(c.sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with c._conn() as failed:
                failed.execute("DELETE FROM kv WHERE k = 'close-test'")
                raise RuntimeError("rollback")
        with self.assertRaises(c.sqlite3.ProgrammingError):
            failed.execute("SELECT 1")
        self.assertEqual(c.cache_get("close-test", max_age=0), {"committed": True})

    def test_p0_9_search_returns_before_slow_source_finishes(self):
        release, finished, returned = threading.Event(), threading.Event(), threading.Event()
        result = {}

        def slow(*args, **kwargs):
            try:
                release.wait(5)
                return []
            finally:
                finished.set()

        def search():
            try:
                result.update(c.search("timeout regression", sources=["openalex", "europepmc"]))
            finally:
                returned.set()

        with mock.patch.object(c, "_search_openalex", side_effect=slow), \
                mock.patch.object(c, "_search_europepmc", return_value=[]), \
                mock.patch.object(c, "SEARCH_TIMEOUT", 0.05):
            worker = threading.Thread(target=search)
            worker.start()
            try:
                self.assertTrue(returned.wait(1), "search waited for the timed-out source")
                self.assertFalse(finished.is_set())
            finally:
                release.set()
                worker.join(timeout=5)
                self.assertTrue(finished.wait(1))
        self.assertEqual(result["sources"], ["europepmc"])
        self.assertTrue(any("openalex: TimeoutError" in w for w in result["warnings"]))

    def test_p0_10_http_byte_cap_and_html_fulltext(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = {}
        response.read.side_effect = [b"1234", b"5678", b""]
        with mock.patch.object(c.urllib.request, "urlopen", return_value=response):
            assert_http_error(413, c._http_get, "https://example.org/paper.pdf", max_bytes=6)
        self.assertEqual(response.read.call_args_list, [mock.call(65536), mock.call(65536)])
        response.__exit__.assert_called_once()

        paper = {"id": "test:html-cleaning", "title": "HTML study", "pdf_url": "https://example.org/article"}
        boilerplate = "".join(f"<{tag}>{'boilerplate ' * 300}</{tag}>" for tag in
                              ("script", "style", "nav", "header", "footer"))
        with mock.patch.object(c, "resolve_paper", return_value=paper), \
                mock.patch.object(c, "_http_get", return_value=(boilerplate + "<p>Sign in</p>").encode()):
            self.assertFalse(c.fulltext(paper["id"])["available"])
        article = "<article>" + ("Methods and results of our study. " * 100) + "</article>"
        with mock.patch.object(c, "resolve_paper", return_value=paper), \
                mock.patch.object(c, "_http_get", return_value=(boilerplate + article).encode()) as fetch:
            self.assertTrue(c.fulltext(paper["id"])["available"])
        fetch.assert_called_once_with(paper["pdf_url"], timeout=20, binary=True, public_only=True)
        self.assertNotIn("boilerplate", c.cache_get("ft:" + paper["id"])["text"])

    def test_p0_12_tool_argument_validation(self):
        with mock.patch.object(c, "search") as search:
            for args in ([], "", {}, {"query": None}, {"query": "valid", "typo": 1}):
                with self.subTest(args=args):
                    assert_http_error(400, t.call, "search_papers", args)
            search.assert_not_called()
        self.assertEqual(t.call("configure_noul", None), t.configure_noul())

    def test_p0_14_europepmc_source_search(self):
        fixture = {"resultList": {"result": [{
            "title": "A study", "doi": "10.1000/europepmc", "pmid": "123", "pmcid": "PMC123",
            "pubYear": "2024", "isOpenAccess": "Y", "citedByCount": 7,
            "abstractText": "<p>Trial methods and findings</p>",
            "authorList": {"author": [{"firstName": "Alice", "lastName": "Smith"}]},
            "journalInfo": {"journal": {"title": "Journal of Testing"}},
            "pubTypeList": {"pubType": ["Randomized Controlled Trial"]},
        }]}}
        with mock.patch.object(c, "_http_json", return_value=fixture) as fetch:
            result = c.search("Europe PMC regression", filters={"year_min": 2020, "open_access": True},
                              sources=["europepmc"])
        params = c.urllib.parse.parse_qs(c.urllib.parse.urlparse(fetch.call_args.args[0]).query)
        self.assertEqual(params["resultType"], ["core"])
        self.assertIn("PUB_YEAR:[2020 TO 3000]", params["query"][0])
        self.assertIn("OPEN_ACCESS:y", params["query"][0])
        self.assertEqual(result["sources"], ["europepmc"])
        paper = result["papers"][0]
        self.assertEqual((paper["evidence"], paper["authors"]), ("rct", ["Alice Smith"]))
        self.assertEqual(c.resolve_paper(paper["id"])["doi"], "10.1000/europepmc")
        self.assertEqual(paper["pmc_id"], "PMC123")
        self.assertTrue(paper["open_access"])

    def test_installer_backups_runners_and_corrupt_json(self):
        with tempfile.TemporaryDirectory() as home:
            env = {**ENV, "HOME": home, "USERPROFILE": home}
            config = Path(home) / ".claude.json"
            original = '{"mcpServers": {"noul": {"command": "previous-command"}}}\n'
            config.write_text(original, encoding="utf-8")

            def run(*args):
                return subprocess.run([sys.executable, str(ROOT / "install.py"), "claude", *args],
                                      env=env, capture_output=True, text=True, timeout=30)

            self.assertEqual(run("--dry-run").returncode, 0)
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertFalse(config.with_suffix(".json.bak").exists())
            for runner in ("uvx", "npx"):
                result = run("--runner", runner)
                self.assertEqual(result.returncode, 0, result.stderr)
                entry = json.loads(config.read_text(encoding="utf-8"))["mcpServers"]["noul"]
                expected_command = "cmd" if runner == "npx" and os.name == "nt" else runner
                self.assertEqual(entry["command"], expected_command)
                self.assertEqual(entry["args"][-2:], ["noul-search-agent", "mcp"])
                self.assertEqual(config.with_suffix(".json.bak").read_text(encoding="utf-8"), original)
            config.write_text("{ broken JSON", encoding="utf-8")
            result = run()
            self.assertEqual(result.returncode, 1)
            self.assertIn(str(config), result.stderr)
            self.assertEqual(config.read_text(encoding="utf-8"), "{ broken JSON")
            self.assertEqual(config.with_suffix(".json.bak").read_text(encoding="utf-8"), original)

    def test_cli_config_and_meter(self):
        with tempfile.TemporaryDirectory() as cache:
            env = {**ENV, "NOUL_HOME": cache, "NOUL_DB_PATH": str(Path(cache) / "cache.db")}

            def run(*args, stdin=None):
                result = subprocess.run([sys.executable, str(ROOT / "noul_cli.py"), *args],
                                        env=env, input=stdin, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            cfg = run("config", "email:user@university.edu", "--download-dir", "downloads")
            self.assertEqual(cfg["email_source"], "config")
            self.assertEqual(run("config")["download_dir"], "downloads")
            items = [{"id": "test:meter-cli", "stance": "supportive", "evidence": "rct"}]
            self.assertEqual(run("meter", json.dumps(items))["raw_percentages"]["supportive"], 100.0)
            self.assertEqual(run("meter", "-", stdin=json.dumps(items))["total_papers"], 1)
            self.assertEqual(run("config", "--clear")["email_source"], "none")
            self.assertFalse((Path(cache) / "config.json").exists())

    def test_http_write_requires_both_opt_in_and_token(self):
        body = {"topic": "HTTP test", "synthesis_markdown": "Saved with authentication."}
        with http_server(token="", allow_write=True) as port:
            self.assertEqual(http_request(port, "POST", "/tools/save_research_dossier", body)[0], 403)
        with tempfile.TemporaryDirectory(dir=ENV["NOUL_WORKSPACE"]) as output:
            body["output_dir"] = output
            with http_server(token="test-token", allow_write=True) as port:
                self.assertEqual(http_request(port, "POST", "/tools/save_research_dossier", body)[0], 401)
                headers = {"Authorization": "Bearer test-token", "X-Forwarded-Proto": "https"}
                status, saved, _ = http_request(port, "POST", "/tools/save_research_dossier", body, headers)
                self.assertEqual(status, 200)
                self.assertIn("Saved with authentication.", Path(saved["readme_path"]).read_text(encoding="utf-8"))
                status, spec, _ = http_request(port, "GET", "/openapi.json", headers=headers)
                self.assertEqual(status, 200)
                self.assertEqual(spec["servers"], [{"url": f"https://127.0.0.1:{port}"}])
                self.assertIn("/tools/save_research_dossier", spec["paths"])
                self.assertEqual(http_request(
                    port, "POST", "/mcp", headers={**headers, "Content-Length": str(m.MAX_BODY_BYTES + 1)}
                )[0], 413)

    def test_protocol_negotiation_and_cloud_run_binding(self):
        responses = rpc(*[
            {"jsonrpc": "2.0", "id": i, "method": "initialize", "params": {"protocolVersion": version}}
            for i, version in enumerate(("2025-03-26", "2024-11-05", "unsupported"), 1)
        ])
        self.assertEqual([r["result"]["protocolVersion"] for r in responses],
                         ["2025-03-26", "2024-11-05", "2025-06-18"])
        with mock.patch.object(m, "serve_http") as serve, \
                mock.patch.dict(os.environ, {"K_SERVICE": "test", "PORT": "8080", "NOUL_HTTP_HOST": ""}):
            m.main(["--http"])
            serve.assert_called_with(8080, "0.0.0.0")
            with mock.patch.dict(os.environ, {"NOUL_HTTP_HOST": "127.0.0.1"}):
                m.main(["--http"])
                serve.assert_called_with(8080, "127.0.0.1")
                m.main(["--http", "9000", "--host", "localhost"])
                serve.assert_called_with(9000, "localhost")

    def test_workspace_dossiers_stay_confined(self):
        with tempfile.TemporaryDirectory() as external:
            outside = Path(external)
            (outside / "README.md").write_text("outside workspace", encoding="utf-8")
            index = {"outside": {"slug": "outside", "topic": "Outside", "path": str(outside),
                                 "readme_path": str(outside / "README.md")}}
            with mock.patch.object(c, "cache_get", return_value=index):
                self.assertEqual(t.list_research_dossiers()["dossiers"], [])
                assert_http_error(404, t.get_research_dossier, "outside")

    def test_workspace_symlinks_stay_confined(self):
        with tempfile.TemporaryDirectory(dir=ENV["NOUL_WORKSPACE"]) as workspace, \
                tempfile.TemporaryDirectory() as external, \
                mock.patch.dict(os.environ, {"NOUL_WORKSPACE": workspace, "NOUL_ALLOW_ANY_PATH": ""}):
            root, outside = Path(workspace), Path(external)
            (outside / "README.md").write_text("unchanged", encoding="utf-8")
            research = root / "research"
            research.mkdir()
            try:
                (research / "escape").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"Symlinks unavailable: {exc}")
            assert_http_error(403, t.save_research_dossier, "escape", "overwrite")
            self.assertEqual(t.list_research_dossiers()["dossiers"], [])
            (research / "local").mkdir()
            (research / "local/README.md").symlink_to(outside / "README.md")
            assert_http_error(403, t.save_research_dossier, "local", "overwrite")
            self.assertEqual((outside / "README.md").read_text(encoding="utf-8"), "unchanged")

            paper = {"id": "test:symlink", "title": "Symlink test"}
            pdf_dir = root / "papers"
            pdf_dir.mkdir()
            (pdf_dir / c._safe_filename(paper)).symlink_to(outside / "paper.pdf")
            with mock.patch.object(c, "resolve_paper", return_value=paper):
                assert_http_error(403, c.download_paper_pdf, paper["id"], str(pdf_dir))
            self.assertFalse((outside / "paper.pdf").exists())

    def test_1_mcp(self):
        test_mcp_handshake_and_tools()

    def test_2_installer(self):
        test_installer_roundtrip()

    def test_3_workflow(self):
        test_dossier_download_and_multi_paper_workflow()

    def test_p0_1_http_security_cors_and_write_block(self):
        test_http_security_cors_and_write_block()

    def test_p0_2_october_arxiv_id_not_confused_with_doi(self):
        test_october_arxiv_id_not_confused_with_doi()

    def test_p0_3_journal_impact_no_false_q1(self):
        test_journal_impact_no_false_q1()

    def test_p0_4_classifier_negative_contexts(self):
        test_classifier_negative_contexts()

    def test_p0_10_ssrf_protection(self):
        test_ssrf_protection()

    def test_config_and_deterministic_evidence_meter(self):
        test_config_and_deterministic_evidence_meter()


if __name__ == "__main__":
    unittest.main()
