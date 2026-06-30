"""Guard: no benchmark leak in any LLM-facing surface.

The bench measures whether an LLM can RESOLVE a term to the right class and read its
composition. Any concrete DATA class, ground-truth VALUE, or axis word we put in the
system prompt / corrective re-prompts / skills / builder-derived comments hands it
the answer. bench/leak_check.py harvests the leak vocabulary from the live data and
scans every LLM-facing text; this test fails the suite if it finds anything.

Skipped only if the served futuram.ttl is absent (leak_check needs it to harvest the
class vocabulary).
"""
import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
LEAK = REPO / "bench" / "leak_check.py"
SERVED = REPO / "fuseki" / "futuram" / "data" / "query" / "futuram.ttl"


def _load():
    spec = importlib.util.spec_from_file_location("bench_leak_check", LEAK)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.mark.skipif(not SERVED.exists(), reason="served futuram.ttl not built")
def test_no_llm_facing_leaks():
    leaks = _load().find_leaks()
    assert not leaks, "benchmark leaks found:\n" + "\n".join(
        f"  {p}:{ln} [{k}] {h!r}" for p, ln, k, h in sorted(leaks))
