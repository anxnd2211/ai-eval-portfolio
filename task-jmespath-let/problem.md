# Add lexical scoping (`let` expressions) to jmespath.py

Repository: `jmespath/jmespath.py` at commit `2812594e69d43098ef60f81f4efc404c071b0418`
(the checkout at `/work/repo`). Python 3.12, no third-party runtime dependencies.

## Background

JMESPath evaluates every sub-expression against a *current node* that narrows as
the query proceeds. Once an expression has drilled into `states[*]`, nothing
inside it can refer back to a value from the parent document. The JMESPath
community specification fixes this with **let expressions** (JEP-11a,
"Lexical Scoping"). The Python implementation in this repository does not
support them yet. Your job is to add them.

## Syntax

```
let-expression   = "let" bindings "in" expression
bindings         = variable-binding *( "," variable-binding )
variable-binding = variable-ref "=" expression
variable-ref     = "$" unquoted-string
```

`unquoted-string` is the existing identifier rule: `[A-Za-z_][A-Za-z0-9_]*`.
Both `let-expression` and `variable-ref` are new alternatives of `expression`,
so they may appear anywhere an expression may appear (multi-select lists and
hashes, function arguments, filter conditions, pipes, parentheses, expression
references `&...`).

Examples that must parse and evaluate:

```
let $foo = bar in {a: myvar, b: $foo}
let $foo = baz[0] in bar[? baz == $foo ] | [0]
let $a = b, $c = d in bar[*].[$a, $c, foo, bar]
```

## Semantics

1. **Bindings first, then body.** Every binding expression is evaluated against
   the current node, in the scope that *encloses* the let expression. Only after
   all bindings are evaluated does the body run with the new names visible.
   Consequently a binding cannot refer to a sibling binding of the same `let`
   (`let $a = \`1\`, $b = $a in $b` is an undefined-variable error unless `$a`
   is already bound by an outer scope).
2. **Current node is unchanged.** Binding expressions and the body are all
   evaluated against the same current node the let expression received.
   `foo | let $x = @ in bar` evaluates `bar` against the value of `foo`.
3. **Visibility.** A binding is visible only while the body of its `let` is
   being evaluated, including inside projections, filters and expression
   references invoked from that body (for example `map(&[@, $n], items)`).
   After the let expression finishes, its bindings are gone:
   `[let $x = 'a' in $x, $x]` is an error at the second `$x`.
4. **Shadowing.** An inner `let` may rebind a name from an outer scope; the
   outer value is restored when the inner body completes
   (`let $a = \`1\` in [let $a = \`2\` in $a, $a]` → `[2, 1]`).
5. **Variable values are plain values.** Binding a projection stores its
   result; the projection does not continue through the variable.
   `let $foo = foo[*] in $foo[0]` with `{"foo": [[0,1],[2,3],[4,5]]}` → `[0, 1]`
   (whereas `foo[*][0]` → `[0, 2, 4]`).
6. **Undefined variables are evaluation-time errors.** Referencing a variable
   that is not bound must raise `jmespath.exceptions.UndefinedVariable`, a new
   subclass of `jmespath.exceptions.JMESPathError`, whose message contains the
   variable name. The error is raised when the reference is *evaluated*, not
   when the expression is compiled: `jmespath.compile('$foo')` succeeds and
   `.search({})` raises. A reference that is never evaluated (for example the
   right side of `||` when the left side is truthy, or a filter condition over
   an empty list) raises nothing. Binding a name to `null` makes it defined.
7. **Contextual keywords.** `let` and `in` are *not* reserved. They remain
   ordinary identifiers everywhere the grammar does not force the keyword
   reading: `let`, `in`, `let.in`, `{let: in}`, `let[0]`, `length(let)` and even
   `let $x = in in $x` (binding `$x` to the field named `in`) must keep working.
   Quoted identifiers (`"let"`) are never keywords.
8. **Syntax errors stay compile-time.** Malformed let expressions must raise a
   `jmespath.exceptions.ParseError` (or one of its existing subclasses) from
   `jmespath.compile`. This includes a missing `in`, a missing body, a missing
   `=`, a trailing comma in the bindings, `let in foo`, a bare `$`, `$1`,
   `$ foo`, `foo.$x`, `foo[*] $x`, and `=` used anywhere outside a binding
   (`foo = bar`). `==` keeps its meaning.

## Deliverable

A patch to files under `jmespath/` (optionally `CHANGELOG.rst` / `README.rst`).
Do not modify, add or delete anything under `tests/`, and do not add
`conftest.py`, packaging or pytest configuration files; such patches are
rejected by the grader before any test runs.

The existing test suite (`python -m pytest tests`) must keep passing. Hidden
tests exercise only the public API (`jmespath.search`, `jmespath.compile`,
`jmespath.Options`, `jmespath.exceptions`), so the internal design (token names,
AST node shapes, how scopes are stored) is entirely yours.
