"""The release workflow: jobs downstream of optional (skippable) jobs must not inherit the implicit success()."""

import re
import unittest

import _support

WORKFLOW = _support.ROOT / ".github/workflows/release.yml"
STATUS_FUNCTIONS = ("always()", "!cancelled()", "success()", "failure()", "cancelled()")


def _jobs(text):
    """{job: (needs, if)} from the workflow, parsed with regexes (no YAML dependency in the test suite)."""
    jobs = {}
    for match in re.finditer(r"^  ([a-z][a-z0-9-]*):\n((?:    .*\n|\n)+)", text, re.M):
        body = match.group(2)
        needs = re.search(r"^    needs: *(.+)$", body, re.M)
        cond = re.search(r"^    if: *(.+)$", body, re.M)
        jobs[match.group(1)] = (
            re.findall(r"[a-z][a-z0-9-]*", needs.group(1)) if needs else [],
            cond.group(1) if cond else "",
        )
    return jobs


class ReleaseWorkflowTests(unittest.TestCase):
    def test_jobs_after_skippable_jobs_use_a_status_function(self):
        jobs = _jobs(WORKFLOW.read_text(encoding="utf-8"))
        self.assertIn("publish", jobs)
        skippable = {name for name, (_, cond) in jobs.items() if "vars." in cond}  # e.g. signing only with secrets

        def downstream_of_skippable(name, seen=()):
            needs = jobs.get(name, ([], ""))[0]
            return any(n in skippable or (n not in seen and downstream_of_skippable(n, (*seen, n))) for n in needs)

        for name, (_, cond) in jobs.items():
            if cond and downstream_of_skippable(name):
                with self.subTest(job=name):
                    self.assertTrue(any(f in cond for f in STATUS_FUNCTIONS), f"{name}: {cond}")


if __name__ == "__main__":
    unittest.main()
