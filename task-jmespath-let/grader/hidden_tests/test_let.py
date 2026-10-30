"""Hidden acceptance tests: JEP-11a let-expressions for jmespath.py.

Design rules for this file (see ../../README.md, "Test design"):

* Only the public API is exercised: ``jmespath.search``, ``jmespath.compile``,
  ``jmespath.Options`` and ``jmespath.exceptions``.  Any internal design that
  implements the specified behaviour passes; no AST shapes or token names are
  asserted.
* Every behavioural clause in problem.md has at least one test, and every test
  maps back to a clause.  Nothing here is "gotcha" behaviour that the problem
  statement does not state.
* Error cases distinguish *compile-time* errors (``jmespath.compile`` raises
  ``ParseError``) from *evaluation-time* errors (``compile`` succeeds and
  ``.search`` raises ``UndefinedVariable``), because the spec requires that
  distinction.
"""
import collections

import pytest

import jmespath
import jmespath.functions
from jmespath import exceptions


STATES = {
    "home_state": "WA",
    "states": [
        {"name": "WA", "cities": ["Seattle", "Bellevue", "Olympia"]},
        {"name": "CA", "cities": ["Los Angeles", "San Francisco"]},
        {"name": "NY", "cities": ["New York City", "Albany"]},
    ],
}

# (id, expression, data, expected)
RESULT_CASES = [
    # --- basic binding and reference -------------------------------------
    ("single_binding", "let $foo = foo in $foo", {"foo": "bar"}, "bar"),
    ("binding_subexpression", "let $foo = foo.bar in $foo",
     {"foo": {"bar": "baz"}}, "baz"),
    ("variable_used_twice", "let $foo = foo in [$foo, $foo]",
     {"foo": "bar"}, ["bar", "bar"]),
    ("two_bindings", "let $a = a, $b = b in [$b, $a]", {"a": 1, "b": 2}, [2, 1]),
    ("three_bindings_odd_whitespace",
     "let  $a =a ,$b= b,  $c = c  in  [$a,$b,$c]",
     {"a": 1, "b": 2, "c": 3}, [1, 2, 3]),
    ("binding_to_null_is_defined", "let $x = missing in [$x]", {}, [None]),
    ("binding_to_literal", "let $x = `[1, 2]` in $x[1]", {}, 2),
    ("binding_to_raw_string", "let $x = 'raw' in $x", {}, "raw"),
    # --- current node handling ---------------------------------------------
    ("body_sees_current_node", "let $x = foo in bar", {"foo": 1, "bar": 2}, 2),
    ("pipe_changes_current_node", "foo | let $x = @ in bar",
     {"foo": {"bar": 3}}, 3),
    ("binding_and_body_share_current_node", "foo | let $x = bar in [$x, bar]",
     {"foo": {"bar": 3}}, [3, 3]),
    # --- variables compose with the rest of the language -------------------
    ("variable_then_field", "let $x = foo in $x.bar", {"foo": {"bar": 4}}, 4),
    ("variable_then_index", "let $x = foo in $x[1]", {"foo": [10, 11]}, 11),
    ("variable_then_projection", "let $x = foo in $x[*].a",
     {"foo": [{"a": 1}, {"a": 2}]}, [1, 2]),
    ("variable_then_flatten", "let $x = foo in $x[]",
     {"foo": [[1], [2, 3]]}, [1, 2, 3]),
    ("variable_in_pipe", "let $x = foo in $x | length(@)", {"foo": [1, 2, 3]}, 3),
    ("variable_in_function_arg", "let $x = foo in length($x)", {"foo": "abcd"}, 4),
    ("variable_in_multiselect_hash", "let $x = foo in {a: $x, b: bar}",
     {"foo": 1, "bar": 2}, {"a": 1, "b": 2}),
    ("variable_in_comparison_filter", "let $t = threshold in items[?value > $t].id",
     {"threshold": 5, "items": [{"id": "a", "value": 3},
                                {"id": "b", "value": 7},
                                {"id": "c", "value": 9}]},
     ["b", "c"]),
    ("variable_in_expref", "let $n = n in map(&[@, $n], items)",
     {"n": 9, "items": [1, 2]}, [[1, 9], [2, 9]]),
    ("variable_in_or_and_not", "let $x = x, $y = y in [$x || $y, $x && $y, !$x]",
     {"x": None, "y": 2}, [2, None, True]),
    ("let_result_piped", "let $x = foo in $x | [0]", {"foo": [5, 6]}, 5),
    ("let_inside_parens", "(let $x = foo in $x) || bar",
     {"foo": None, "bar": 7}, 7),
    ("let_as_filter_condition_operand",
     "items[?let $v = value in $v == `2`].id",
     {"items": [{"id": "a", "value": 1}, {"id": "b", "value": 2}]}, ["b"]),
    # --- the motivating example from the spec ------------------------------
    ("motivating_example_filter",
     "let $home = home_state in states[?name == $home].cities[]",
     STATES, ["Seattle", "Bellevue", "Olympia"]),
    ("per_element_via_expref",
     "map(&let $home = home_state in states[?name == $home].cities[], @)",
     [STATES, dict(STATES, home_state="NY")],
     [["Seattle", "Bellevue", "Olympia"], ["New York City", "Albany"]]),
    # --- nesting, shadowing, scope restoration -----------------------------
    ("nested_outer_visible_in_inner_binding",
     "let $a = `1` in let $b = $a in [$a, $b]", {}, [1, 1]),
    ("nested_shadowing_spec_example",
     "let $a = a in b[*].[a, $a, let $a = 'shadow' in $a]",
     {"a": "topval", "b": [{"a": "inner1"}, {"a": "inner2"}]},
     [["inner1", "topval", "shadow"], ["inner2", "topval", "shadow"]]),
    ("shadowing_restored_after_inner_let",
     "let $a = `1` in [let $a = `2` in $a, $a]", {}, [2, 1]),
    ("sibling_lets_in_multiselect",
     "[let $x = `1` in $x, let $x = `2` in $x]", {}, [1, 2]),
    ("deep_nesting", "let $a = `1` in let $b = `2` in let $c = `3` in [$a, $b, $c]",
     {}, [1, 2, 3]),
    # --- projections -------------------------------------------------------
    ("binding_stops_projection", "let $foo = foo[*] in $foo[0]",
     {"foo": [[0, 1], [2, 3], [4, 5]]}, [0, 1]),
    ("projection_without_binding_still_projects", "foo[*][0]",
     {"foo": [[0, 1], [2, 3], [4, 5]]}, [0, 2, 4]),
    ("outer_variable_visible_inside_projection",
     "let $k = key in rows[*].[name, $k]",
     {"key": "K", "rows": [{"name": "r1"}, {"name": "r2"}]},
     [["r1", "K"], ["r2", "K"]]),
    # --- let / in stay usable as ordinary identifiers ----------------------
    ("let_as_plain_identifier", "let", {"let": 1}, 1),
    ("in_as_plain_identifier", "in", {"in": 2}, 2),
    ("let_dot_in", "let.in", {"let": {"in": 3}}, 3),
    ("let_index", "let[0]", {"let": [7]}, 7),
    ("field_named_let_after_dot", "foo.let", {"foo": {"let": 8}}, 8),
    ("keywords_as_multiselect_hash_keys_and_values", "{let: in, in: let}",
     {"let": "L", "in": "I"}, {"let": "I", "in": "L"}),
    ("quoted_let_is_identifier", '"let"', {"let": 9}, 9),
    ("binding_expression_is_field_named_in", "let $x = in in $x", {"in": 5}, 5),
    ("body_is_field_named_let", "let $x = `0` in let", {"let": 6}, 6),
    ("let_as_function_argument", "length(let)", {"let": "abc"}, 3),
    # --- errors are evaluation-time, so unevaluated refs are fine -----------
    ("undefined_variable_not_evaluated_in_short_circuit", "foo || $x",
     {"foo": 1}, 1),
    ("undefined_variable_not_evaluated_in_empty_filter", "foo[?a == $x]",
     {"foo": []}, []),
]


@pytest.mark.parametrize(
    "expression, data, expected",
    [pytest.param(e, d, x, id=i) for i, e, d, x in RESULT_CASES],
)
def test_result(expression, data, expected):
    assert jmespath.search(expression, data) == expected


# (id, expression, data) -> compile succeeds, search raises UndefinedVariable
UNDEFINED_CASES = [
    ("top_level_undefined", "$foo", {}),
    ("binding_out_of_scope_after_let", "[let $foo = 'bar' in $foo, $foo]", {}),
    ("sibling_binding_not_visible_in_same_let", "let $a = `1`, $b = $a in $b", {}),
    ("undefined_in_filter_condition", "foo[?a == $x]", {"foo": [{"a": 1}]}),
    ("undefined_in_or_fallback_branch", "foo || $x", {"foo": None}),
    ("undefined_inside_expref_when_invoked", "map(&$x, foo)", {"foo": [1]}),
    ("inner_binding_not_visible_outside_inner_let",
     "[let $a = `1` in let $b = `2` in $b, $b]", {}),
]


@pytest.mark.parametrize(
    "expression, data",
    [pytest.param(e, d, id=i) for i, e, d in UNDEFINED_CASES],
)
def test_undefined_variable_is_an_evaluation_error(expression, data):
    compiled = jmespath.compile(expression)  # must NOT raise
    with pytest.raises(exceptions.UndefinedVariable):
        compiled.search(data)


def test_undefined_variable_exception_type_and_message():
    assert issubclass(exceptions.UndefinedVariable, exceptions.JMESPathError)
    assert issubclass(exceptions.UndefinedVariable, ValueError)
    with pytest.raises(exceptions.UndefinedVariable) as excinfo:
        jmespath.search("$missing_name", {})
    assert "missing_name" in str(excinfo.value)


# (id, expression) -> jmespath.compile raises ParseError (incl. subclasses)
SYNTAX_ERROR_CASES = [
    ("missing_in_keyword", "let $x = foo $x"),
    ("missing_in_keyword_at_eof", "let $x = foo"),
    ("missing_body", "let $x = foo in"),
    ("missing_assign", "let $x foo in $x"),
    ("missing_binding_expression", "let $x = in $x"),
    ("trailing_comma_in_bindings", "let $x = foo, in $x"),
    ("empty_bindings", "let in foo"),
    ("binding_without_sigil", "let x = foo in x"),
    ("assign_outside_let", "foo = bar"),
    ("bare_dollar", "$"),
    ("dollar_then_digit", "$1"),
    ("dollar_then_space", "$ foo"),
    ("variable_as_dot_rhs", "foo.$x"),
    ("variable_after_projection_without_operator", "foo[*] $x"),
    ("two_adjacent_variables", "let $x = foo in $x $y"),
    ("variable_as_function_name", "let $x = foo in $x(@)"),
]


@pytest.mark.parametrize(
    "expression",
    [pytest.param(e, id=i) for i, e in SYNTAX_ERROR_CASES],
)
def test_syntax_errors_are_compile_time(expression):
    with pytest.raises(exceptions.ParseError):
        jmespath.compile(expression)


def test_compiled_expression_is_reusable_across_inputs():
    expr = jmespath.compile("let $x = a in [$x, b]")
    assert expr.search({"a": 1, "b": 2}) == [1, 2]
    assert expr.search({"a": 3, "b": 4}) == [3, 4]


def test_state_is_clean_after_undefined_variable_error():
    bad = jmespath.compile("[let $x = `1` in $x, $x]")
    with pytest.raises(exceptions.UndefinedVariable):
        bad.search({})
    with pytest.raises(exceptions.UndefinedVariable):
        bad.search({})
    assert jmespath.search("let $x = `5` in $x", {}) == 5


def test_dict_cls_option_respected_inside_let():
    result = jmespath.search(
        "let $x = a in {x: $x, y: b}", {"a": 1, "b": 2},
        options=jmespath.Options(dict_cls=collections.OrderedDict))
    assert isinstance(result, collections.OrderedDict)
    assert list(result.items()) == [("x", 1), ("y", 2)]


def test_custom_functions_receive_variable_values():
    class CustomFunctions(jmespath.functions.Functions):
        @jmespath.functions.signature({"types": ["number"]},
                                      {"types": ["number"]})
        def _func_custom_add(self, x, y):
            return x + y

    options = jmespath.Options(custom_functions=CustomFunctions())
    assert jmespath.search("let $x = `2` in custom_add($x, `3`)", {},
                           options=options) == 5
