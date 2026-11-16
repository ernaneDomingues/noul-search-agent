"""Technical deep-dive tools over one paper's text (tables, claims, maths, ablations, prompts, failure modes, arXiv versions)."""

from noul import registry as _registry
from noul.api import common as _common
from noul.schemas import tool_inputs as _inputs
from noul.schemas import tool_outputs as _out
from noul.tools import hardware_tools as _hardware_tools
from noul.tools.technical import ablation as _ablation
from noul.tools.technical import equations as _equations
from noul.tools.technical import failure_modes as _failure_modes
from noul.tools.technical import grounding as _grounding
from noul.tools.technical import prompts as _prompts
from noul.tools.technical import tables as _tables
from noul.tools.technical import version_diff as _version_diff


@_registry.tool(
    "extract_benchmark_tables",
    description=(
        "Benchmark table extractor: detects Markdown, LaTeX (tabular) and plain-text/PDF tables plus inline "
        "`Benchmark: value` pairs in the paper's open-access full text (abstract fallback), keeps the exact "
        "model-row x metric-column correspondence, normalizes metric names (gsm-8k -> GSM8K, human-eval -> "
        "HumanEval, acc -> Accuracy, ppl -> Perplexity, ...) and converts values like '84.5%' or '84.5 ± 0.3' "
        "to numbers. Returns `tables` and a cross-table `normalized_summary` (by_metric, best_by_metric)."
    ),
    input=_inputs.ExtractBenchmarkTablesInput,
    profiles=("review",),
)
def extract_benchmark_tables(paper_id):
    return _tables.extract_benchmark_tables(paper_id)


@_registry.tool(
    "estimate_hardware_specs",
    description=(
        "Hardware feasibility / inference footprint: detects the model size (7B, 1.5B, 350M, 8x7B MoE, '671B "
        "parameters with 37B activated', family defaults) and architecture (dense, moe, encoder_only, "
        "rnn_ssm), then computes FP16/INT8/INT4 weights, KV cache (2 x layers x kv_heads x head_dim x context "
        "x bytes) and recommended minimum VRAM (+15% overhead), extracts the authors' reported training "
        "hardware (e.g. '64 NVIDIA A100 80GB GPUs', 'TPU v4-1024') and classifies the operational tier "
        "(edge_consumer_gpu <= 16 GB, workstation <= 48 GB, datacenter_cluster) from the INT4 footprint. "
        "Override sizes with total_params_b / active_params_b."
    ),
    input=_inputs.EstimateHardwareSpecsInput,
    profiles=("review",),
)
def estimate_hardware_specs(paper_id, total_params_b=None, active_params_b=None, context_length=8192):
    return _hardware_tools.estimate_hardware_specs(
        paper_id, total_params_b=total_params_b, active_params_b=active_params_b, context_length=context_length
    )


@_registry.tool(
    "verify_paper_claims",
    description=(
        "Claim grounding with strict attribution: locates each claim (or, when `claims` is empty, the "
        "numeric/comparative sentences auto-extracted from the abstract/text) in the paper's literal text, "
        "indexed by page and table/section. Per claim: numbers found/missing in the closest passage, keyword "
        "overlap, claim words absent from the text, a location tag '[p. X, Table Y]' and the best-matching "
        "excerpts. It does not decide whether a claim is supported: read `best_match` and decide. "
        "Use it before stating numbers from a paper; judge each claim with the returned rubric."
    ),
    input=_inputs.VerifyPaperClaimsInput,
    profiles=("review",),
    output=_out.ClaimsGroundingReport,
)
def verify_paper_claims(paper_id, claims=None):
    return _common._with_integrity(_grounding.verify_paper_claims(paper_id, claims=claims), paper_id)


@_registry.tool(
    "extract_paper_math",
    description=(
        "Math & pseudocode extractor: finds equations (\\begin{equation|align}, $$..$$, \\[..\\], inline $a = "
        "b$, plain-text 'L_total = ...' / 'Attention(Q, K, V) = ...') and algorithms (\\begin{algorithm}, "
        "'Algorithm N:' blocks). mode='latex' returns clean delimited LaTeX; mode='python' returns typed "
        "Python functions (`def equation_1(...) -> float` / `def algorithm_1(...) -> Any`, `import math`, "
        "original LaTeX in the docstring), always validated with ast.parse; mode='both' returns both."
    ),
    input=_inputs.ExtractPaperMathInput,
    profiles=("review",),
)
def extract_paper_math(paper_id, mode="latex"):
    return _equations.extract_paper_math(paper_id, mode=mode)


@_registry.tool(
    "analyze_ablation_tables",
    description=(
        "Ablation analyzer: locates ablation tables/lists (rows 'Full model' / 'Ours' / 'Baseline' and "
        "variants 'w/o X', 'without X', '- X', '+ X', 'no X'), takes the full model (else baseline) as "
        "reference and computes, per component and metric, delta and delta_percent, flags is_marginal (|Δ%| < "
        "1%), ranks components by |Δ%| and emits warnings_and_alerts for marginal components and for a single "
        "component accounting for >= 60% of the total reported gain."
    ),
    input=_inputs.AnalyzeAblationTablesInput,
    profiles=("review",),
)
def analyze_ablation_tables(paper_id):
    return _ablation.analyze_ablation_tables(paper_id)


@_registry.tool(
    "extract_prompts_and_decoding",
    description=(
        "Prompt & inference protocol extractor: finds prompt templates (after 'System Prompt:', 'User "
        "Prompt:', 'Prompt Template:', 'Instruction:', in code fences/quotes, or text with {variable}, "
        '{{variable}}, <context>, "Let\'s think step by step", "You are an expert") keeping placeholders '
        "verbatim and listing `variables`; extracts decoding hyperparameters (temperature, top_p, top_k, "
        "max_tokens, repetition_penalty, beam_size, seed, decoding_strategy) with literal evidence, and the "
        "JSON output schemas shown in the paper."
    ),
    input=_inputs.ExtractPromptsAndDecodingInput,
    profiles=("review",),
)
def extract_prompts_and_decoding(paper_id):
    return _prompts.extract_prompts_and_decoding(paper_id)


@_registry.tool(
    "detect_failure_modes",
    description=(
        "Failure mode & critical assumption detector: scans Limitations, Error Analysis, Failure Cases, "
        "Discussion, Threats to Validity and Appendix (plus abstract/body) to classify failure modes into "
        "long_context, noise_sensitivity, out_of_distribution, latency_or_compute, sample_or_population_bias "
        "and general_limitation (as `signals` with the triggering sentence and a priority hint), extracts "
        "critical_assumptions, baseline_superiority_scenarios (from text and from benchmark tables where a "
        "baseline beats Ours) and operational_limits. Candidates, not confirmed failures: check each excerpt."
    ),
    input=_inputs.DetectFailureModesInput,
    profiles=("review",),
    output=_out.FailureModesReport,
)
def detect_failure_modes(paper_id, inspect_fulltext=True):
    return _common._with_integrity(
        _failure_modes.detect_failure_modes(paper_id, inspect_fulltext=bool(inspect_fulltext)), paper_id
    )


@_registry.tool(
    "diff_arxiv_versions",
    description=(
        "arXiv temporal version diff analyzer: fetches and compares two revisions of an arXiv preprint (e.g. "
        "v1 vs v2), tracking title and authorship changes, metric and table number deltas (`metric_deltas`), "
        "claim attenuations or strengthenings (`claim_shifts`), newly added baselines or datasets "
        "(`new_baselines_or_datasets`) and a structured `markdown_diff` report."
    ),
    input=_inputs.DiffArxivVersionsInput,
    profiles=("review",),
    output=_out.ArxivVersionDiffReport,
    title="Diff arXiv versions",
)
def diff_arxiv_versions(arxiv_id, from_version="v1", to_version="v2"):
    return _version_diff.diff_arxiv_versions(arxiv_id, from_version=from_version, to_version=to_version)
