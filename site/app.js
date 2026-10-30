/* Interactive Terminal Simulator, 1-Click Clipboard Copy, MCP Config Tabs, Theme Toggle & Tool Filter */
(function () {
  const TERMINAL_SCENARIOS = {
    compare: {
      command: 'noul compare arxiv:2305.14314 arxiv:2405.11001 --pretty',
      output: [
        '# Comparative Matrix (2 papers)',
        '',
        '| Paper | Core Problem | Methodology | Key Metrics | Declared Limitations |',
        '| --- | --- | --- | --- | --- |',
        '| Dettmers et al. (2023) | 4-bit LLM Fine-Tuning | NormalFloat4 (NF4) + Double Quantization | 99.3% of FP16 on MMLU | Latency overhead at high batch size |',
        '| Rostova et al. (2024) | Sub-Linear KV Cache | Block-Sparse Key-Value Compression | -68% VRAM, 3.8x tok/s | Slight drop on exact needle recall |',
        '',
        '✓ Convergence: Both methods target single-GPU memory bottlenecks without full retraining.'
      ].join('\n')
    },
    review: {
      command: 'noul review arxiv:2405.11001 --domain cs --pretty',
      output: [
        '# Multi-Persona Scientific Peer Review — FlashKV (NeurIPS/ICLR Rubric)',
        'Verdict: WEAK_ACCEPT | Overall Score: 3.8 / 5.0',
        '',
        '• [Persona 1 — Theoretical & Mathematical Reviewer (Score: 4.0)]',
        '  Strengths: Sub-linear complexity bound O(N log N) is formally derived.',
        '  Flaw: Proposition 2 assumes uniform attention entropy [pág. 4, Seção 3].',
        '• [Persona 2 — Skeptical Empirical & Baselines Reviewer (Score: 3.5)]',
        '  Flaw: Missing direct latency comparison against FlashAttention-2 at 64k context.',
        '• [Persona 3 — Systems & Reproducibility Auditor (Score: 4.0)]',
        '  Strengths: Open-source CUDA kernels and 5-seed error bars reported.'
      ].join('\n')
    },
    scaffold: {
      command: 'noul scaffold arxiv:2405.11001 --target pytorch --pretty',
      output: [
        '# Code & Protocol Scaffold: FlashKV (Domain: computer_science | Target: pytorch)',
        'Syntax Validated (ast.parse): TRUE',
        '',
        'class FlashKVSparseAttention(nn.Module):',
        '    def __init__(self, d_model: int = 4096, num_heads: int = 32) -> None:',
        '        super().__init__()',
        '        self.head_dim = d_model // num_heads',
        '        self.qkv_proj = nn.Linear(d_model, 3 * d_model, bias=False)',
        '        # Source: Equation 2 (Block-Sparse KV Routing)',
        '',
        '✓ Generated 68 lines of PyTorch nn.Module + unittest shape assertions.'
      ].join('\n')
    },
    gatekeeper: {
      command: 'noul gatekeeper audit draft_neurips.tex --venue neurips --pretty',
      output: [
        '# Pre-Submission Gatekeeper Audit (draft_neurips.tex — Venue: NEURIPS)',
        'Readiness Score: 92/100 | Desk-Reject Risk: LOW',
        '',
        '• Stage 1 (Double-Blind Anonymity): ✅ Zero violations (anonymous.4open.science verified)',
        '• Stage 2 (NeurIPS Checklist):      ✅ 5/5 passed (Limitations, A100 GPU hours, 5 seeds, ± std, MIT License)',
        '• Stage 3 (Overclaiming Scanner):   ✅ Calibrated empirical hedging verified',
        '• Stage 4 (Mandatory Sections):     ✅ Abstract, Intro, Methods, Experiments, Limitations, Broader Impact'
      ].join('\n')
    }
  };

  let activeTimer = null;

  function renderScenario(key) {
    const scenario = TERMINAL_SCENARIOS[key] || TERMINAL_SCENARIOS.compare;
    const screen = document.getElementById('terminal-screen');
    if (!screen) return;
    if (activeTimer) clearInterval(activeTimer);

    const fullText = '$ ' + scenario.command + '\n\n' + scenario.output;
    let idx = 0;
    screen.textContent = '$ ';
    activeTimer = setInterval(function () {
      idx += 18;
      if (idx >= fullText.length) {
        screen.textContent = fullText;
        clearInterval(activeTimer);
        activeTimer = null;
      } else {
        screen.textContent = fullText.slice(0, idx) + '▋';
      }
    }, 16);
  }

  document.addEventListener('DOMContentLoaded', function () {
    // Theme toggle
    const root = document.documentElement;
    const toggleBtn = document.getElementById('theme-toggle');
    const savedTheme = localStorage.getItem('noul-theme') || 'dark';
    root.setAttribute('data-theme', savedTheme);

    if (toggleBtn) {
      toggleBtn.textContent = savedTheme === 'dark' ? '☀️ Modo Claro' : '🌙 Modo Escuro';
      toggleBtn.addEventListener('click', function () {
        const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        localStorage.setItem('noul-theme', next);
        toggleBtn.textContent = next === 'dark' ? '☀️ Modo Claro' : '🌙 Modo Escuro';
      });
    }

    // Terminal simulator tabs
    const termTabs = document.querySelectorAll('.term-tab');
    termTabs.forEach(function (btn) {
      btn.addEventListener('click', function () {
        termTabs.forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
        renderScenario(btn.getAttribute('data-scenario'));
      });
    });
    renderScenario('compare');

    // MCP Config tabs
    const cfgTabs = document.querySelectorAll('.cfg-tab');
    const cfgPanels = document.querySelectorAll('.cfg-panel');
    cfgTabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        const target = tab.getAttribute('data-target');
        cfgTabs.forEach(function (t) { t.classList.remove('active'); });
        cfgPanels.forEach(function (p) {
          p.classList.toggle('active', p.id === target);
        });
        tab.classList.add('active');
      });
    });

    // 1-Click Copy buttons
    document.querySelectorAll('.copy-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        const codeId = btn.getAttribute('data-copy-target');
        const el = document.getElementById(codeId);
        if (!el) return;
        const text = el.textContent.trim();
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text);
        }
        const orig = btn.textContent;
        btn.textContent = '✓ Copiado!';
        setTimeout(function () { btn.textContent = orig; }, 1600);
      });
    });

    // Interactive Tools Search Filter
    const searchInput = document.getElementById('tool-search');
    if (searchInput) {
      searchInput.addEventListener('input', function () {
        const q = searchInput.value.toLowerCase().trim();
        document.querySelectorAll('.tool-card').forEach(function (card) {
          const hay = (card.textContent || '').toLowerCase();
          card.style.display = !q || hay.includes(q) ? '' : 'none';
        });
      });
    }
  });
})();
