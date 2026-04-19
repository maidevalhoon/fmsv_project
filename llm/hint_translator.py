"""
llm/hint_translator.py — Parse LLM JSON response into PySAT assumptions.

The LLM is expected to return a JSON object of the form:
    {
        "signal_assignments": {"<signal_name_or_net_id>": 0_or_1, ...},
        "sensitization_hint": "..."
    }

This module:
  1. Parses the JSON (robustly — handles extra prose, missing fields).
  2. Maps each signal name or net ID → SAT variable using good_map.
  3. Validates that only primary input nets are assigned (rejects internal
     nets that would contradict gate Tseitin clauses and cause false UNSAT).
  4. Returns a PySAT assumptions list: positive literal for value=1,
     negative literal for value=0.

If anything goes wrong, returns [] (empty assumptions — SAT solves unaided).
"""

import json
import re


def _extract_json(text: str) -> str:
    """Extract the first JSON object from a string."""
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)

    start = text.find("{")
    if start == -1:
        return ""

    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def _build_name_to_id(module_data: dict) -> dict:
    """Build signal_name → net_id mapping for user-visible wires.

    Accepts both formats from the LLM:
      "N1" → "2",  "N22" → "7",  "2" → "2" (passthrough)
    """
    mapping = {}
    for signal_name, net_info in module_data.get("netnames", {}).items():
        if net_info.get("hide_name", 1) != 0:
            continue
        for bit in net_info.get("bits", []):
            bit_str = str(bit)
            mapping[signal_name] = bit_str
            mapping[signal_name.lower()] = bit_str
            mapping[bit_str] = bit_str
    return mapping


def translate_hints(
    llm_response_str: str,
    good_map: dict,
    input_nets: list[str] | None = None,
    module_data: dict | None = None,
    verbose: bool = False,
) -> tuple[list[int], str]:
    """Parse the LLM response and produce PySAT assumption literals.

    Args:
        llm_response_str: Raw string returned by the LLM API call.
        good_map:         ``{net_id_str: sat_variable_int}`` from build_miter.
        input_nets:       List of primary input net ID strings. If provided,
                          only these nets are accepted (rejects internal nets).
        module_data:      Module dict — if provided, enables signal name → ID
                          resolution so the LLM can use "N1" instead of "2".
        verbose:          Print parsed assignments when True.

    Returns:
        ``(assumptions, hint_text)`` where:
        - ``assumptions`` is a list of signed integers for ``solver.solve(assumptions=...)``.
        - ``hint_text`` is the LLM's sensitization_hint string (or "").
        On any parse failure, returns ``([], "")``.
    """
    if not llm_response_str or not llm_response_str.strip():
        if verbose:
            print("[hint_translator] Empty LLM response — returning []")
        return [], ""

    json_str = _extract_json(llm_response_str)
    if not json_str:
        if verbose:
            print("[hint_translator] No JSON found in LLM response — returning []")
        return [], ""

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        if verbose:
            print(f"[hint_translator] JSON parse error: {exc} — returning []")
        return [], ""

    if not isinstance(data, dict):
        if verbose:
            print("[hint_translator] Parsed JSON is not a dict — returning []")
        return [], ""

    assignments = data.get("signal_assignments", {})
    hint_text = data.get("sensitization_hint", "")

    if not isinstance(assignments, dict):
        if verbose:
            print("[hint_translator] signal_assignments is not a dict — returning []")
        return [], str(hint_text)

    name_to_id = _build_name_to_id(module_data) if module_data else {}
    input_set = set(input_nets) if input_nets else None

    assumptions: list[int] = []
    skipped = []

    for key_raw, value_raw in assignments.items():
        key = str(key_raw).strip()

        net_id = name_to_id.get(key) or name_to_id.get(key.lower()) or key

        try:
            value = int(value_raw)
        except (TypeError, ValueError):
            if verbose:
                print(f"  [hint_translator] {key}: invalid value {value_raw!r} — skipped")
            skipped.append(key)
            continue

        if value not in (0, 1):
            if verbose:
                print(f"  [hint_translator] {key}: value {value} not in {{0,1}} — skipped")
            skipped.append(key)
            continue

        if net_id not in good_map:
            if verbose:
                print(f"  [hint_translator] {key} (net_id={net_id}): not in good_map — skipped")
            skipped.append(key)
            continue

        if input_set and net_id not in input_set:
            if verbose:
                print(f"  [hint_translator] {key} (net{net_id}): not a primary input — rejected")
            skipped.append(key)
            continue

        var = good_map[net_id]
        lit = var if value == 1 else -var
        assumptions.append(lit)

        if verbose:
            sign = "+" if value == 1 else "-"
            print(f"  [hint_translator] {key} → net{net_id} = {value}  → literal {sign}{var}")

    if verbose:
        if skipped:
            print(f"  [hint_translator] Skipped: {skipped}")
        print(f"  [hint_translator] Final assumptions ({len(assumptions)}): {assumptions}")
        if hint_text:
            print(f"  [hint_translator] Hint: {hint_text}")

    return assumptions, str(hint_text)
