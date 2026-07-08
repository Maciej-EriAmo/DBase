"""
karmazyn_exec.py — WARSTWA WYKONAWCZA KarmazynOS (mini-Lisp na Store)
====================================================================
Maciej Mazur, Warsaw 2026

Po boocie: prompt, w ktory wpisujesz kod — i on sie wykonuje. To jest jezyk
do robienia narzedzi: ma `lambda`/domkniecia, wiec mozesz budowac abstrakcje.

Niezbywalne wlasciwosci (zgodne z cala architektura):
  - ZERO ZALEZNOSCI: czysty stdlib. Dziala w obrazie bez numpy.
  - ZMIENNE = ATOMY: `define`/argumenty tworza atomy; wartosc jezykowa w
    `atom.metadata["v"]`. Scope = babel (`Bubble`); lookup idzie po lancuchu
    leksykalnym (parent).
  - ZYCIE POD REACH-GC: globalny env jest korzeniem. Zmienne zlapane w
    DOMKNIECIU sa osiagalne przez hook `env_of` w `Store` — wiec reach-GC ich
    nie skasuje, nawet gdy ostygna w nie-korzeniowym bablu. To jest kuracja P6
    dla closure: `env_of(closure) -> babel-srodowiska closure`.

SZEW MONTAZU: boot oczekuje obiektu z `eval_line(str) -> str`. Bogatszy
front-end (np. karmazyn_scheme) podpina sie tak samo.

Formy: liczby, symbole, `define`, `set!`, `if`, `begin`, `lambda`,
arytmetyka `+ - * /`, porownania `= < > <= >=`, `not` `and` `or`, `print`.
"""

from typing import Any, List


# ─── Reader: tekst -> S-wyrazenie ─────────────────────────────────────────────
def tokenize(src: str) -> List[str]:
    tokens: List[str] = []
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()":
            tokens.append(ch)
            i += 1
            continue
        if ch == '"':
            i += 1
            buf: List[str] = []
            while i < n:
                if src[i] == "\\" and i + 1 < n:
                    buf.append(src[i + 1])
                    i += 2
                    continue
                if src[i] == '"':
                    i += 1
                    break
                buf.append(src[i])
                i += 1
            else:
                raise SyntaxError("niezamknięty cudzysłów w literale string")
            tokens.append("\x00" + "".join(buf))
            continue
        start = i
        while i < n and not src[i].isspace() and src[i] not in "()":
            i += 1
        tokens.append(src[start:i])
    return tokens


def read(tokens: List[str]):
    if not tokens:
        raise SyntaxError("nieoczekiwany koniec wejscia")
    tok = tokens.pop(0)
    if tok == "(":
        lst = []
        while tokens and tokens[0] != ")":
            expr, tokens = read(tokens)
            lst.append(expr)
        if not tokens:
            raise SyntaxError("brak ')' — niezamknieta lista")
        tokens.pop(0)
        return lst, tokens
    if tok == ")":
        raise SyntaxError("nieoczekiwane ')'")
    return _atomize(tok), tokens


def _atomize(tok: str) -> Any:
    if tok.startswith("\x00"):
        return StringLit(tok[1:])
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        return tok


def parse(src: str):
    tokens = tokenize(src)
    if not tokens:
        return None
    expr, rest = read(tokens)
    if rest:
        raise SyntaxError(f"nadmiarowe tokeny: {' '.join(map(str, rest))}")
    return expr


# ─── Domkniecie: kod + zlapane srodowisko (babel) ─────────────────────────────
class StringLit:
    __slots__ = ("value",)

    def __init__(self, value: str):
        self.value = value


class Closure:
    __slots__ = ("params", "body", "env")

    def __init__(self, params, body, env):
        self.params = params
        self.body = body
        self.env = env                       # Bubble — widziany przez env_of -> reach-GC

    def __repr__(self):
        return f"<closure ({' '.join(self.params)})>"


# ─── Ewaluator na Store ───────────────────────────────────────────────────────
class Evaluator:
    def __init__(self, store, env_label: str = "repl"):
        self.store = store
        self.env = store.bubble_new(env_label)
        store.set_root(self.env)             # globalne zmienne osiagalne
        # KURACJA P6 DLA CLOSURE: ucz rdzen, jak czytac osiagalnosc z wartosci.
        # Rdzen sam nie zna jezyka; podajemy mu hook (udokumentowany punkt rozszerzen).
        store._env_of = lambda v: v.env if isinstance(v, Closure) else None
        self._builtins = {
            "+": lambda *a: _fold(lambda x, y: x + y, a),
            "-": lambda *a: (-a[0] if len(a) == 1 else _fold(lambda x, y: x - y, a)),
            "*": lambda *a: _fold(lambda x, y: x * y, a, start=1),
            "/": lambda *a: _fold(_safediv, a),
            "=": lambda x, y: x == y,
            "<": lambda x, y: x < y,
            ">": lambda x, y: x > y,
            "<=": lambda x, y: x <= y,
            ">=": lambda x, y: x >= y,
            "not": lambda x: not _truthy(x),
            "and": lambda *a: all(_truthy(x) for x in a),
            "or": lambda *a: any(_truthy(x) for x in a),
            "print": lambda *a: " ".join(_fmt(x) for x in a),
        }

    # publiczny szew dla boota / innych shellow
    def eval_line(self, line: str) -> str:
        line = (line or "").strip()
        if not line or line.startswith(";"):
            return ""
        try:
            expr = parse(line)
            return "" if expr is None else _fmt(self._eval(expr, self.env))
        except Exception as e:
            return f"blad: {type(e).__name__}: {e}"

    # ── jadro ewaluacji (parametryzowane srodowiskiem) ──
    def _eval(self, x: Any, env) -> Any:
        if isinstance(x, StringLit):
            return x.value
        if isinstance(x, (int, float)):
            return x
        if isinstance(x, str):                       # symbol -> zmienna
            atom = env.lookup(x)
            if atom is None:
                raise NameError(f"'{x}' nieznane")
            return atom.metadata["v"]
        if not isinstance(x, list) or not x:
            raise SyntaxError(f"nie umiem wykonac: {x!r}")

        head = x[0]

        # ── formy specjalne ──
        if head == "define":
            if len(x) != 3 or not isinstance(x[1], str):
                raise SyntaxError("uzycie: (define nazwa wyrazenie)")
            val = self._eval(x[2], env)
            atom = self.store.atom_new("var", x[1], value=val)
            env.bind(x[1], atom)                     # w BIEZACYM env (globalny lub lokalny)
            return f"{x[1]} := {_fmt(val)}"
        if head == "set!":
            if len(x) != 3 or not isinstance(x[1], str):
                raise SyntaxError("uzycie: (set! nazwa wyrazenie)")
            atom = env.lookup(x[1])                   # znajdz istniejace wiazanie (po lancuchu)
            if atom is None:
                raise NameError(f"set!: '{x[1]}' nie istnieje")
            atom.metadata["v"] = self._eval(x[2], env)   # mutacja w miejscu (ten sam atom/id)
            return f"{x[1]} <- {_fmt(atom.metadata['v'])}"
        if head == "if":
            if len(x) not in (3, 4):
                raise SyntaxError("uzycie: (if warunek to [else])")
            if _truthy(self._eval(x[1], env)):
                return self._eval(x[2], env)
            return self._eval(x[3], env) if len(x) == 4 else None
        if head == "begin":
            result = None
            for sub in x[1:]:
                result = self._eval(sub, env)
            return result
        if head == "lambda":
            if len(x) != 3 or not isinstance(x[1], list):
                raise SyntaxError("uzycie: (lambda (params...) cialo)")
            if not all(isinstance(p, str) for p in x[1]):
                raise SyntaxError("parametry lambda musza byc symbolami")
            return Closure(x[1], x[2], env)          # domkniecie lapie BIEZACY env

        # ── zastosowanie ──
        if isinstance(head, str) and head in self._builtins:
            args = [self._eval(a, env) for a in x[1:]]
            return self._builtins[head](*args)
        fn = self._eval(head, env)                   # symbol->Closure albo pod-wyrazenie
        if isinstance(fn, Closure):
            args = [self._eval(a, env) for a in x[1:]]
            return self._apply(fn, args)
        raise SyntaxError(f"nie da sie wywolac: {head!r}")

    def _apply(self, clo: "Closure", args: list) -> Any:
        if len(args) != len(clo.params):
            raise TypeError(f"zla liczba argumentow: oczekiwano {len(clo.params)}, "
                            f"podano {len(args)}")
        local = self.store.bubble_new("call", parent=clo.env)   # nowy scope, rodzic = env closure
        for p, val in zip(clo.params, args):
            atom = self.store.atom_new("arg", p, value=val)
            local.bind(p, atom)
        return self._eval(clo.body, local)


# ─── pomocnicze ───────────────────────────────────────────────────────────────
def _fold(op, args, start=None):
    vals = list(args)
    if start is None:
        if not vals:
            return 0
        acc, rest = vals[0], vals[1:]
    else:
        acc, rest = start, vals
    for a in rest:
        acc = op(acc, a)
    return acc


def _safediv(x, y):
    if y == 0:
        raise ZeroDivisionError("dzielenie przez zero")
    return x / y


def _truthy(v) -> bool:
    return not (v is False or v is None or v == 0)


def _fmt(v) -> str:
    if isinstance(v, StringLit):
        return v.value
    if v is True:
        return "#t"
    if v is False:
        return "#f"
    if v is None:
        return "()"
    if isinstance(v, Closure):
        return repr(v)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)