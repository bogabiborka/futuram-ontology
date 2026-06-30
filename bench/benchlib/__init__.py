"""Internals of the head-to-head bench (bench/run_bench.py). All LLM-FACING strings
MUST stay in prompts.py or agent.py (leak_check.py AST-scans only those two); the
pure modules here (units/cases/scoring/report) stay LLM-text-free.
"""
