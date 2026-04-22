import sys
import unittest.mock

sys.modules["google.genai"] = unittest.mock.MagicMock()

import run_llm_atpg
from llm.query_builder import _describe_circuit_compact
from core.circuit_loader import load_circuit

m, d = load_circuit("benchmarks/json/c17_tech.json")
print("TECH:")
print(_describe_circuit_compact(d))

m, d = load_circuit("benchmarks/json/c17_notech.json")
print("NOTECH:")
print(_describe_circuit_compact(d))
