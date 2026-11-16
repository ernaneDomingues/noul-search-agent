"""Prompts and decoding: verbatim prompt templates, their variables, decoding and training hyperparameters (`extract_prompts_and_decoding`)."""

import re

from noul.evidence import untrusted as _untrusted
from noul.providers import pdf_parser
from noul.tools.technical import common as _common

MAX_PROMPTS = 30


MAX_PROMPT_CHARS = 4000


_PROMPT_LABEL = re.compile(
    r"^[ \t>*_#]*(?P<label>(?:system|user|assistant)\s+(?:prompt|message)|prompt\s+template|(?:few-shot\s+|cot\s+)?"
    r"prompt(?:\s+\d+)?|instructions?|system|user)[ \t*_]*:[ \t]*(?P<rest>[^\n]*)$",
    re.I | re.M,
)


_FENCE = re.compile(r"^[ \t]*```[ \t]*(?P<lang>[\w+-]*)[ \t]*\n(?P<body>.*?)^[ \t]*```[ \t]*$", re.S | re.M)


_QUOTED = re.compile(r"[\"“]([^\"“”]{20,1500})[\"”]")


_VAR_DOUBLE = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)\s*\}\}")


_VAR_SINGLE = re.compile(r"(?<![{\\$\w])\{([A-Za-z_][\w.-]*)\}(?!\})")


_VAR_TAG = re.compile(r"<(?!/)([a-z_][a-z0-9_-]{1,30})>", re.I)


_HTML_TAGS = frozenset("a b i u p br hr em li ol ul div span table tr td th sup sub code pre img s".split())


_PROMPT_CUES = re.compile(
    r"let'?s think step by step|you are an? (?:expert|helpful|assistant|\w+ (?:assistant|expert))|"
    r"answer the (?:following )?question|respond (?:only )?(?:in|with) json|^\s*(?:question|answer|context)\s*:",
    re.I | re.M,
)


_PROMPT_HINT = re.compile(r"\bprompts?\b|\binstructions?\b|\btemplate\b", re.I)


_HP_SEP = (
    r"(?:\s*(?:=|:|≈|\bof\b|\bis\b|\bwas\b|\bset\s+to\b|\bequal\s+to\b|\bto\b|\bat\b|\bfixed\s+at\b|\(\s*)\s*)*\s*"
)


_HP_NUM = r"(?P<v>\d+(?:\.\d+)?|\.\d+)"


_HYPERPARAMS = (
    ("temperature", r"\b(?:sampling\s+)?(?:temperature|temp)\b(?:\s*\(\s*T\s*\))?"),
    ("temperature", r"(?<![\w.])T(?=\s*=)"),
    ("top_p", r"\btop[-_ ]?p\b|\bnucleus\s+sampling(?:\s+with)?(?:\s+(?:p|threshold|probability))?|\bnucleus\s+p\b"),
    ("top_k", r"\btop[-_ ]?k(?:\s+sampling)?\b"),
    (
        "max_tokens",
        r"\bmax(?:imum)?[-_ ]?(?:number\s+of\s+)?(?:new[-_ ]|generated[-_ ]|generation[-_ ]|output[-_ ])?"
        r"(?:tokens|length|gen(?:eration)?[-_ ]len(?:gth)?)\b",
    ),
    ("repetition_penalty", r"\brepetition[-_ ]penalty\b"),
    ("beam_size", r"\b(?:beam[-_ ](?:size|width)|num[-_]beams|number\s+of\s+beams)\b"),
    ("seed", r"\b(?:random\s+)?seed\b"),
)


_HP_PATTERNS = [
    (key, re.compile(rf"(?:{name}){_HP_SEP}{_HP_NUM}(?![\w.]*\d)", 0 if name.startswith("(?<!") else re.I))
    for key, name in _HYPERPARAMS
]  # the bare `T = 0.2` form stays case-sensitive


_HP_REVERSED = (
    (
        "beam_size",
        re.compile(
            r"\b(?P<v>\d+)\s+beams\b|\bbeam\s+search\s+with\s+(?:a\s+)?(?:beam\s+)?"
            r"(?:size|width)?\s*(?:of\s+)?(?P<v2>\d+)\b",
            re.I,
        ),
    ),
    (
        "max_tokens",
        re.compile(r"\b(?:up\s+to|(?:a\s+)?maximum\s+of)\s+(?P<v>\d+)\s+(?:new\s+|generated\s+)?tokens\b", re.I),
    ),
)


_HP_RANGES = {
    "temperature": (0, 5),
    "top_p": (0, 1),
    "top_k": (1, 100000),
    "max_tokens": (1, 10**7),
    "repetition_penalty": (0.5, 5),
    "beam_size": (1, 1000),
    "seed": (0, 2**63),
}


_HP_INT = {"top_k", "max_tokens", "beam_size", "seed"}


_DECODING = (
    ("greedy", r"\bgreedy(?:\s+decoding)?\b"),
    ("beam_search", r"\bbeam\s+search\b"),
    ("nucleus_sampling", r"\bnucleus\s+sampling\b|\btop[-_ ]?p\s+sampling\b"),
    ("top_k_sampling", r"\btop[-_ ]?k\s+sampling\b"),
    ("self_consistency", r"\bself[-_ ]consistency\b"),
)


def prompt_variables(text):
    """`{var}`, `{{var}}` and `<var>` placeholders, in order of appearance (verbatim names)."""
    found = []
    for m in sorted(
        [*_VAR_DOUBLE.finditer(text), *_VAR_SINGLE.finditer(text), *_VAR_TAG.finditer(text)], key=lambda m: m.start()
    ):
        name = m.group(1)
        if m.re is _VAR_TAG and (
            name.lower() in _HTML_TAGS
            or f"</{name}>" not in text
            and name.lower() not in ("context", "question", "input", "answer", "document", "query")
        ):
            continue
        if name not in found:
            found.append(name)
    return found


def _is_prompt_like(text):
    return bool(
        _VAR_DOUBLE.search(text)
        or _VAR_SINGLE.search(text)
        or _PROMPT_CUES.search(text)
        or any(m.group(1).lower() not in _HTML_TAGS for m in _VAR_TAG.finditer(text))
    )


def _labeled_block(text, m):
    """Body of `Label: ...` — rest of the line plus following lines up to a blank line/next label/fence."""
    rest, pos = m.group("rest"), m.end()
    fence = _FENCE.match(text, pos + 1) if not rest.strip() else None
    if fence:
        return fence.group("body").rstrip("\n"), fence.end()
    lines, end = ([rest] if rest.strip() else []), m.end()
    for line_m in re.finditer(r"\n([^\n]*)", text[pos:]):
        line = line_m.group(1)
        if not line.strip():
            if lines:
                break
            continue
        if _PROMPT_LABEL.match(line) or line.strip().startswith("```"):
            break
        lines.append(line)
        end = pos + line_m.end()
    return "\n".join(lines).strip("\n"), end


def _find_prompts(text):
    found, spans = [], []

    def free(s, e):
        return not any(a < e and s < b for a, b in spans)

    def add(start, end, kind, label, body):
        body = body.strip("\n")
        if len(body.strip()) < 8 or not free(start, end):
            return
        spans.append((start, end))
        found.append({"start": start, "type": kind, "label": label, "text": body[:MAX_PROMPT_CHARS]})

    labels = list(_PROMPT_LABEL.finditer(text))
    for m in labels:
        label = re.sub(r"\s+", " ", m.group("label")).strip()
        body, end = _labeled_block(text, m)
        low = label.lower()
        kind = (
            "system"
            if low.startswith("system")
            else "user"
            if low.startswith("user")
            else "assistant"
            if low.startswith("assistant")
            else "template"
            if "template" in low
            else "instruction"
            if low.startswith("instruction")
            else "prompt"
        )
        # a lone `Instruction:`/`User:` line in prose must still look like a prompt
        if kind in ("instruction", "user", "prompt") and not _is_prompt_like(body) and len(body) < 40:
            continue
        add(m.start(), max(end, m.end()), kind, label.title() if label.islower() else label, body)
    for m in _FENCE.finditer(text):
        body, lang = m.group("body"), m.group("lang").lower()
        if lang == "json" or (body.lstrip().startswith(("{", "[")) and not _VAR_SINGLE.search(body)):
            continue
        context = text[max(0, m.start() - 300) : m.start()]
        if lang in ("python", "py", "bash", "sh") and not _is_prompt_like(body):
            continue
        if _is_prompt_like(body) or _PROMPT_HINT.search(context):
            add(m.start(), m.end(), "code_block", "Code block", body)
    for m in _QUOTED.finditer(text):
        if _is_prompt_like(m.group(1)):
            add(m.start(), m.end(), "quoted", "Quoted prompt", m.group(1))
    for m in re.finditer(r"(?:^|\n\s*\n)(?P<para>(?:[^\n]+\n?)+)", text):
        para = m.group("para")
        if _is_prompt_like(para) and (
            _VAR_DOUBLE.search(para) or _VAR_SINGLE.search(para) or _PROMPT_CUES.search(para)
        ):
            s = m.start("para")
            add(s, s + len(para), "inline", "Prompt text", para.rstrip())
    found.sort(key=lambda p: p["start"])
    return found[:MAX_PROMPTS]


def _num(raw, key):
    value = float(raw)
    if key in _HP_INT:
        if not value.is_integer():
            return None
        value = int(value)
    lo, hi = _HP_RANGES[key]
    return value if lo <= value <= hi else None


_NUMTXT = r"(?P<v>\d+(?:\.\d+)?(?:\s*[eE]\s*[-−]?\s*\d+)?|\.\d+|10\s*\^?\s*[-−]\s*\d+|\d+(?:\.\d+)?\s*[×x·]\s*10\s*\^?\s*[-−]\s*\d+)"


_OPTIMIZERS = r"AdamW|Adam|Adafactor|SGD|RMSprop|LAMB|Adagrad|Lion|Muon"


# Training hyperparameters: (name, pattern with a `v` group). Values are kept as written (e.g. "10−9", "1e-4").
_TRAIN_HP = (
    (
        "optimizer",
        re.compile(
            rf"\b(?P<v>{_OPTIMIZERS})\b(?=[^.\n]{{0,30}}\b(?:optimi[sz]er|with|lr|learning)\b)"
            rf"|\b(?:optimi[sz]e\w*|optimi[sz]er|using|use)\b[^.\n]{{0,25}}?\b(?P<v2>{_OPTIMIZERS})\b",
        ),
    ),
    ("learning_rate", re.compile(rf"\b(?:learning\s+rate|\blr)\s*(?:of|=|:|is|was|set\s+to)?\s*{_NUMTXT}", re.I)),
    (
        "lr_schedule",
        re.compile(
            r"\b(?P<v>inverse[\s-]+square[\s-]*root|cosine(?:\s+annealing)?|linear(?:\s+decay)?|polynomial|"
            r"step|exponential|one[\s-]?cycle)\s+(?:learning[\s-]+rate\s+)?(?:schedule|decay|annealing|scheduler)\b",
            re.I,
        ),
    ),
    (
        "warmup_steps",
        re.compile(
            r"\bwarm[\s-]?up(?:_steps|\s+steps)?\s*(?:of|=|:|over|for)?\s*(?P<v>\d{1,3}(?:,?\d{3}){0,3})\b"
            r"|(?<![\d,])(?P<v2>\d{1,3}(?:,?\d{3}){0,3})\s+warm[\s-]?up\s+steps\b",
            re.I,
        ),
    ),
    ("beta1", re.compile(rf"(?:β|beta)\s*_?\s*[1₁]\s*[=:]\s*{_NUMTXT}", re.I)),
    ("beta2", re.compile(rf"(?:β|beta)\s*_?\s*[2₂]\s*[=:]\s*{_NUMTXT}", re.I)),
    ("epsilon", re.compile(rf"(?<![A-Za-z])(?:ϵ|ε|epsilon|eps)(?!\s*_?\s*ls)\s*[=:]\s*{_NUMTXT}", re.I)),
    (
        "dropout",
        re.compile(
            r"(?:\bdropout\b|\bP_?\s*drop\b)[^.\n]{0,30}?(?:=|\bof\b|\brate\b|:)\s*(?P<v>0?\.\d+|\d{1,2}\s*%)", re.I
        ),
    ),
    (
        "label_smoothing",
        re.compile(
            r"\blabel\s+smoothing\b[^.\n]{0,30}?(?:=|\bof\b|\bvalue\b|:)\s*(?P<v>0?\.\d+)"
            r"|(?:ϵ|ε|epsilon)\s*_?\s*ls\s*=\s*(?P<v2>0?\.\d+)",
            re.I,
        ),
    ),
    (
        "batch_size",
        re.compile(
            r"\bbatch\s+size\s*(?:of|=|:|is|was)?\s*(?P<v>\d{1,3}(?:,?\d{3}){0,3})"
            r"|\bbatch\w*\b[^.\n]{0,30}?(?:about|approximately|roughly|~)?\s*(?<![\d,])(?P<v2>\d{1,3}(?:,?\d{3}){0,3})\s+(?:source\s+|target\s+)?tokens\b",
            re.I,
        ),
    ),
    ("epochs", re.compile(r"\b(?:for\s+)?(?P<v>\d{1,4})\s+(?:training\s+)?epochs\b", re.I)),
    (
        "train_steps",
        re.compile(
            r"\btrain(?:ed|ing)?\s+for\s+(?P<v>\d{1,3}(?:,?\d{3}){0,3}(?:\.\d+)?\s*[KkM]?)\s+(?:training\s+)?steps\b",
            re.I,
        ),
    ),
    ("weight_decay", re.compile(rf"\bweight\s+decay\s*(?:of|=|:)?\s*{_NUMTXT}", re.I)),
)


def extract_training_hyperparameters(text, pages=None):
    """Training hyperparameters (optimizer, learning rate and schedule, warmup, Adam betas/epsilon, dropout,
    label smoothing, batch size, epochs/steps, weight decay) -> ({name: value as written}, evidence list)."""
    params, evidence = {}, []
    for key, rx in _TRAIN_HP:
        for m in rx.finditer(text or ""):
            raw = m.group("v") or (m.groupdict().get("v2") or "")
            value = re.sub(r"\s+", "", raw).replace("−", "-")
            if not value:
                continue
            if key not in params:
                params[key] = value
            evidence.append(
                {
                    "parameter": key,
                    "value": value,
                    "quote": _untrusted.neutralize(re.sub(r"\s+", " ", m.group(0)).strip()),
                    "page": pdf_parser.page_at(pages, m.start()) if pages else None,
                    "kind": "training",
                }
            )
            break  # the first mention is enough to show the paper reports it
    return params, evidence


def extract_hyperparameters(text, pages=None):
    """Decoding hyperparameters → ({name: value | [values...]}, evidence list)."""
    hits = []
    for key, rx in _HP_PATTERNS:
        for m in rx.finditer(text):
            value = _num(m.group("v"), key)
            if key == "temperature" and m.group(0).lstrip().startswith("T") and (value is None or value > 2):
                continue
            if value is not None:
                hits.append((m.start(), key, value, m.group(0)))
    for key, rx in _HP_REVERSED:
        for m in rx.finditer(text):
            raw = m.group("v") or (m.groupdict().get("v2"))
            value = _num(raw, key) if raw else None
            if value is not None and not any(k == key and abs(s - m.start()) < 40 for s, k, _, _ in hits):
                hits.append((m.start(), key, value, m.group(0)))
    hits.sort()
    params, evidence = {}, []
    for start, key, value, quote in hits:
        current = params.get(key)
        if current is None:
            params[key] = value
        elif isinstance(current, list):
            if value not in current:
                current.append(value)
        elif current != value:
            params[key] = [current, value]
        evidence.append(
            {
                "parameter": key,
                "value": value,
                "quote": re.sub(r"\s+", " ", quote).strip(),
                "page": pdf_parser.page_at(pages, start) if pages else None,
                "kind": "decoding",
            }
        )
    strategies = [name for name, rx in _DECODING if re.search(rx, text, re.I)]
    if strategies:
        params["decoding_strategy"] = strategies[0] if len(strategies) == 1 else strategies
    return params, evidence


def extract_json_schemas(text):
    """JSON objects shown in the paper (```json fences and balanced `{ "key": ... }` blocks), verbatim."""
    import json

    out, spans = [], []
    for m in _FENCE.finditer(text):
        body = m.group("body").strip()
        if m.group("lang").lower() == "json" or (body.startswith(("{", "[")) and re.search(r"\"[\w ]+\"\s*:", body)):
            out.append(body)
            spans.append((m.start(), m.end()))
    for m in re.finditer(r"\{", text):
        if any(a <= m.start() < b for a, b in spans):
            continue
        got = pdf_parser.balanced(text, m.start())
        if not got:
            continue
        inner, end = got
        block = "{" + inner + "}"
        if not re.match(r"\{\s*\"[^\"\n]{1,60}\"\s*:", block) or len(block) > MAX_PROMPT_CHARS:
            continue
        try:
            json.loads(block)
        except ValueError:
            if not re.search(r"\"[^\"\n]+\"\s*:\s*(?:\"|\[|\{|\d|true|false|null|<|\.\.\.)", block):
                continue
        out.append(block)
        spans.append((m.start(), end))
    seen, unique = set(), []
    for block in out:
        key = re.sub(r"\s+", "", block)
        if key not in seen:
            seen.add(key)
            unique.append(block)
    return unique


def extract_prompts_and_decoding(paper_id):
    """Prompt templates (placeholders kept verbatim), decoding hyperparameters and JSON output schemas."""
    corpus = pdf_parser.get_paper_corpus(_common._paper_id(paper_id))
    text, pages = corpus["text"], corpus["pages"]
    from noul.schemas.technical import PromptExtractionResult

    prompts, variables = [], []
    for i, p in enumerate(_find_prompts(text), 1):
        names = prompt_variables(p["text"])
        variables += [n for n in names if n not in variables]
        prompts.append(
            {
                "index": i,
                "type": p["type"],
                "label": p["label"],
                "text": p["text"],
                "variables": names,
                "page": pdf_parser.page_at(pages, p["start"]),
                "section": pdf_parser.section_at(corpus["sections"], p["start"]),
            }
        )
    params, evidence = extract_hyperparameters(text, pages)
    training, training_evidence = extract_training_hyperparameters(text, pages)
    schemas = extract_json_schemas(text)
    result = PromptExtractionResult(
        paper_id=corpus["paper_id"],
        prompts=prompts,
        hyperparameters=params,
        training_hyperparameters=training,
        json_schemas=schemas,
    ).model_dump()
    warnings = list(corpus["warnings"])
    if not prompts:
        warnings.append("No prompt templates detected.")
    if not params:
        warnings.append("No decoding hyperparameters (temperature, top_p, top_k, max_tokens, ...) reported.")
    result.update(
        {
            "title": corpus["title"],
            "source": corpus["source"],
            "variables": variables,
            "hyperparameter_evidence": evidence + training_evidence,
            "warnings": warnings,
        }
    )
    return result
