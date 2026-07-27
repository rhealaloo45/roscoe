"""Phase 1 — expression + template evaluation for declarative workflows.

The sandbox tests matter most: workflow YAML can arrive from a shared repo or a visual
editor, so anything outside the allowlist must be rejected rather than executed.
"""

import pytest

from roscoe.workflow.expressions import ExpressionError, evaluate, render, truthy

STATE = {
    "input": {"employee_id": "E-1042", "count": 3},
    "employee": {"name": "Rhea", "department": "Engineering", "tags": ["vpn", "admin"]},
    "score": 7,
    "empty": None,
}


# --- reading state ---


def test_dotted_access_reads_dict_keys():
    assert evaluate("employee.name", STATE) == "Rhea"
    assert evaluate("input.employee_id", STATE) == "E-1042"


def test_indexing_dicts_lists_and_strings():
    assert evaluate("employee['department']", STATE) == "Engineering"
    assert evaluate("employee.tags[0]", STATE) == "vpn"
    assert evaluate("employee.name[0]", STATE) == "R"


def test_unknown_name_lists_what_is_available():
    with pytest.raises(ExpressionError, match="Unknown name 'nope'"):
        evaluate("nope", STATE)


def test_missing_key_names_the_key():
    with pytest.raises(ExpressionError, match="Key 'salary' not found"):
        evaluate("employee.salary", STATE)


def test_get_helper_tolerates_missing_keys():
    assert evaluate("get(employee, 'salary', 0)", STATE) == 0


# --- operators ---


def test_comparisons_and_membership():
    assert evaluate("score > 5", STATE) is True
    assert evaluate("employee.department in ['Engineering', 'Product']", STATE) is True
    assert evaluate("employee.department not in ['HR']", STATE) is True
    assert evaluate("'vpn' in employee.tags", STATE) is True


def test_boolean_and_arithmetic_operators():
    assert evaluate("score > 5 and employee.name == 'Rhea'", STATE) is True
    assert evaluate("score < 5 or score == 7", STATE) is True
    assert evaluate("not score > 100", STATE) is True
    assert evaluate("score * 2 + 1", STATE) == 15


def test_chained_comparison():
    assert evaluate("1 < score < 10", STATE) is True
    assert evaluate("1 < score < 5", STATE) is False


def test_conditional_expression_and_safe_functions():
    assert evaluate("'big' if score > 5 else 'small'", STATE) == "big"
    assert evaluate("len(employee.tags)", STATE) == 2
    assert evaluate("upper(employee.name)", STATE) == "RHEA"


def test_yaml_style_constants():
    assert evaluate("true", STATE) is True
    assert evaluate("null", STATE) is None


def test_membership_on_none_is_false_not_an_error():
    assert evaluate("'x' in empty", STATE) is False


# --- sandbox ---


def test_dunder_attributes_are_rejected():
    with pytest.raises(ExpressionError, match="not allowed"):
        evaluate("employee.__class__", STATE)


def test_attribute_access_never_reaches_python_objects():
    # ``upper`` is a real str method, but dotted access is dict lookup only.
    with pytest.raises(ExpressionError, match="Cannot read"):
        evaluate("score.real", STATE)


def test_method_calls_are_rejected():
    with pytest.raises(ExpressionError, match="method calls are not"):
        evaluate("employee.name.upper()", STATE)


def test_unknown_functions_are_rejected():
    with pytest.raises(ExpressionError, match="Unknown function '__import__'"):
        evaluate("__import__('os')", STATE)


def test_lambdas_and_comprehensions_are_rejected():
    with pytest.raises(ExpressionError, match="Unsupported syntax"):
        evaluate("lambda: 1", STATE)
    with pytest.raises(ExpressionError, match="Unsupported syntax"):
        evaluate("[x for x in employee.tags]", STATE)


def test_power_operator_is_rejected_to_avoid_cheap_hangs():
    with pytest.raises(ExpressionError, match="Unsupported operator"):
        evaluate("9 ** 9 ** 9", STATE)


def test_walrus_and_syntax_errors_report_cleanly():
    with pytest.raises(ExpressionError, match="Could not parse"):
        evaluate("employee.name =", STATE)


def test_division_by_zero_is_an_expression_error():
    with pytest.raises(ExpressionError, match="Division by zero"):
        evaluate("score / 0", STATE)


# --- templates ---


def test_sole_placeholder_preserves_type():
    assert render("{{ employee }}", STATE) == STATE["employee"]
    assert render("{{ score }}", STATE) == 7
    assert isinstance(render("{{ employee.tags }}", STATE), list)


def test_mixed_text_renders_to_a_string():
    assert render("Hi {{ employee.name }}, score {{ score }}", STATE) == "Hi Rhea, score 7"


def test_render_walks_dicts_and_lists():
    template = {"id": "{{ input.employee_id }}", "tags": ["{{ score }}", "static"]}
    assert render(template, STATE) == {"id": "E-1042", "tags": [7, "static"]}


def test_render_passes_non_templates_through():
    assert render(42, STATE) == 42
    assert render("plain text", STATE) == "plain text"


def test_none_interpolates_as_empty_string():
    assert render("value:{{ empty }}", STATE) == "value:"


def test_truthy_accepts_bare_or_wrapped_expressions():
    assert truthy("score > 5", STATE) is True
    assert truthy("{{ score > 5 }}", STATE) is True
    assert truthy("score > 100", STATE) is False
