"""Shared offline test helpers (isolated cache/workspace, no network)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for _path in (SRC, ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


def isolate_environment():
    """Call from setUpModule: points cache, config and workspace to a throw-away directory."""
    temp = tempfile.TemporaryDirectory(prefix="noul-tests-")
    unittest.addModuleCleanup(temp.cleanup)
    base = Path(temp.name)
    for folder in ("home", "cache", "workspace", "appdata"):
        (base / folder).mkdir()
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("NOUL_")
        and k not in ("DB_PATH", "UNPAYWALL_EMAIL", "OPENALEX_API_KEY", "S2_API_KEY", "NCBI_API_KEY")
    }
    env.update(
        {
            "HOME": str(base / "home"),
            "USERPROFILE": str(base / "home"),
            "APPDATA": str(base / "appdata"),
            "LOCALAPPDATA": str(base / "appdata"),
            "NOUL_HOME": str(base / "cache"),
            "NOUL_DB_PATH": str(base / "cache/cache.db"),
            "NOUL_WORKSPACE": str(base / "workspace"),
            # Subprocesses get a fake HOME, which hides user site-packages: hand them our import path.
            "PYTHONPATH": os.pathsep.join([str(SRC)] + [p for p in sys.path if p]),
        }
    )
    patcher = mock.patch.dict(os.environ, env, clear=True)
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    # Real network through httpx is never allowed in tests (MockTransport-based tests are unaffected).
    import httpx

    blocker = mock.patch.object(httpx.HTTPTransport, "handle_request", side_effect=no_network)
    blocker.start()
    unittest.addModuleCleanup(blocker.stop)
    # The same for urllib, which artifact_radar's liveness probes still use.
    import urllib.request

    opener = mock.patch.object(urllib.request.OpenerDirector, "open", side_effect=no_network)
    opener.start()
    unittest.addModuleCleanup(opener.stop)
    return env


def block_http(testcase):
    """Call from setUp: any `_http_json`/`_http_get` call fails the test (the papers it needs are seeded)."""
    for name in ("_http_json", "_http_get"):
        patcher = mock.patch(core_target(name), side_effect=no_network)
        patcher.start()
        testcase.addCleanup(patcher.stop)


def mcp_client(server, **kwargs):
    """An MCP client wired to `server` in memory: `async with _support.mcp_client(build_server()) as client`.

    The transport is private in the SDK, so this is the one place that imports it."""
    from mcp import Client
    from mcp.client._memory import InMemoryTransport

    return Client(InMemoryTransport(server), **kwargs)


def tmp_path(name):
    """A path named `name` inside a fresh temporary directory (removed at interpreter exit)."""
    import atexit
    import shutil
    import tempfile

    folder = tempfile.mkdtemp(prefix="noul-test-")
    atexit.register(shutil.rmtree, folder, True)
    return Path(folder) / name


def no_network(*args, **kwargs):
    raise AssertionError(f"unexpected network access: {args[:1]}")


# Phase 2 corpus: two LLM quantization papers, a human RCT and a preclinical mouse study.
PHASE2_PAPERS = {
    "llm_a": {
        "id": "doi:10.1000/qlora",
        "doi": "10.1000/qlora",
        "title": "Efficient 4-bit Quantization for LLM Fine-Tuning",
        "authors": ["Alice Smith", "Bob Jones", "Carol White"],
        "year": 2023,
        "journal": "NeurIPS",
        "citations": 420,
        "evidence": "empirical",
        "domain": "computer_science",
        "open_access": True,
        "abstract": (
            "We propose QLoRA, a low-rank adapter method for 4-bit quantization of large language models. "
            "Using a frozen 4-bit base model, we train LoRA adapters on instruction data. "
            "Our method achieves 84.5% accuracy on MMLU and improves GSM8K by 3.2 points while reducing "
            "VRAM to 48 GB with a 2.5x speedup. However, the computational cost of the quantization "
            "kernels remains high. Code is available at https://github.com/example/qlora."
        ),
        "sources": ["openalex"],
    },
    "llm_b": {
        "id": "doi:10.1000/gptq",
        "doi": "10.1000/gptq",
        "title": "Post-Training Quantization of Large Language Models",
        "authors": ["Dan Brown", "Eve Green"],
        "year": 2024,
        "journal": "ICLR",
        "citations": 150,
        "evidence": "empirical",
        "domain": "computer_science",
        "open_access": True,
        "abstract": (
            "This paper examines post-training quantization of large language models with GPTQ. "
            "We evaluate 3-bit and 4-bit quantization on MMLU and HumanEval using a transformer architecture. "
            "Results show 4-bit models improve throughput but 3-bit quantization yields mixed results "
            "on code generation. Future work should study quantization-aware training."
        ),
        "sources": ["s2"],
    },
    "rct": {
        "id": "doi:10.1000/rct",
        "doi": "10.1000/rct",
        "title": "Aerobic Exercise and Serum BDNF in Older Adults",
        "authors": ["Garcia, Maria", "Lee, Kim"],
        "year": 2021,
        "journal": "JAMA",
        "citations": 310,
        "evidence": "rct",
        "domain": "biomedical",
        "open_access": False,
        "abstract": (
            "Objective: To evaluate whether aerobic exercise increases serum BDNF in older adults. "
            "Methods: Randomized double-blind controlled trial including 1,200 patients (n = 1200) "
            "followed for 12 months. Results: Exercise increased BDNF by 18.5% (HR 0.75, 95% CI 0.62-0.91, "
            "p < 0.01). Limitations: single-center design and short follow-up."
        ),
        "sources": ["pubmed"],
    },
    "mice": {
        "id": "doi:10.1000/mice",
        "doi": "10.1000/mice",
        "title": "Exercise Induces Hippocampal BDNF Expression in Mice",
        "authors": ["Hiro Tanaka"],
        "year": 2024,
        "journal": "Cell Reports",
        "citations": 12,
        "evidence": "preclinical",
        "domain": "biomedical",
        "open_access": False,
        "abstract": (
            "We investigate how voluntary exercise regulates hippocampal BDNF expression in mice. "
            "Using an in vitro neuronal culture and wheel-running mice, we demonstrate that exercise "
            "increased BDNF by 40% (p = 0.003). However, the small sample limits generalization. "
            "Data are available upon reasonable request."
        ),
        "sources": ["openalex"],
    },
}


def seed_phase2_papers(keys=None):
    """Cache the Phase 2 fixture papers and return their ids (in `keys` order)."""
    import copy

    from noul.net import cache as _cache

    ids = []
    for key in keys or PHASE2_PAPERS:
        p = copy.deepcopy(PHASE2_PAPERS[key])
        _cache.cache_set("paper:" + p["id"], p)
        ids.append(p["id"])
    return ids


def seed_fulltext_paper(pid, text, **meta):
    """Phase 3: cache a paper plus its open-access full text (`ft:<id>`) so tools never hit the network."""
    from noul.net import cache as _cache

    paper = {
        "id": pid,
        "doi": pid.split(":", 1)[-1],
        "title": meta.pop("title", "Technical Fixture Paper"),
        "authors": ["Ada Lovelace"],
        "year": 2024,
        "journal": "arXiv",
        "citations": 1,
        "evidence": "empirical",
        "domain": "computer_science",
        "open_access": True,
        "abstract": meta.pop("abstract", ""),
        "sources": ["arxiv"],
        **meta,
    }
    _cache.cache_set("paper:" + pid, paper)
    if text is not None:
        _cache.cache_set("ft:" + pid, {"text": text, "source": "test", "url": None})
    return pid


# Where the names tests patch live. Modules call each other through module attributes, so each name has
# exactly one `mock.patch` target; `core_target` builds it.
CORE_HOMES = {
    "SEARCH_TIMEOUT": "noul.search.engine",
    "_http_get": "noul.net.http",
    "_http_json": "noul.net.http",
    "_search_crossref": "noul.providers.crossref",
    "_search_europepmc": "noul.providers.europepmc",
    "_search_openalex": "noul.providers.openalex",
    "_validate_public_url": "noul.net.http",
    "cache_get": "noul.net.cache",
    "citation_graph": "noul.library.graph",
    "resolve_paper": "noul.search.engine",
    "search": "noul.search.engine",
}


def core_target(name):
    """`mock.patch` target for a former `noul_core` symbol, e.g. `core_target("_http_json")`."""
    return f"{CORE_HOMES[name]}.{name}"


def public_url_or_raise(url):
    """Offline stand-in for `_validate_public_url`: literal private/metadata IPs are blocked, names pass."""
    import ipaddress
    import urllib.parse

    from noul.errors import HTTPError

    host = urllib.parse.urlparse(url).hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not ip.is_global:
        raise HTTPError(400, f"Blocked non-public address {ip} for host {host}")
    return url
