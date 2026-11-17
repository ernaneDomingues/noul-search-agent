"""Equations and algorithms: LaTeX and plain-text maths turned into typed Python stubs (`extract_paper_math`)."""

import re
from typing import Any

from noul import errors as _errors
from noul.providers import pdf_parser
from noul.tools.technical import common as _common

MATH_MODES = ("latex", "python", "both")


MAX_MATH_ITEMS = 40


_GREEK = (
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi rho "
    "sigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Phi Psi Omega"
).split()


_UNICODE = {
    "α": "\\alpha ",
    "β": "\\beta ",
    "γ": "\\gamma ",
    "δ": "\\delta ",
    "ε": "\\epsilon ",
    "η": "\\eta ",
    "θ": "\\theta ",
    "λ": "\\lambda ",
    "μ": "\\mu ",
    "π": "\\pi ",
    "ρ": "\\rho ",
    "σ": "\\sigma ",
    "τ": "\\tau ",
    "φ": "\\phi ",
    "ω": "\\omega ",
    "Σ": "\\Sigma ",
    "Δ": "\\Delta ",
    "·": "*",
    "×": "*",
    "−": "-",
    "√": "\\sqrt ",
    "ᵀ": "^T",
    "²": "^2",
    "³": "^3",
    "⊤": "\\top",
    "←": "=",
}


_MATH_FUNCS = {
    "exp": "math.exp",
    "log": "math.log",
    "ln": "math.log",
    "sin": "math.sin",
    "cos": "math.cos",
    "tan": "math.tan",
    "tanh": "math.tanh",
    "sqrt": "math.sqrt",
    "max": "max",
    "min": "min",
    "abs": "abs",
    "sigmoid": None,
    "softmax": None,
}


_CALLABLE_LETTERS = set("fghFGH")


class _MathError(ValueError):
    pass


def _py_name(name):
    import keyword

    name = re.sub(r"\W+", "_", name).strip("_") or "x"
    if name[0].isdigit():
        name = "v_" + name
    if keyword.iskeyword(name) or name in ("math", "Any", "Callable", "Sequence"):
        name += "_"
    return "lam" if name == "lambda_" else name


def _prep_latex(expr):
    s = expr
    for k, v in _UNICODE.items():
        s = s.replace(k, v)
    s = re.split(r"\\\\", s)[0]  # first line of multi-line (align) environments
    s = re.sub(r"\\(?:label|tag)\{[^{}]*\}|\\(?:nonumber|notag|displaystyle|limits)\b", " ", s)
    s = re.sub(r"\\(?:left|right|big|Big|bigg|Bigg)(?![A-Za-z])", "", s)
    s = re.sub(r"\\[,;:! ]|\\q?quad\b|~|&", " ", s)
    s = re.sub(r"\\(?:cdot|times|ast)\b", "*", s)
    s = re.sub(r"\\div\b", "/", s)
    s = re.sub(r"\^\s*\{?\s*(?:\\top|T|\\intercal)\s*\}?", "^⊺", s)
    s = re.sub(r"\\(?:mathcal|mathbf|boldsymbol|bm|mathbb|mathit|mathsf)\s*\{([^{}]*)\}", r"\1", s)
    s = re.sub(
        r"\\(?:mathrm|text|textrm|operatorname|textit)\s*\{([^{}]*)\}", lambda m: "⟨" + m.group(1).strip() + "⟩", s
    )
    s = re.sub(r"\\(hat|bar|tilde|dot|vec)\s*\{([^{}]*)\}", lambda m: f"⟨{m.group(2)}_{m.group(1)}⟩", s)
    s = re.sub(r"\\(hat|bar|tilde|dot|vec)\s+([A-Za-z])", lambda m: f"⟨{m.group(2)}_{m.group(1)}⟩", s)
    return s.strip().rstrip(".,;")


_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|(\\[A-Za-z]+)|(⟨[^⟩]+⟩)|([A-Za-z][A-Za-z0-9]*)|(\^⊺)|(.))")


def _tokenize(s, word_mode):
    out = []
    for m in _TOKEN.finditer(s):
        num, cmd, ph, word, tr, ch = m.groups()
        if num:
            out.append(("num", num))
        elif cmd:
            out.append(("cmd", cmd[1:]))
        elif ph:
            out.append(("id", ph[1:-1].replace(" ", "_")))
        elif word:
            if word_mode or len(word) == 1:
                out.append(("id", word))
            else:
                out.extend(("id", c) for c in word)
        elif tr:
            out.append(("op", "T"))
        elif ch and not ch.isspace():
            if ch not in "+-*/^_=(){}[],'|":
                raise _MathError(f"unsupported symbol {ch!r}")
            out.append(("op", ch))
    return out


class _MathParser:
    """Recursive-descent LaTeX/plain-text expression -> Python expression (implicit multiplication aware)."""

    def __init__(self, tokens, word_mode):
        self.t, self.i, self.word_mode = tokens, 0, word_mode
        self.vars, self.seqs, self.calls, self.bound = [], [], [], set()
        self.transpose = False
        self.matrices = set()

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        tok = self.peek()
        if tok[0] is None or (kind and tok[0] != kind) or (val is not None and tok[1] != val):
            raise _MathError(f"unexpected token {tok}")
        self.i += 1
        return tok

    def accept(self, val):
        if self.peek() == ("op", val):
            self.i += 1
            return True
        return False

    def _note(self, bucket, name):
        if name not in bucket and name not in self.bound:
            bucket.append(name)

    def parse(self):
        out = self.expr()
        if self.peek()[0] is not None:
            raise _MathError(f"trailing token {self.peek()}")
        return out

    def expr(self):
        out = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            out = f"{out} {op} {self.term()}"
        return out

    def _starts_factor(self):
        kind, val = self.peek()
        return (
            kind in ("num", "id")
            or (kind == "cmd" and val not in ("le", "leq", "ge", "geq", "to"))
            or (kind == "op" and val in "({[")
        )

    def term(self):
        out = self.unary()
        while True:
            if self.peek() in (("op", "*"), ("op", "/")):
                op = self.take()[1]
                out = f"{out} {op} {self.unary()}"
            elif self._starts_factor():
                out = f"{out} * {self.unary()}"
            else:
                return out

    def unary(self):
        if self.peek() in (("op", "-"), ("op", "+")):
            op = self.take()[1]
            return f"{op}{self.unary()}"
        return self.power()

    def power(self):
        base = self.atom()
        while True:
            if self.peek() == ("op", "T"):
                self.take()
                self.transpose = True
                if re.fullmatch(r"\w+", base):
                    self.matrices.add(base)
                base = f"{base}.T"
            elif self.accept("^"):
                base = f"{base} ** {self.group_or_atom()}"
            elif self.accept("'"):
                base = base + "_prime"
            else:
                return base

    def group_or_atom(self):
        if self.peek() == ("op", "{"):
            return f"({self.braced()})"
        if self.peek()[0] == "num" and len(self.peek()[1]) > 1 and not self.word_mode:
            num = self.take()[1]
            return num[0] if "." not in num else num
        return self.atom()

    def braced(self):
        self.take("op", "{")
        out = self.expr() if self.peek() != ("op", "}") else "1"
        self.take("op", "}")
        return out

    def subscript(self):
        """`_{total}` / `_k` / `_{i,j}` -> python-safe suffix."""
        names = {"+": "plus", "-": "minus", ",": "_", "*": "x", "=": "eq"}
        if self.accept("{"):
            out, depth = "", 1
            while True:
                kind, val = self.take()
                if (kind, val) == ("op", "{"):
                    depth += 1
                    continue
                if (kind, val) == ("op", "}"):
                    depth -= 1
                    if not depth:
                        return out.strip("_") or "0"
                    continue
                if kind == "op":
                    out += f"_{names[val]}_" if val in names else "_"
                elif kind == "cmd":
                    out += f"_{val}_"
                else:
                    out += val
        kind, val = self.take()
        return val

    def call_args(self):
        self.take("op", "(")
        args = [] if self.peek() == ("op", ")") else [self.expr()]
        while self.accept(","):
            args.append(self.expr())
        self.take("op", ")")
        return ", ".join(args)

    def identifier(self, name):
        sub = None
        if self.accept("_"):
            sub = self.subscript()
        if sub is not None and sub in self.bound:
            py = _py_name(name)
            self._note(self.seqs, py)
            return f"{py}[{sub}]"
        py = _py_name(f"{name}_{sub}" if sub is not None else name)
        is_call = self.peek() == ("op", "(") and (len(name) > 1 or name in _CALLABLE_LETTERS) and sub is None
        if is_call and name.lower() in _MATH_FUNCS and _MATH_FUNCS[name.lower()]:
            return f"{_MATH_FUNCS[name.lower()]}({self.call_args()})"
        if is_call:
            self._note(self.calls, py)
            return f"{py}({self.call_args()})"
        if py in self.bound:
            return py
        self._note(self.vars, py)
        return py

    def big_operator(self, fn):
        var, lo, hi = None, None, None
        if self.accept("_"):
            if self.accept("{"):
                var = _py_name(self.take("id")[1])
                if self.accept("="):
                    lo = self.expr()
                self.take("op", "}")
            else:
                var = _py_name(self.take("id")[1])
        if self.accept("^"):
            hi = self.group_or_atom()
        var = var or "i"
        self.bound.add(var)
        start = len(self.seqs)
        summand = self.unary_chain()
        self.bound.discard(var)
        if hi is not None:
            rng = f"range({lo if lo is not None else 0}, {hi} + 1)"
        elif len(self.seqs) > start:
            rng = f"range(len({self.seqs[start]}))"
        else:
            raise _MathError("unbounded big operator")
        return f"{fn}({summand} for {var} in {rng})"

    def unary_chain(self):
        out = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")) or self._starts_factor():
            op = self.take()[1] if self.peek() in (("op", "*"), ("op", "/")) else "*"
            out = f"{out} {op} {self.unary()}"
        return f"({out})"

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return val
        if kind == "id":
            if val == "e" and self.peek() == ("op", "^"):
                self.take()
                return f"math.exp{self.group_or_atom() if self.peek() == ('op', '{') else '(' + self.atom() + ')'}"
            return self.identifier(val)
        if kind == "op" and val in "([":
            close = ")" if val == "(" else "]"
            inner = self.expr()
            self.take("op", close)
            return f"({inner})"
        if kind == "op" and val == "{":
            self.i -= 1
            return f"({self.braced()})"
        if kind == "op" and val == "|":
            inner = self.expr()
            self.take("op", "|")
            return f"abs({inner})"
        if kind == "cmd":
            if val == "frac" or val in ("dfrac", "tfrac"):
                num = self.braced()
                return f"(({num}) / ({self.braced()}))"
            if val == "sqrt":
                if self.accept("["):
                    n = self.expr()
                    self.take("op", "]")
                    return f"(({self.braced()}) ** (1 / ({n})))"
                return f"math.sqrt({self.braced() if self.peek() == ('op', '{') else self.atom()})"
            if val in ("sum", "prod"):
                return self.big_operator("sum" if val == "sum" else "math.prod")
            if val == "pi":
                return "math.pi"
            if val in ("infty",):
                return "math.inf"
            if val in _MATH_FUNCS and _MATH_FUNCS[val]:
                if self.peek() == ("op", "("):
                    return f"{_MATH_FUNCS[val]}({self.call_args()})"
                if self.peek() == ("op", "{"):
                    return f"{_MATH_FUNCS[val]}({self.braced()})"
                return f"{_MATH_FUNCS[val]}({self.power()})"
            if val in _GREEK:
                return self.identifier(val)
            if val == "nabla" and self.peek()[0] in ("id", "cmd"):
                return self.identifier("grad_" + self.take()[1])
            if val == "partial" and self.peek()[0] in ("id", "cmd"):
                return self.identifier("d_" + self.take()[1])
        raise _MathError(f"unsupported token {kind}:{val}")


def _split_equation(expr):
    """Top-level `lhs = rhs` split (first `=` outside braces/parentheses)."""
    depth = 0
    for i, ch in enumerate(expr):
        if ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
        elif ch == "=" and depth == 0 and expr[i - 1 : i] not in ("<", ">", "!", ":") and expr[i + 1 : i + 2] != "=":
            lhs = expr[:i].rstrip(":").strip()
            rhs = expr[i + 1 :].strip()
            if "=" in rhs:  # chained (align) equations: keep the first right-hand side
                rhs = _split_equation(rhs)[0] or rhs
            return lhs, rhs
    return None, expr.strip()


def _docstring(title, body):
    title, body = (x.replace('"""', '\\"\\"\\"').rstrip() for x in (title, body))
    body = "\n".join(f"    {ln.strip()}".rstrip() for ln in body.splitlines() if ln.strip())
    return f'    r"""{title}\n\n{body} \n    """\n'


def _signature(vars_, seqs, calls, matrices=()):
    params = [f"{v}: {'Any' if v in matrices else 'float'}" for v in vars_ if v not in seqs and v not in calls]
    params += [f"{s}: Sequence[float]" for s in seqs]
    params += [f"{c}: Callable[..., Any]" for c in calls]
    return ", ".join(params)


def latex_to_python(expr, func_name, label="", word_mode=False):
    """Translate an equation into a typed Python function. Returns (code, variables, translated_ok)."""
    lhs, rhs = _split_equation(_prep_latex(expr))
    header = "import math\nfrom typing import Any, Callable, Sequence\n\n\n"
    original = re.sub(r"\\(?:label|tag)\{[^{}]*\}|\\(?:nonumber|notag)\b", "", expr).strip()
    doc = _docstring(f"{label or func_name}: LaTeX formula", original)
    try:
        parser = _MathParser(_tokenize(rhs, word_mode), word_mode)
        body = parser.parse()
        lhs_name = re.sub(r"\s+", " ", lhs.replace("⟨", "").replace("⟩", "")).strip() if lhs else None
        variables = [v for v in parser.vars if v not in parser.calls]
        if parser.transpose:  # matrix expression: operands multiplied with a transposed matrix are matrices too
            parser.matrices |= {
                v for v in parser.vars if re.search(rf"\b{re.escape(v)}\b\s*\*|\*\s*\b{re.escape(v)}\b", body)
            }
        ret = "Any" if parser.transpose else "float"
        code = (
            f"{header}def {func_name}({_signature(variables, parser.seqs, parser.calls, parser.matrices)}) -> {ret}:\n{doc}"
            + (f"    # {lhs_name} = ...\n" if lhs_name else "")
            + f"    return {body}\n"
        )
        import ast

        ast.parse(code)
        return code, variables + [s for s in parser.seqs if s not in variables] + parser.calls, True
    except (_MathError, SyntaxError, IndexError, ValueError):
        code = (
            f"{header}def {func_name}(*args: Any, **kwargs: Any) -> float:\n{doc}"
            f'    raise NotImplementedError("Automatic translation unsupported; see the LaTeX in the docstring.")\n'
        )
        return code, [], False


_ALGO_INPUT = re.compile(r"^\s*(?:\d+[:.]\s*)?\\?(?:Input|Require|Inputs|KwIn)\b\s*:?\s*(.*)$", re.I | re.M)


def algorithm_to_python(body, func_name, label):
    """Typed skeleton for pseudocode: inputs become parameters, every step a comment (always ast-valid)."""
    import ast

    inputs = []
    for m in _ALGO_INPUT.finditer(body):
        for part in re.split(r",|;|\band\b", m.group(1)):
            words = re.findall(
                r"\\?[A-Za-z][A-Za-z0-9_]*|[α-ωΑ-Ω]", _prep_latex(part).replace("⟨", "").replace("⟩", "")
            )
            if words:
                name = _py_name(words[-1].lstrip("\\"))
                if name not in inputs:
                    inputs.append(name)
    steps = [re.sub(r"\s+", " ", ln).strip() for ln in body.splitlines()]
    steps = [s for s in steps if s and not re.match(r"\\(?:begin|end)\{", s)]
    code = (
        "import math\nfrom typing import Any, Callable, Sequence\n\n\n"
        f"def {func_name}({', '.join(f'{x}: Any' for x in inputs)}) -> Any:\n" + _docstring(label, body)
    )
    code += "".join(f"    # {s}\n" for s in steps)
    code += '    raise NotImplementedError("Pseudocode transcribed step by step above; implement each step.")\n'
    ast.parse(code)
    return code, inputs


_ENV = re.compile(
    r"\\begin\{(equation|align|gather|multline|eqnarray|algorithm|algorithmic)(\*?)\}(.*?)"
    r"\\end\{\1\2\}",
    re.S,
)


_DISPLAY = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.S)


_INLINE = re.compile(r"(?<![\\$])\$([^$\n]{3,200})\$(?!\$)")


_PLAIN_EQ = re.compile(
    r"^[ \t]*(?:\(\d+\)[ \t]*)?(?P<eq>(?P<lhs>[A-Za-zα-ωΑ-Ω][\w]*(?:\([^()\n=]{0,60}\))?)[ \t]*=[ \t]*"
    r"(?P<rhs>[^=\n]{3,240}?))[ \t]*(?:\(\d+\))?[ \t]*$",
    re.M,
)


_PLAIN_ALGO = re.compile(r"^[ \t]*(Algorithm[ \t]+\d+)[ \t]*[:.]?[ \t]*([^\n]*)\n((?:[ \t]*\S[^\n]*\n?){1,40})", re.M)


def _has_operator(rhs):
    return bool(re.search(r"[+*/^·×√−-]|\\(?:frac|sqrt|sum|prod|exp|log|cdot)|[A-Za-z]\s*\(", rhs))


def parse_math(text):
    """Equations (LaTeX environments, $$..$$, \\[..\\], inline $a = b$, plain-text) and algorithms, in order."""
    spans: list[Any]
    found, spans = [], []

    def free(s, e):
        return not any(a < e and s < b for a, b in spans)

    for m in _ENV.finditer(text):
        env, body = m.group(1), m.group(3)
        spans.append((m.start(), m.end()))
        if env in ("algorithm", "algorithmic"):
            cap = re.search(r"\\caption\{([^{}]*)\}", body)
            found.append((m.start(), "algorithm", cap.group(1) if cap else "", body, False))
        else:
            lab = re.search(r"\\label\{([^{}]*)\}", body)
            found.append((m.start(), "equation", lab.group(1) if lab else "", body.strip(), False))
    for m in _DISPLAY.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end()))
            found.append((m.start(), "equation", "", (m.group(1) or m.group(2)).strip(), False))
    for m in _INLINE.finditer(text):
        body = m.group(1)
        lhs, rhs = _split_equation(body)
        if free(m.start(), m.end()) and lhs and _has_operator(rhs):
            spans.append((m.start(), m.end()))
            found.append((m.start(), "equation", "", body.strip(), False))
    for m in _PLAIN_ALGO.finditer(text):
        if free(m.start(), m.end()):
            spans.append((m.start(), m.end()))
            found.append(
                (m.start(), "algorithm", f"{m.group(1)}: {m.group(2).strip()}".strip(": "), m.group(3).rstrip(), True)
            )
    for m in _PLAIN_EQ.finditer(text):
        if not free(m.start(), m.end()) or "|" in m.group(0) or "$" in m.group(0) or "\\" in m.group(0):
            continue
        lhs, rhs = m.group("lhs"), m.group("rhs").strip().rstrip(".,;")
        if not _has_operator(rhs) or len(rhs.split()) > 25:
            continue
        try:
            _MathParser(_tokenize(_prep_latex(rhs), True), True).parse()
        except _MathError:
            continue
        spans.append((m.start(), m.end()))
        found.append((m.start(), "equation", "", f"{lhs} = {rhs}", True))
    return sorted(found, key=lambda x: x[0])


def _latex_clean(body, word_mode):
    s = re.sub(r"\\(?:label|tag)\{[^{}]*\}|\\(?:nonumber|notag)\b", "", body)
    s = re.sub(r"[ \t]*\n[ \t]*", " ", s).strip()
    if word_mode:
        s = _prep_latex(s).replace("⟨", "\\mathrm{").replace("⟩", "}").replace("^⊺", "^{\\top}")
    return f"$$ {s} $$"


def extract_paper_math(paper_id, mode="latex"):
    """Equations and pseudocode as clean LaTeX and/or typed, ast-validated Python functions."""
    import ast

    mode = (mode or "latex").strip().lower() if isinstance(mode, str) else mode
    if mode not in MATH_MODES:
        raise _errors.HTTPError(400, f"mode must be one of {', '.join(MATH_MODES)} (got {mode!r})")
    corpus = pdf_parser.get_paper_corpus(_common._paper_id(paper_id))
    from noul.schemas.technical import MathItem

    items: list[Any]
    items, eq_n, algo_n, seen, untranslated = [], 0, 0, set(), 0
    for _, kind, label, body, plain in parse_math(corpus["text"]):
        key = re.sub(r"\s+", "", body)
        if key in seen:
            continue
        seen.add(key)
        if kind == "equation":
            eq_n += 1
            name, label = f"equation_{eq_n}", label or f"Equation {eq_n}"
            code, variables, ok = latex_to_python(body, name, label, word_mode=plain)
            untranslated += not ok
            latex = _latex_clean(body, plain)
        else:
            algo_n += 1
            name, label = f"algorithm_{algo_n}", label or f"Algorithm {algo_n}"
            code, variables = algorithm_to_python(body, name, label)
            latex = body.strip() if not plain else "\\begin{algorithmic}\n" + body.strip() + "\n\\end{algorithmic}"
        ast.parse(code)
        items.append(
            MathItem(
                index=len(items) + 1,
                kind=kind,
                label=label,
                latex=latex if mode in ("latex", "both") else "",
                python_code=code if mode in ("python", "both") else "",
                variables=variables,
            ).model_dump()
        )
        if len(items) >= MAX_MATH_ITEMS:
            break
    warnings = list(corpus["warnings"])
    if not items:
        warnings.append("No equations or algorithms detected.")
    if untranslated and mode != "latex":
        warnings.append(
            f"{untranslated} equation(s) could not be translated automatically; their functions raise "
            "NotImplementedError and keep the LaTeX in the docstring."
        )
    return {
        "paper_id": corpus["paper_id"],
        "title": corpus["title"],
        "source": corpus["source"],
        "mode": mode,
        "equation_count": eq_n,
        "algorithm_count": algo_n,
        "items": items,
        "warnings": warnings,
    }
