/* Interactive Terminal Simulator, 1-Click Clipboard Copy, MCP Config Tabs, Theme Toggle & Tool Filter */
(function () {
  // Illustrative output: it shows the shape of what each command returns, with placeholder values.
  const TERMINAL_SCENARIOS = {
    search: {
      command: 'noul search "creatine supplementation working memory" --types meta-analysis,rct --pretty',
      output: [
        '# 12 results (OpenAlex, PubMed, Semantic Scholar, Europe PMC), deduplicated',
        '',
        '1. <title of a meta-analysis> (2023)          meta-analysis · tier: high · 214 citations',
        '   doi:10.xxxx/xxxxx · open access · abstract available',
        '2. <title of a randomized trial> (2021)       rct · tier: standard · 57 citations',
        '   pmid:xxxxxxxx · abstract available',
        '3. <title of a retracted trial> (2019)        rct · RETRACTED (notice: doi:10.xxxx/xxxxx)',
        '',
        'Next: classify the stance of each study, then run `noul meter`.'
      ].join('\n')
    },
    meter: {
      command: 'noul meter \'[{"id":"doi:10.xxxx/a","stance":"supportive"},{"id":"pmid:xxxxxxxx","stance":"inconclusive"}]\' --pretty',
      output: [
        '# Evidence Meter (2 studies; stances assigned by your assistant)',
        '',
        '              raw     weighted by study design',
        'supportive    50%     59%',
        'inconclusive  50%     41%',
        'contrary       0%      0%',
        '',
        'Weights: meta-analysis 1.0 > systematic review > rct > cohort > preclinical 0.2',
        'A heuristic picture of the studies found, not a statistical meta-analysis.'
      ].join('\n')
    },
    review: {
      command: 'noul review arxiv:2305.14314 --pretty',
      output: [
        '# Review evidence (domain: computer_science · rubric: NeurIPS/ICLR)',
        'extraction_confidence: high (full text, 22 pages)',
        '',
        'signals',
        '  • baselines: comparison table found [p. 7, Section 5]',
        '  • seeds / error bars: not detected in the results tables',
        '  • limitations: section present [p. 9]',
        'excerpts: 14 passages with section and page',
        'rubric: 6 criteria with how_to_judge',
        '',
        'No verdict and no score: check each signal against its excerpt and make the call.'
      ].join('\n')
    },
    gatekeeper: {
      command: 'noul gatekeeper audit draft_neurips.tex --venue neurips --pretty',
      output: [
        '# Pre-submission check (draft_neurips.tex · venue: neurips)',
        '',
        'signals',
        '  • anonymity: author block detected [line 12]',
        '  • anonymity: identifying repository URL [line 240]',
        '  • overclaiming: "clearly proves" [line 88]',
        'checklist',
        '  • limitations section     detected: yes',
        '  • compute resources       detected: no',
        '  • error bars / seeds      detected: yes',
        '',
        'No readiness score: confirm each finding against the quoted text.'
      ].join('\n')
    }
  };

  let activeTimer = null;

  function renderScenario(key) {
    const scenario = TERMINAL_SCENARIOS[key] || TERMINAL_SCENARIOS.search;
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
      toggleBtn.textContent = savedTheme === 'dark' ? 'Light mode' : 'Dark mode';
      toggleBtn.addEventListener('click', function () {
        const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        root.setAttribute('data-theme', next);
        localStorage.setItem('noul-theme', next);
        toggleBtn.textContent = next === 'dark' ? 'Light mode' : 'Dark mode';
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
    renderScenario('search');

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
        btn.textContent = '✓ Copied!';
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
