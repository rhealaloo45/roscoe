"""Safe expression + template evaluation for declarative workflows.

Workflow YAML is written by people who are not necessarily writing Python, and it may
come from a shared repo or a visual editor. So expressions are **never** passed to
``eval()``. They are parsed with ``ast`` and walked against an allowlist; anything not
explicitly permitted raises :class:`ExpressionError` naming the construct.

Two sandbox rules do most of the work:

* **Dotted access is dict lookup, not ``getattr``.** ``employee.name`` compiles to
  ``employee["name"]``. Python attributes are never touched, so the usual escape via
  ``__class__`` / ``__globals__`` simply has nothing to walk.
* **Only bare-name calls to allowlisted functions.** ``x.foo()`` is rejected outright,
  so no method on a state value can be reached.

Templates (``"Hi {{ name }}"``) are the same grammar wrapped in ``{{ }}``. A string
that is *exactly* one placeholder returns the typed value — ``"{{ employee }}"`` stays
a dict rather than becoming its ``str()`` — which is what makes it usable for a node's
structured ``inputs:``.
"""

from __future__ import annotations

import ast
import re
from typing import Any

#: ``{{ ... }}`` placeholder, non-greedy so several can appear in one string.
_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

#: A string consisting of exactly one placeholder and nothing else.
_SOLE_PLACEHOLDER = re.compile(r"^\s*\{\{(.*?)\}\}\s*$", re.DOTALL)


class ExpressionError(ValueError):
    """Raised when an expression is malformed, unsafe, or references missing state."""


def _lower(value: Any) -> str:
    return str(value).lower()


def _upper(value: Any) -> str:
    return str(value).upper()


def _strip(value: Any) -> str:
    return str(value).strip()


def _get(obj: Any, key: Any, default: Any = None) -> Any:
    """Forgiving lookup — the one way to read a possibly-absent key without erroring."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    if isinstance(obj, (list, tuple)) and isinstance(key, int):
        return obj[key] if -len(obj) <= key < len(obj) else default
    return default


#: Functions callable from an expression. Bare names only — never methods.
_SAFE_FUNCTIONS: dict[str, Any] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
    "sorted": sorted,
    "sum": sum,
    "any": any,
    "all": all,
    "lower": _lower,
    "upper": _upper,
    "strip": _strip,
    "get": _get,
}

#: Literals that are not variable references.
_CONSTANTS: dict[str, Any] = {"true": True, "false": False, "null": None, "none": None}

_BOOL_OPS = {ast.And: all, ast.Or: any}


def evaluate(expression: str, state: dict[str, Any]) -> Any:
    """Evaluate ``expression`` against ``state`` and return the result.

    Raises:
        ExpressionError: if the expression cannot be parsed, uses a construct outside
            the allowlist, or references a name that is not in ``state``.
    """
    source = expression.strip()
    if not source:
        raise ExpressionError("Empty expression.")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Could not parse expression {source!r}: {exc.msg}") from exc
    return _eval_node(tree.body, state, source)


def render(template: Any, state: dict[str, Any]) -> Any:
    """Resolve ``{{ ... }}`` placeholders in ``template`` against ``state``.

    Strings that are a single placeholder keep the referenced value's type; strings
    with surrounding text render to a string. Dicts and lists are walked recursively,
    so a whole ``inputs:`` block can be passed in one call. Other values pass through.
    """
    if isinstance(template, str):
        sole = _SOLE_PLACEHOLDER.match(template)
        if sole:
            return evaluate(sole.group(1), state)
        return _PLACEHOLDER.sub(lambda m: _stringify(evaluate(m.group(1), state)), template)
    if isinstance(template, dict):
        return {key: render(value, state) for key, value in template.items()}
    if isinstance(template, list):
        return [render(item, state) for item in template]
    return template


#: AST node types the grammar permits. Used by :func:`check_syntax` to inspect an
#: expression without evaluating it — the same allowlist ``_eval_node`` enforces.
_ALLOWED_NODES: tuple[type, ...] = (
    ast.Expression, ast.Constant, ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.Name, ast.Load, ast.Attribute, ast.Subscript, ast.Compare, ast.BoolOp,
    ast.UnaryOp, ast.BinOp, ast.IfExp, ast.Call,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Is, ast.IsNot, ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
)


def check_syntax(expression: str) -> None:
    """Validate an expression's *shape* without evaluating it.

    Catches syntax errors and disallowed constructs, but not unknown names — those
    depend on runtime state. Used by ``roscoe validate`` to surface typos before a
    workflow ever runs.

    Raises:
        ExpressionError: if the expression cannot be parsed or uses a construct
            outside the allowlist.
    """
    source = expression.strip()
    if not source:
        raise ExpressionError("Empty expression.")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Could not parse expression {source!r}: {exc.msg}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"Unsupported syntax ({type(node).__name__}) in expression {source!r}."
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ExpressionError(
                f"Access to '{node.attr}' is not allowed in expression {source!r}."
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ExpressionError(
                    f"Only direct calls to built-in helpers are allowed in {source!r}; "
                    "method calls are not."
                )
            if node.func.id not in _SAFE_FUNCTIONS:
                allowed = ", ".join(sorted(_SAFE_FUNCTIONS))
                raise ExpressionError(
                    f"Unknown function '{node.func.id}' in {source!r}. Allowed: {allowed}"
                )


def iter_expressions(template: Any) -> list[str]:
    """Collect every ``{{ ... }}`` expression inside a template value.

    Walks dicts and lists, so a whole ``inputs:`` block can be checked in one call.
    """
    found: list[str] = []
    if isinstance(template, str):
        found.extend(match.group(1) for match in _PLACEHOLDER.finditer(template))
    elif isinstance(template, dict):
        for value in template.values():
            found.extend(iter_expressions(value))
    elif isinstance(template, list):
        for item in template:
            found.extend(iter_expressions(item))
    return found


def truthy(expression: str, state: dict[str, Any]) -> bool:
    """Evaluate ``expression`` and coerce the result to a bool.

    Tolerates a ``{{ ... }}`` wrapper so a ``when:`` can be written either way.
    """
    sole = _SOLE_PLACEHOLDER.match(expression)
    return bool(evaluate(sole.group(1) if sole else expression, state))


def _stringify(value: Any) -> str:
    """Render a value for interpolation into surrounding text."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _eval_node(node: ast.AST, state: dict[str, Any], source: str) -> Any:
    """Walk one AST node against the allowlist. Anything unlisted is rejected."""
    # --- literals ---
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_eval_node(item, state, source) for item in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(item, state, source) for item in node.elts)
    if isinstance(node, ast.Set):
        return {_eval_node(item, state, source) for item in node.elts}
    if isinstance(node, ast.Dict):
        return {
            _eval_node(k, state, source): _eval_node(v, state, source)
            for k, v in zip(node.keys, node.values)
            if k is not None
        }

    # --- names ---
    if isinstance(node, ast.Name):
        lowered = node.id.lower()
        if node.id not in state and lowered in _CONSTANTS:
            return _CONSTANTS[lowered]
        if node.id not in state:
            known = ", ".join(sorted(state)) or "(state is empty)"
            raise ExpressionError(
                f"Unknown name '{node.id}' in expression {source!r}. Available: {known}"
            )
        return state[node.id]

    # --- access: dotted lookup is dict access, never getattr ---
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("_"):
            raise ExpressionError(
                f"Access to '{node.attr}' is not allowed in expression {source!r}."
            )
        obj = _eval_node(node.value, state, source)
        return _lookup(obj, node.attr, source)

    if isinstance(node, ast.Subscript):
        obj = _eval_node(node.value, state, source)
        key = _eval_node(node.slice, state, source)
        return _lookup(obj, key, source)

    # --- operators ---
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, state, source)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, state, source)
            if not _compare(op, left, right, source):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        reducer = _BOOL_OPS.get(type(node.op))
        if reducer is None:
            raise ExpressionError(f"Unsupported boolean operator in {source!r}.")
        # Evaluated eagerly (no short-circuit): expressions are side-effect free by
        # construction, so the only cost is a few extra dict lookups.
        return reducer(bool(_eval_node(v, state, source)) for v in node.values)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, state, source)
        if isinstance(node.op, ast.Not):
            return not operand
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return +operand
        raise ExpressionError(f"Unsupported unary operator in {source!r}.")

    if isinstance(node, ast.BinOp):
        return _binop(node, state, source)

    if isinstance(node, ast.IfExp):
        if bool(_eval_node(node.test, state, source)):
            return _eval_node(node.body, state, source)
        return _eval_node(node.orelse, state, source)

    # --- calls: bare allowlisted names only ---
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ExpressionError(
                f"Only direct calls to built-in helpers are allowed in {source!r}; "
                "method calls are not."
            )
        func = _SAFE_FUNCTIONS.get(node.func.id)
        if func is None:
            allowed = ", ".join(sorted(_SAFE_FUNCTIONS))
            raise ExpressionError(
                f"Unknown function '{node.func.id}' in {source!r}. Allowed: {allowed}"
            )
        if node.keywords:
            raise ExpressionError(f"Keyword arguments are not supported in {source!r}.")
        args = [_eval_node(arg, state, source) for arg in node.args]
        try:
            return func(*args)
        except ExpressionError:
            raise
        except Exception as exc:  # noqa: BLE001 — surface as an expression problem
            raise ExpressionError(
                f"Calling '{node.func.id}' in {source!r} failed: {type(exc).__name__}: {exc}"
            ) from exc

    raise ExpressionError(
        f"Unsupported syntax ({type(node).__name__}) in expression {source!r}."
    )


def _lookup(obj: Any, key: Any, source: str) -> Any:
    """Index into a dict / list / string. Never falls back to attribute access."""
    if isinstance(obj, dict):
        if key not in obj:
            known = ", ".join(sorted(str(k) for k in obj)) or "(empty)"
            raise ExpressionError(
                f"Key '{key}' not found in expression {source!r}. Available keys: {known}"
            )
        return obj[key]
    if isinstance(obj, (list, tuple, str)):
        if isinstance(key, bool) or not isinstance(key, int):
            raise ExpressionError(
                f"'{key}' is not a valid index for a {type(obj).__name__} in {source!r}."
            )
        try:
            return obj[key]
        except IndexError:
            raise ExpressionError(
                f"Index {key} is out of range in expression {source!r}."
            ) from None
    raise ExpressionError(
        f"Cannot read '{key}' from a {type(obj).__name__} in expression {source!r}."
    )


def _compare(op: ast.cmpop, left: Any, right: Any, source: str) -> bool:
    if isinstance(op, ast.Eq):
        return bool(left == right)
    if isinstance(op, ast.NotEq):
        return bool(left != right)
    if isinstance(op, ast.Lt):
        return bool(left < right)
    if isinstance(op, ast.LtE):
        return bool(left <= right)
    if isinstance(op, ast.Gt):
        return bool(left > right)
    if isinstance(op, ast.GtE):
        return bool(left >= right)
    if isinstance(op, ast.In):
        return _contains(left, right, source)
    if isinstance(op, ast.NotIn):
        return not _contains(left, right, source)
    if isinstance(op, ast.Is):
        return left is right
    if isinstance(op, ast.IsNot):
        return left is not right
    raise ExpressionError(f"Unsupported comparison in expression {source!r}.")


def _contains(needle: Any, haystack: Any, source: str) -> bool:
    if haystack is None:
        return False
    try:
        return needle in haystack
    except TypeError as exc:
        raise ExpressionError(
            f"'in' is not supported on a {type(haystack).__name__} in {source!r}."
        ) from exc


def _binop(node: ast.BinOp, state: dict[str, Any], source: str) -> Any:
    left = _eval_node(node.left, state, source)
    right = _eval_node(node.right, state, source)
    op = node.op
    try:
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
    except ZeroDivisionError as exc:
        raise ExpressionError(f"Division by zero in expression {source!r}.") from exc
    except TypeError as exc:
        raise ExpressionError(f"Unsupported operand types in {source!r}: {exc}") from exc
    # ** is excluded on purpose: 9**9**9 is a cheap way to hang the process.
    raise ExpressionError(f"Unsupported operator in expression {source!r}.")
