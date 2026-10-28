"""Phase 3 (#09): hardware feasibility and inference-footprint estimator.

Deterministic, stdlib-only: model size / architecture are mined from the paper text (or given explicitly) and
memory is computed per precision as `weights = params * bytes` plus a KV cache of
`2 * layers * kv_heads * head_dim * seq_len * bytes` (Llama-style dimensions unless the paper states them)."""
import re

from noul.providers import pdf_parser
from noul.schemas.hardware import HardwareFeasibility, PrecisionMemory

BYTES_PER_ELEM = {"fp16": 2.0, "int8": 1.0, "int4": 0.5}
OVERHEAD = 1.15
DEFAULT_PARAMS_B = 7.0
MOE_EXPERT_FRACTION = 0.81  # share of a dense block's parameters living in the (replicated) expert FFNs
MAX_CONTEXT = 10_000_000

# (max params in B, layers, kv_heads, head_dim) for Llama-style decoders.
KV_CONFIGS = (
    (0.5, 24, 16, 64),
    (2.0, 24, 16, 128),
    (4.0, 32, 32, 80),
    (10.0, 32, 32, 128),
    (20.0, 40, 40, 128),
    (40.0, 48, 8, 128),
    (100.0, 80, 8, 128),
    (float("inf"), 126, 8, 128),
)
FAMILY_DEFAULTS = (
    (re.compile(r"\bBERT[-\s]?large\b", re.I), 0.34, "BERT-large"),
    (re.compile(r"\bBERT[-\s]?base\b", re.I), 0.11, "BERT-base"),
    (re.compile(r"\bGPT-?2\b", re.I), 1.5, "GPT-2"),
    (re.compile(r"\bL[Ll]a[Mm][Aa]\b|\bLlama\b", re.I), 7.0, "Llama"),
)
_WORD_NUM = {"single": 1, "one": 1, "two": 2, "four": 4, "eight": 8, "sixteen": 16}


def _core():
    import noul_core

    return noul_core


def _num(x):
    return round(float(x), 2)


_NOT_PARAMS = r"(?!\s*(?:tokens?|words?|images?|examples?|samples?|steps?|FLOPs?|pairs?|documents?|sentences?|hours?))"
_MOE = re.compile(r"(?<![\w.])(\d{1,3})\s*[x×]\s*(\d+(?:\.\d+)?)\s*B\b", re.I)
_TOTAL_ACTIVE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*B\b\s*(?:total\s+)?(?:parameters|params)?(?:\s+in total|\s+total)?"
    r"[^.;\n]{0,40}?(?<![\w.])(\d+(?:\.\d+)?)\s*B\b\s*(?:are\s+)?(?:activ(?:e|ated)|active parameters)", re.I)
_SIZE = re.compile(rf"(?<![\w.$])(\d+(?:\.\d+)?)\s?([BM])\b{_NOT_PARAMS}")
_SIZE_WORDS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(billion|million)\s+(?:parameters|params)", re.I)
_TOP_K = re.compile(r"\btop[-\s]?(\d)\b(?:\s+(?:expert|routing|gating))", re.I)


def _sizes(text, skip_spans=()):
    """[(params_b, offset)] for every model-size mention outside `skip_spans`."""
    out = []
    for m in _SIZE.finditer(text):
        if any(s <= m.start() < e for s, e in skip_spans):
            continue
        val = float(m.group(1)) / (1000.0 if m.group(2) == "M" else 1.0)
        if 0.01 <= val <= 5000:
            out.append((round(val, 3), m.start()))
    for m in _SIZE_WORDS.finditer(text):
        val = float(m.group(1)) / (1000.0 if m.group(2).lower() == "million" else 1.0)
        out.append((round(val, 3), m.start()))
    return out


def _pick_size(title, abstract, all_sizes):
    """Paper's own model size: largest in the title, else most frequent in the abstract, else in the full text."""
    for scope, rule in ((title, "title"), (abstract, "abstract")):
        found = [v for v, _ in _sizes(scope)]
        if found:
            if rule == "title":
                return max(found), f"Model size {max(found)}B taken from the title."
            best = max(set(found), key=lambda v: (found.count(v), v))
            return best, f"Model size {best}B is the most frequent size in the abstract."
    if all_sizes:
        found = [v for v, _ in all_sizes]
        best = max(set(found), key=lambda v: (found.count(v), v))
        return best, f"Model size {best}B is the most frequent size mentioned in the text."
    return None, None


def _architecture(text, moe_found):
    if moe_found or re.search(r"mixture[-\s]of[-\s]experts|\bMoE\b|sparse(?:ly)?[-\s]gated experts", text, re.I):
        return "moe"
    if re.search(r"state[-\s]space model|\bSSMs?\b|\bMamba\b|\bRWKV\b|\bLSTMs?\b|recurrent neural|\bRNNs?\b", text):
        return "rnn_ssm"
    enc = len(re.findall(r"encoder[-\s]only|\b(?:BERT|RoBERTa|DeBERTa|ELECTRA|ALBERT)\b", text))
    dec = len(re.findall(r"decoder[-\s]only|autoregressive|causal language model|\bGPT|\bL[Ll]a[Mm][Aa]|Mistral", text))
    return "encoder_only" if enc > dec else "dense"


_DIMS = {
    "layers": re.compile(r"(?<![\w.])(\d{1,3})\s+(?:transformer\s+)?(?:layers|blocks)\b|"
                         r"\b(?:num_layers|n_layers|num_hidden_layers)\s*[=:]\s*(\d{1,3})", re.I),
    "kv_heads": re.compile(r"(?<![\w.])(\d{1,3})\s+(?:KV|key[-\s]value)\s+heads\b|"
                           r"\b(?:num_key_value_heads|n_kv_heads)\s*[=:]\s*(\d{1,3})", re.I),
    "heads": re.compile(r"(?<![\w.])(\d{1,3})\s+(?:attention\s+)?heads\b|"
                        r"\b(?:num_attention_heads|n_heads)\s*[=:]\s*(\d{1,3})", re.I),
    "hidden": re.compile(r"\b(?:hidden (?:size|dimension)|d_model|model dimension)\s*(?:of|=|:)?\s*(\d{3,5})", re.I),
    "head_dim": re.compile(r"\bhead[_\s]dim(?:ension)?\s*(?:of|=|:)?\s*(\d{2,3})", re.I),
}


def _kv_config(text, size_b):
    """(layers, kv_heads, head_dim, source) — explicit values from the text override the size-based table."""
    layers, kv_heads, head_dim = next((l, h, d) for cap, l, h, d in KV_CONFIGS if size_b <= cap)
    found = {}
    for key, rx in _DIMS.items():
        m = rx.search(text)
        if m:
            found[key] = int(next(g for g in m.groups() if g))
    layers = found.get("layers", layers)
    heads = found.get("kv_heads") or found.get("heads")
    if heads:
        kv_heads = heads
    if found.get("head_dim"):
        head_dim = found["head_dim"]
    elif found.get("hidden") and (found.get("heads") or heads):
        head_dim = max(1, found["hidden"] // found.get("heads", heads))
    return layers, kv_heads, head_dim, ("paper text" if found else "size-based Llama-style defaults")


_HW_COUNT = r"(?:(?P<count>\d[\d,]*|single|one|two|four|eight|sixteen)\s*(?:[x×]\s*)?)?"
_HARDWARE = re.compile(
    _HW_COUNT + r"(?P<hw>(?:NVIDIA\s+|Nvidia\s+)?(?:Tesla\s+)?(?:A100|H100|H200|H800|A800|V100|A6000|A40|L40S?|T4|P100|"
    r"GH200|B200|RTX\s*\d{4}(?:\s*Ti)?|GTX\s*\d{3,4}|(?:AMD\s+)?MI\d{3}X?)(?:[\s-]*\d+\s*GB)?(?:\s+GPUs?)?|"
    r"(?:Google\s+)?TPU\s*v\d[a-z]?(?:-\d+)?(?:\s+(?:pods?|chips?|cores?))?)", re.I)


def _reported_hardware(text):
    hits = []
    for m in _HARDWARE.finditer(text):
        window = text[max(0, m.start() - 200):m.end() + 200]
        hits.append((0 if re.search(r"\btrain(?:ed|ing)?\b|pre-?train", window, re.I) else 1, m.start(),
                     re.sub(r"\s+", " ", m.group(0)).strip()))
    return min(hits)[2] if hits else None


def _tier(int4_vram):
    if int4_vram <= 16.0:
        return "edge_consumer_gpu"
    if int4_vram <= 48.0:
        return "workstation"
    return "datacenter_cluster"


def _positive(name, value):
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise _core().HTTPError(400, f"{name} must be a positive number")
    if not 0 < v <= 100000 or isinstance(value, bool):
        raise _core().HTTPError(400, f"{name} must be a positive number")
    return v


def estimate_hardware_specs(paper_id, total_params_b=None, active_params_b=None, context_length=8192):
    """Estimate FP16/INT8/INT4 VRAM (weights + KV cache), reported training hardware and an operational tier."""
    core = _core()
    if not isinstance(paper_id, str) or not paper_id.strip():
        raise core.HTTPError(400, "paper_id must be a non-empty string")
    total = _positive("total_params_b", total_params_b)
    active = _positive("active_params_b", active_params_b)
    try:
        seq_len = int(context_length if context_length is not None else 8192)
    except (TypeError, ValueError):
        raise core.HTTPError(400, "context_length must be a positive integer")
    if isinstance(context_length, bool) or not 0 < seq_len <= MAX_CONTEXT:
        raise core.HTTPError(400, f"context_length must be between 1 and {MAX_CONTEXT}")

    corpus = pdf_parser.get_paper_corpus(paper_id.strip())
    title, abstract = corpus["title"] or "", corpus["abstract"]
    text = "\n".join([title, abstract, corpus["text"] if corpus["source"] == "fulltext" else ""])
    notes = list(corpus["warnings"])

    moe_total = moe_active = None
    spans = []
    m = _TOTAL_ACTIVE.search(text)
    if m:
        moe_total, moe_active = float(m.group(1)), float(m.group(2))
        spans.append((m.start(), m.end()))
        notes.append(f"Explicit MoE sizes in text: {moe_total}B total, {moe_active}B active.")
    m = _MOE.search(text)
    if m:
        spans.append((m.start(), m.end()))
        if moe_total is None:
            experts, base = int(m.group(1)), float(m.group(2))
            k = int(_TOP_K.search(text).group(1)) if _TOP_K.search(text) else 2
            k = min(k, experts)
            shared = base * (1 - MOE_EXPERT_FRACTION)
            moe_total = round(shared + experts * base * MOE_EXPERT_FRACTION, 1)
            moe_active = round(shared + k * base * MOE_EXPERT_FRACTION, 1)
            notes.append(f"MoE '{m.group(0)}' interpreted as {experts} experts of {base}B with top-{k} routing "
                         f"(~{moe_total}B total, ~{moe_active}B active; attention/embeddings shared).")
    sizes = _sizes(text, spans)
    detected = sorted({v for v, _ in sizes} | ({moe_total} if moe_total else set()))

    if total is not None:
        notes.append(f"Total parameters provided by the caller: {total}B.")
    elif moe_total is not None:
        total = moe_total
    else:
        total, why = _pick_size(title, abstract, sizes)
        if total is not None:
            notes.append(why)
        else:
            for rx, size, family in FAMILY_DEFAULTS:
                if rx.search(text):
                    total = size
                    notes.append(f"No explicit model size found; assumed the {family} default ({size}B).")
                    break
            else:
                total = DEFAULT_PARAMS_B
                notes.append(f"No explicit model size or known family found; assumed the documented default "
                             f"of {DEFAULT_PARAMS_B}B parameters.")
    if active is None and moe_active is not None and total_params_b is None:
        active = moe_active

    arch = _architecture(text, moe_total is not None or (active is not None and active < total))
    if arch == "moe" and active is None:
        active = round(total * 0.28, 2)
        notes.append("MoE without an explicit active size: assumed ~28% of parameters active per token.")
    if active is not None and active > total:
        raise core.HTTPError(400, "active_params_b cannot exceed the total parameter count")

    layers, kv_heads, head_dim, kv_source = _kv_config(text, active if arch == "moe" else total)
    memory = {}
    for prec, bpe in BYTES_PER_ELEM.items():
        weights = round(total * bpe, 2)
        if arch in ("encoder_only", "rnn_ssm"):
            kv = round(weights * 0.05, 2)
        else:
            kv = round(2 * layers * kv_heads * head_dim * seq_len * bpe / 1024 ** 3, 2)
        memory[prec] = PrecisionMemory(weights_gb=weights, kv_cache_8k_context_gb=kv,
                                       recommended_min_vram_gb=round((weights + kv) * OVERHEAD, 2))
    if arch in ("encoder_only", "rnn_ssm"):
        notes.append(f"{arch}: no growing per-token KV cache; state/activation memory approximated as 5% of weights.")
    else:
        notes.append(f"KV cache for {seq_len} tokens with {layers} layers x {kv_heads} KV heads x {head_dim} head dim "
                     f"({kv_source}).")
    if seq_len != 8192:
        notes.append(f"`kv_cache_8k_context_gb` is computed for the requested context_length={seq_len}.")
    notes.append(f"recommended_min_vram_gb = (weights + KV cache) x {OVERHEAD} runtime overhead.")

    result = HardwareFeasibility(
        paper_id=corpus["paper_id"], total_parameters_billion=_num(total),
        active_parameters_billion=_num(active) if active is not None else None,
        detected_model_sizes_b=[_num(x) for x in detected], architecture_type=arch,
        fp16=memory["fp16"], int8=memory["int8"], int4=memory["int4"],
        reported_training_hardware=_reported_hardware(text),
        operational_tier=_tier(memory["int4"].recommended_min_vram_gb), notes=notes,
    ).model_dump()
    result.update({"title": corpus["title"], "context_length": seq_len, "source": corpus["source"],
                   "kv_cache_assumptions": {"layers": layers, "kv_heads": kv_heads, "head_dim": head_dim,
                                            "source": kv_source if arch not in ("encoder_only", "rnn_ssm")
                                            else "5% of weights"}})
    return result
