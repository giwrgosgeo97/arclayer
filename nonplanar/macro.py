"""A small evaluator for Bambu Studio / Orca G-code macros.

Bambu's filament-change G-code (stored in every sliced .gcode.3mf) is written in the
slicer's macro language:
    [next_extruder]                       legacy placeholder
    {max_layer_z + 3.0}                   expression
    {nozzle_temperature_range_high[next_extruder]}
    {if flush_length_1 > 1} ... {elsif x} ... {else} ... {endif}
This module renders that text with our own values.

Unknown variables evaluate to 0 and are reported, so the caller can warn about them.
"""
import math
import re

TOKEN = re.compile(r"""\s*(?:
    (?P<num>\d+\.\d*|\.\d+|\d+)|
    (?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)|
    (?P<op>=~|!~|==|!=|<=|>=|&&|\|\||[-+*/%<>!()\[\]?:,=])
)""", re.X)


class MacroError(ValueError):
    pass


def _regex_literals(src):
    """`x =~ /re/` (Perl style, as Prusa's profiles use) -> `x =~ "re"`."""
    def lit(m):
        # the string token is read with unicode_escape, so keep the regex's backslashes doubled
        body = m.group(2).replace("\\/", "/").replace("\\", "\\\\").replace('"', '\\"')
        return f'{m.group(1)} "{body}"'
    return re.sub(r"(=~|!~)\s*/((?:[^/\\]|\\.)*)/", lit, src)


def _tokens(src):
    pos, out = 0, []
    src = _regex_literals(src.strip())
    while pos < len(src):
        m = TOKEN.match(src, pos)
        if not m or m.end() == pos:
            raise MacroError(f"Can't read {src[pos:pos + 20]!r}")
        pos = m.end()
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
    return out


class _Parser:
    """Precedence climbing: ternary < or < and < comparison < + - < * / % < unary < postfix."""

    def __init__(self, toks, env, unknown):
        self.t, self.i, self.env, self.unknown = toks, 0, env, unknown

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, val=None):
        k, v = self.peek()
        if val is not None and v != val:
            raise MacroError(f"Expected {val!r}, got {v!r}")
        self.i += 1
        return k, v

    def parse(self):
        v = self.ternary()
        if self.i != len(self.t):
            raise MacroError(f"Unexpected {self.peek()[1]!r}")
        return v

    def ternary(self):
        c = self.or_()
        if self.peek()[1] == "?":
            self.take("?")
            a = self.ternary()
            self.take(":")
            b = self.ternary()
            return a if c else b
        return c

    def or_(self):
        v = self.and_()
        while self.peek()[1] in ("or", "||"):
            self.take()
            r = self.and_()
            v = bool(v) or bool(r)
        return v

    def and_(self):
        v = self.cmp()
        while self.peek()[1] in ("and", "&&"):
            self.take()
            r = self.cmp()
            v = bool(v) and bool(r)
        return v

    def cmp(self):
        v = self.add()
        while self.peek()[1] in ("==", "!=", "<", ">", "<=", ">=", "=", "=~", "!~"):
            op = self.take()[1]
            r = self.add()
            v, r = _scalar(v), _scalar(r)
            if op in ("=~", "!~"):
                hit = re.search(str(r), str(v)) is not None
                v = hit if op == "=~" else not hit
                continue
            if isinstance(v, str) or isinstance(r, str):
                v, r = str(v), str(r)
            v = {"==": v == r, "=": v == r, "!=": v != r, "<": v < r, ">": v > r, "<=": v <= r, ">=": v >= r}[op]
        return v

    def add(self):
        v = self.mul()
        while self.peek()[1] in ("+", "-"):
            op = self.take()[1]
            r = self.mul()
            v, r = _scalar(v), _scalar(r)
            if op == "+":
                v = (str(v) + str(r)) if (isinstance(v, str) or isinstance(r, str)) else v + r
            else:
                v = v - r
        return v

    def mul(self):
        v = self.unary()
        while self.peek()[1] in ("*", "/", "%"):
            op = self.take()[1]
            r = self.unary()
            v, r = _scalar(v), _scalar(r)
            if op == "*":
                v = v * r
            elif op == "/":
                v = v / r if r else 0
            else:
                v = math.fmod(v, r) if r else 0
        return v

    def unary(self):
        k, v = self.peek()
        if v in ("-", "+"):
            self.take()
            x = self.unary()
            return -x if v == "-" else x
        if v in ("!", "not"):
            self.take()
            return not self.unary()
        return self.postfix()

    def postfix(self):
        v = self.atom()
        while self.peek()[1] == "[":
            self.take("[")
            idx = self.ternary()
            self.take("]")
            if isinstance(v, (list, tuple)):
                v = v[int(idx)] if 0 <= int(idx) < len(v) else (v[-1] if v else 0)
        return v

    def atom(self):
        k, v = self.take()
        if k == "num":
            return float(v) if "." in v else int(v)
        if k == "str":
            return bytes(v[1:-1], "utf-8").decode("unicode_escape")
        if v == "(":
            x = self.ternary()
            self.take(")")
            return x
        if k == "name":
            if v in ("true", "false"):
                return v == "true"
            if self.peek()[1] == "(":            # function call
                self.take("(")
                args = []
                while self.peek()[1] != ")":
                    args.append(self.ternary())
                    if self.peek()[1] == ",":
                        self.take(",")
                self.take(")")
                return _call(v, args)
            if v in self.env:
                return self.env[v]
            self.unknown.add(v)
            return 0
        raise MacroError(f"Unexpected {v!r}")


def _scalar(v):
    """A per-filament / per-extruder list used as a plain value means its first item (like the slicers do)."""
    return (v[0] if v else 0) if isinstance(v, (list, tuple)) else v


def _call(name, args):
    args = [a if name in ("one_of",) else a for a in args]
    fns = {"min": min, "max": max, "int": lambda x: int(x), "round": lambda x, n=0: round(x, int(n)),
           "abs": abs, "ceil": lambda x: math.ceil(x), "floor": lambda x: math.floor(x),
           "digits": lambda x, n=0, d=0: f"{float(_scalar(x)):.{int(d)}f}", "is_nil": lambda x: x is None,
           "zdigits": lambda x, n=0, d=0: f"{float(_scalar(x)):.{int(d)}f}".rstrip("0").rstrip("."),
           "one_of": lambda x, *opts: any(re.fullmatch(str(o), str(_scalar(x))) for o in opts),
           "regex_replace": lambda x, a, b: re.sub(str(a), str(b), str(_scalar(x))),
           "interpolate_table": None}
    f = fns.get(name)
    if f is None:
        raise MacroError(f"Unsupported function {name}()")
    return f(*args)


def evaluate(expr, env, unknown=None):
    unknown = set() if unknown is None else unknown
    return _Parser(_tokens(expr), env, unknown).parse()


def _fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s if s not in ("-0", "") else "0"
    if isinstance(v, (list, tuple)):
        return _fmt(v[0]) if v else ""
    return str(v)


LEGACY = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\]|_(\d+))?\]")


def render(text, env):
    """Returns (rendered_text, unknown_variable_names)."""
    unknown = set()
    out = []
    # stack of [taking_this_branch, a_branch_was_taken, parent_active]
    stack = []
    active = True
    pos = 0
    for m in re.finditer(r"\{([^{}]*)\}", text):
        if active:
            out.append(text[pos:m.start()])
        pos = m.end()
        body = m.group(1).strip()
        km = re.match(r"(if|elsif|elif|else|endif|local|global)(?![A-Za-z0-9_])", body)
        word = km.group(1) if km else ""
        rest = body[km.end():] if km else body
        if word == "if":
            cond = bool(evaluate(rest, env, unknown)) if active else False
            stack.append([cond, cond, active])
            active = active and cond
        elif word in ("elsif", "elif"):
            if not stack:
                raise MacroError("{elsif} without {if}")
            top = stack[-1]
            cond = (not top[1]) and top[2] and bool(evaluate(rest, env, unknown))
            top[0] = cond
            top[1] = top[1] or cond
            active = top[2] and cond
        elif word == "else":
            if not stack:
                raise MacroError("{else} without {if}")
            top = stack[-1]
            top[0] = not top[1]
            top[1] = True
            active = top[2] and top[0]
        elif word == "endif":
            if not stack:
                raise MacroError("{endif} without {if}")
            active = stack.pop()[2]
        elif word in ("local", "global"):
            if active:
                name, expr = rest.split("=", 1)
                env = {**env, name.strip(): evaluate(expr, env, unknown)}
        elif active:
            out.append(_fmt(evaluate(body, env, unknown)))
    if stack:
        raise MacroError("{if} without {endif}")
    if active:
        out.append(text[pos:])
    rendered = "".join(out)

    def legacy(m):
        name, i1, i2 = m.group(1), m.group(2), m.group(3)
        tail = re.fullmatch(r"(.+)_(\d+)", name)
        if name not in env and tail and tail.group(1) in env and i1 is None:
            name, i2 = tail.group(1), tail.group(2)      # [first_layer_print_min_0] = item 0 of the list
        if i2 is not None and f"{name}_{i2}" in env:   # [flush_length_1] is a name, not an index
            return _fmt(env[f"{name}_{i2}"])
        if name not in env:
            unknown.add(name + (f"_{i2}" if i2 is not None else ""))
            return m.group(0)
        v = env[name]
        if isinstance(v, (list, tuple)):
            # [name] on a per-filament list means the filament being loaded, like Bambu Studio
            i = int(i1 if i1 is not None else i2 if i2 is not None else env.get("_index", 0))
            v = v[i] if i < len(v) else (v[-1] if v else 0)
        return _fmt(v)

    return LEGACY.sub(legacy, rendered), unknown
