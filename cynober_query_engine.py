#!/usr/bin/env python3
"""
cynober_query_engine.py — Silnik zapytań KarminQL v6.1 (PL + aliasy EN)
==========================================================================
Rozszerzenia SQL-owe: WYPISZ GDZIE, ZAKTUALIZUJ GDZIE, złożone warunki,
JOIN po relacji (POŁĄCZONE JAKO), JOIN relacyjny (DOŁĄCZ Z), HAVING (MAJĄCE),
SCAL (UPSERT), OPISZ BAZĘ, LIKE/PODOBNE, CASE WHEN i wyrażenia w projekcji,
operacje zbiorów, MIĘDZY, podzapytania, WSTAW Z, EKSPORT/IMPORT CSV.
"""

import csv
import os
import re
import time
from typing import Any, Optional, Tuple, Set, List, Union
from dataclasses import dataclass
from functools import singledispatchmethod

# ─── 1. FUNDAMENT TYPÓW ──────────────────────────────────────────────────

class KarminType:
    @staticmethod
    def parse(val: Any) -> Any:
        if isinstance(val, (int, float, bool, type(None))): return val
        s = str(val).strip()
        if s.upper() in ("PRAWDA", "TRUE"): return True
        if s.upper() in ("FAŁSZ", "FALSE"): return False
        if s.upper() in ("NIC", "NULL"): return None
        try:
            if "." in s: return float(s)
            return int(s)
        except ValueError:
            return s.strip('"').strip("'")

    @staticmethod
    def to_str(val: Any) -> str:
        if val is True: return "PRAWDA"
        if val is False: return "FAŁSZ"
        if val is None: return "NIC"
        return str(val)


# ─── 2. DRZEWO AST ──────────────────────────────────────────────────────

class ASTNode:
    def accept(self, visitor): return visitor.visit(self)


@dataclass(frozen=True)
class CondCompare:
    key: str
    op: str
    val: str
    val2: Optional[str] = None
    subquery: Optional[Any] = None


@dataclass(frozen=True)
class CondNot:
    inner: Any


@dataclass(frozen=True)
class CondAnd:
    parts: Tuple[Any, ...]


@dataclass(frozen=True)
class CondOr:
    parts: Tuple[Any, ...]


CondExpr = Union[CondCompare, CondNot, CondAnd, CondOr]


@dataclass(frozen=True)
class CreateNamespaceNode(ASTNode): name: str

@dataclass(frozen=True)
class UseNamespaceNode(ASTNode): name: str

@dataclass(frozen=True)
class CreateBubbleNode(ASTNode): name: str

@dataclass(frozen=True)
class AddPropertyNode(ASTNode): target: str; key: str; value: str

@dataclass(frozen=True)
class BulkInjectNode(ASTNode):
    target: str
    properties: Tuple[Tuple[str, str], ...]

@dataclass(frozen=True)
class BulkCreateNode(ASTNode):
    entries: Tuple[Tuple[str, Tuple[Tuple[str, str], ...]], ...]

@dataclass(frozen=True)
class InsertFromNode(ASTNode):
    subquery: ASTNode

@dataclass(frozen=True)
class ExportCsvNode(ASTNode):
    path: str
    subquery: ASTNode

@dataclass(frozen=True)
class ImportCsvNode(ASTNode):
    path: str
    name_column: Optional[str] = None

@dataclass(frozen=True)
class DescribeDatabaseNode(ASTNode):
    pass

@dataclass(frozen=True)
class MergeNode(ASTNode):
    bubble_name: Optional[str]
    match_key: Optional[str]
    match_value: Optional[str]
    properties: Tuple[Tuple[str, str], ...]

@dataclass(frozen=True)
class RelJoinSpec:
    alias: str
    pairs: Tuple[Tuple[str, str], ...]
    left_outer: bool = False
    filter_subquery: Optional[Any] = None


@dataclass(frozen=True)
class ProjColumn:
    label: str
    kind: str = "ref"
    ref_alias: Optional[str] = None
    ref_key: Optional[str] = None
    arith_op: Optional[str] = None
    arith_left: Optional[Any] = None
    arith_right: Optional[Any] = None
    case_parts: Optional[Tuple[Tuple[Any, str], ...]] = None
    case_else: Optional[str] = None

@dataclass(frozen=True)
class UpdatePropertyNode(ASTNode): target: str; key: str; value: str

@dataclass(frozen=True)
class UpdateWhereNode(ASTNode):
    key: str
    value: str
    cond: CondExpr
    join_relation: Optional[str] = None
    join_target: Optional[str] = None

@dataclass(frozen=True)
class RemovePropertyNode(ASTNode): target: str; key: str

@dataclass(frozen=True)
class ConnectNode(ASTNode): source: str; target: str; relation: str

@dataclass(frozen=True)
class DisconnectNode(ASTNode): source: str; target: str; relation: str

@dataclass(frozen=True)
class DeleteBubbleNode(ASTNode): target: str

@dataclass(frozen=True)
class ShowBubbleNode(ASTNode): target: str

@dataclass(frozen=True)
class ProjectNode(ASTNode): target: str; keys: List[str]

@dataclass(frozen=True)
class ProjectWhereNode(ASTNode):
    columns: Tuple[ProjColumn, ...]
    cond: CondExpr
    join_relation: Optional[str] = None
    join_target: Optional[str] = None
    rel_joins: Tuple[RelJoinSpec, ...] = ()
    distinct: bool = False
    sort_by: Optional[str] = None
    sort_desc: bool = False
    limit: Optional[int] = None
    offset: Optional[int] = None

    @property
    def keys(self) -> List[str]:
        return [c.label for c in self.columns]

@dataclass(frozen=True)
class SearchNode(ASTNode): query: str

@dataclass(frozen=True)
class FindRelationNode(ASTNode):
    relation: str
    target: str
    cond: Optional[CondExpr] = None
    sort_by: Optional[str] = None
    sort_desc: bool = False
    limit: Optional[int] = None
    offset: Optional[int] = None

@dataclass(frozen=True)
class ExciteNode(ASTNode): target: str; energy: float; relation: Optional[str]

@dataclass(frozen=True)
class AssignNode(ASTNode): var_name: str; expr: ASTNode

@dataclass(frozen=True)
class ConditionNode(ASTNode):
    action: str
    cond: CondExpr
    join_relation: Optional[str] = None
    join_target: Optional[str] = None
    sort_by: Optional[str] = None
    sort_desc: bool = False
    limit: Optional[int] = None
    offset: Optional[int] = None

@dataclass(frozen=True)
class AggregateNode(ASTNode):
    action: str
    key: str
    cond: CondExpr
    join_relation: Optional[str] = None
    join_target: Optional[str] = None
    sort_by: Optional[str] = None
    sort_desc: bool = False
    limit: Optional[int] = None
    offset: Optional[int] = None
    group_by: Optional[List[str]] = None
    having_action: Optional[str] = None
    having_op: Optional[str] = None
    having_val: Optional[str] = None

@dataclass(frozen=True)
class ShowHistoryNode(ASTNode): target: str; key: str

@dataclass(frozen=True)
class BeginTxNode(ASTNode): pass

@dataclass(frozen=True)
class CommitTxNode(ASTNode): pass

@dataclass(frozen=True)
class RollbackTxNode(ASTNode): pass

@dataclass(frozen=True)
class VarRefNode(ASTNode):
    var_name: str

@dataclass(frozen=True)
class SetOpNode(ASTNode):
    op: str
    left: ASTNode
    right: ASTNode
    sort_by: Optional[str] = None
    sort_desc: bool = False
    limit: Optional[int] = None
    offset: Optional[int] = None


SET_UNION_OPS = ("ZŁĄCZ", "RÓŻNICA")
SET_INTERSECT_OP = "PRZECIĘCIE"
SET_OPS = SET_UNION_OPS + (SET_INTERSECT_OP,)

# Aliasy angielskie → kanon polski (kolejność: dłuższe frazy pierwsze)
_ALIAS_PHRASES: Tuple[Tuple[str, str], ...] = (
    (r"CREATE\s+NAMESPACE", "UTRWAL PRZESTRZEŃ"),
    (r"USE\s+NAMESPACE", "WYBIERZ PRZESTRZEŃ"),
    (r"CREATE\s+MANY", "UTRWAL WIELE"),
    (r"COUNT\s+BUBBLES", "POLICZ BĄBLE"),
    (r"COUNT\s+DISTINCT", "POLICZ RÓŻNE"),
    (r"DELETE\s+BUBBLES", "USUŃ BĄBLE"),
    (r"DELETE\s+BUBBLE", "USUŃ BĄBEL"),
    (r"FIND\s+CONNECTED\s+AS", "ZNAJDŹ POŁĄCZONE JAKO"),
    (r"FIND\s+JOINED\s+AS", "ZNAJDŹ POŁĄCZONE JAKO"),
    (r"INSERT\s+MANY", "WSTRZYKNIJ WIELE"),
    (r"INSERT\s+FROM", "WSTAW Z"),
    (r"SELECT\s+DISTINCT", "WYPISZ UNIKALNE"),
    (r"EXPORT\s+CSV", "EKSPORT CSV"),
    (r"IMPORT\s+CSV", "IMPORT CSV"),
    (r"CONNECTED\s+AS", "POŁĄCZONE JAKO"),
    (r"JOINED\s+AS", "POŁĄCZONE JAKO"),
    (r"IS\s+NOT\s+NULL", "NIE JEST NIC"),
    (r"IS\s+NULL", "JEST NIC"),
    (r"NOT\s+IN", "NIE W"),
    (r"BY\s+RELATION", "PO RELACJI"),
    (r"WITH\s+ENERGY", "ENERGIĄ"),
    (r"SORT\s+BY", "SORTUJ WEDŁUG"),
    (r"GROUP\s+BY", "POGRUPUJ"),
    (r"DESCRIBE\s+DATABASE", "OPISZ BAZĘ"),
    (r"DESCRIBE\s+DB", "OPISZ BAZĘ"),
    (r"MERGE\s+ON", "SCAL PO"),
    (r"UPSERT\s+ON", "SCAL PO"),
    (r"LEFT\s+JOIN\s+\"([^\"]+)\"\s+ON", r'LEWY DOŁĄCZ Z "\1" GDZIE'),
    (r"JOIN\s+\"([^\"]+)\"\s+ON", r'DOŁĄCZ Z "\1" GDZIE'),
)

_ALIAS_WORDS: Tuple[Tuple[str, str], ...] = (
    ("CREATE", "UTRWAL"),
    ("INSERT", "WSTRZYKNIJ"),
    ("UPDATE", "ZAKTUALIZUJ"),
    ("DELETE", "USUŃ"),
    ("CONNECT", "POŁĄCZ"),
    ("DISCONNECT", "ROZŁĄCZ"),
    ("SELECT", "WYPISZ"),
    ("SHOW", "POKAŻ"),
    ("HISTORY", "HISTORIA"),
    ("SEARCH", "SZUKAJ"),
    ("EXCITE", "WZBUDŹ"),
    ("FIND", "ZNAJDŹ"),
    ("UNION", "ZŁĄCZ"),
    ("INTERSECT", "PRZECIĘCIE"),
    ("EXCEPT", "RÓŻNICA"),
    ("UNIQUE", "UNIKALNE"),
    ("DISTINCT", "UNIKALNE"),
    ("WHERE", "GDZIE"),
    ("HAVING", "MAJĄCE"),
    ("OFFSET", "PRZESUNIĘCIE"),
    ("CONTAINS", "ZAWIERA"),
    ("BETWEEN", "MIĘDZY"),
    ("SUM", "SUMA"),
    ("AVG", "ŚREDNIA"),
    ("LET", "NIECH"),
    ("COLUMN", "KOLUMNA"),
    ("INTO", "DO"),
    ("FROM", "Z"),
    ("ENERGY", "ENERGIĄ"),
    ("RELATION", "RELACJI"),
    ("DESC", "MALEJĄCO"),
    ("ASC", "ROSNĄCO"),
    ("AND", "ORAZ"),
    ("OR", "LUB"),
    ("NOT", "NIE"),
    ("AS", "JAKO"),
    ("TO", "Z"),
    ("IN", "W"),
    ("LIKE", "PODOBNE"),
    ("MERGE", "SCAL"),
    ("UPSERT", "SCAL"),
)


def _map_outside_quotes(text: str, mapper) -> str:
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        if text[i] == '"':
            j = i + 1
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if text[j] == '"':
                    j += 1
                    break
                j += 1
            out.append(mapper(text[i:j], quoted=True))
            i = j
            continue
        j = i
        while j < n and text[j] != '"':
            j += 1
        if j > i:
            out.append(mapper(text[i:j], quoted=False))
        i = j
    return "".join(out)


def _apply_phrase_aliases(segment: str) -> str:
    s = segment
    for pattern, repl in _ALIAS_PHRASES:
        s = re.sub(pattern, repl, s, flags=re.IGNORECASE)
    for word, repl in _ALIAS_WORDS:
        s = re.sub(rf"\b{re.escape(word)}\b", repl, s, flags=re.IGNORECASE)
    return s


def normalize_karminql_aliases(line: str) -> str:
    """Angielskie słowa kluczowe → polski kanon KarminQL (poza cudzysłowami)."""
    def mapper(seg: str, *, quoted: bool) -> str:
        if quoted:
            return seg
        s = _apply_phrase_aliases(seg)
        def _between_and_to_do(m: re.Match) -> str:
            head = m.group(1)
            if re.search(r"\bDO\b", head, re.IGNORECASE):
                return m.group(0)
            return f"{head} DO "

        s = re.sub(
            r"(\bMIĘDZY\s+.+?)\s+ORAZ\s+",
            _between_and_to_do,
            s,
            flags=re.IGNORECASE,
        )
        return s

    return _map_outside_quotes(line, mapper)


def _like_to_regex(pattern: str) -> str:
    out: List[str] = []
    for ch in pattern:
        if ch == "%":
            out.append(".*")
        elif ch == "_":
            out.append(".")
        else:
            out.append(re.escape(ch))
    return "^" + "".join(out) + "$"


def _like_match(value: Any, pattern: str) -> bool:
    if value is None:
        return False
    return re.match(_like_to_regex(pattern), str(value), re.IGNORECASE) is not None


# ─── 3. PARSER WARUNKÓW ───────────────────────────────────────────────────

class ConditionParser:
    """Parser wyrażeń logicznych: nawiasy, NIE, ORAZ, LUB, JEST NIC, MIĘDZY, podzapytania."""

    def __init__(self):
        self._subquery_parser = None

    def set_subquery_parser(self, parser_fn):
        self._subquery_parser = parser_fn

    compare_pattern = re.compile(
        r'^"([^"]+)"\s*('
        r'!=|>=|<=|=|>|<|ZAWIERA|NIE\s+PODOBNE|NIE\s+LIKE|PODOBNE|LIKE|NIE\s+W|W|'
        r'JEST\s+NIC|NIE\s+JEST\s+NIC'
        r')\s*(.*)$',
        re.IGNORECASE,
    )

    def parse(self, text: str) -> CondExpr:
        text = normalize_karminql_aliases(text.strip())
        if not text:
            raise SyntaxError("Brak warunku logicznego")
        parts = self._split_top(text, "LUB")
        if len(parts) > 1:
            return CondOr(tuple(self._parse_and(p) for p in parts))
        return self._parse_and(text)

    def _parse_and(self, text: str) -> CondExpr:
        parts = self._split_top(text, "ORAZ")
        if len(parts) > 1:
            return CondAnd(tuple(self._parse_not(p) for p in parts))
        return self._parse_not(text)

    def _parse_not(self, text: str) -> CondExpr:
        text = text.strip()
        upper = text.upper()
        if upper.startswith("NIE "):
            inner = text[4:].strip()
            if inner.startswith("("):
                return CondNot(self.parse(self._unwrap_parens(inner)))
            return CondNot(self._parse_not(inner))
        return self._parse_primary(text)

    def _parse_primary(self, text: str) -> CondExpr:
        text = text.strip()
        if text.startswith("("):
            return self.parse(self._unwrap_parens(text))
        return self._parse_compare(text)

    def _parse_literal(self, raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("$"):
            return raw
        return raw.strip('"').strip("'")

    def _parse_compare(self, text: str) -> CondCompare:
        text = text.strip()

        if m := re.match(r'^"([^"]+)"\s+MIĘDZY\s+(.+?)\s+DO\s+(.+)$', text, re.IGNORECASE):
            return CondCompare(
                m.group(1), "MIĘDZY",
                self._parse_literal(m.group(2)),
                val2=self._parse_literal(m.group(3)),
            )

        if m := re.match(r'^"([^"]+)"\s+(NIE\s+W|W)\s*\(', text, re.IGNORECASE):
            op = re.sub(r"\s+", " ", m.group(2).upper()).strip()
            paren_start = text.index("(", m.end() - 1)
            inner = self._unwrap_parens(text[paren_start:])
            if not self._subquery_parser:
                raise SyntaxError("Podzapytania nie są dostępne w tym kontekście")
            return CondCompare(m.group(1), op, "", subquery=self._subquery_parser(inner))

        m = self.compare_pattern.match(text)
        if not m:
            raise SyntaxError(f"Niepoprawny warunek logiczny: {text}")
        key = m.group(1)
        op = re.sub(r"\s+", " ", m.group(2).upper()).strip()
        val_str = m.group(3).strip()
        if op in ("JEST NIC", "NIE JEST NIC"):
            val_str = ""
        elif not val_str.startswith("$"):
            val_str = val_str.strip('"').strip("'")
        return CondCompare(key, op, val_str)

    def _unwrap_parens(self, text: str) -> str:
        text = text.strip()
        if not text.startswith("("):
            return text
        depth = 0
        for i, ch in enumerate(text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    if i != len(text) - 1:
                        raise SyntaxError(f"Nieoczekiwany tekst po nawiasie: {text[i + 1:]}")
                    return text[1:i]
        raise SyntaxError(f"Niezamknięty nawias w: {text}")

    def _split_top(self, text: str, kw: str) -> List[str]:
        kw_upper = f" {kw.upper()} "
        parts: List[str] = []
        start = 0
        depth = 0
        in_quote = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '"':
                in_quote = not in_quote
            elif not in_quote:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif depth == 0 and text[i:].upper().startswith(kw_upper):
                    parts.append(text[start:i].strip())
                    i += len(kw_upper)
                    start = i
                    continue
            i += 1
        parts.append(text[start:].strip())
        return [p for p in parts if p]


# ─── 4. PARSER ZAPYTAŃ ──────────────────────────────────────────────────

class KarminParser:
    def __init__(self):
        self.cond_parser = ConditionParser()
        self.cond_parser.set_subquery_parser(self._parse_subquery_atom)
        self.cond_pattern = re.compile(
            r'^(ZNAJDŹ|POLICZ\s+BĄBLE|USUŃ\s+BĄBLE)'
            r'(?:\s+POŁĄCZONE\s+JAKO\s+"([^"]+)"\s+Z\s+"([^"]+)")?'
            r'\s+GDZIE\s+(.+)$',
            re.IGNORECASE,
        )
        self.agg_pattern = re.compile(
            r'^(SUMA|ŚREDNIA|MIN|MAX|POLICZ(?:\s+RÓŻNE)?)\s+"([^"]+)"\s+'
            r'(?:POŁĄCZONE\s+JAKO\s+"([^"]+)"\s+Z\s+"([^"]+)"\s+)?'
            r'GDZIE\s+(.+)$',
            re.IGNORECASE,
        )
        self.wypisz_gdzie_pattern = re.compile(
            r'^WYPISZ\s+(?:UNIKALNE\s+)?(.+?)'
            r'(?:\s+POŁĄCZONE\s+JAKO\s+"([^"]+)"\s+Z\s+"([^"]+)")?'
            r'\s+GDZIE\s+(.+)$',
            re.IGNORECASE,
        )
        self.zaktualizuj_gdzie_pattern = re.compile(
            r'^ZAKTUALIZUJ\s+"([^"]+)"\s*(?:->|=)\s*(.+?)'
            r'(?:\s+POŁĄCZONE\s+JAKO\s+"([^"]+)"\s+Z\s+"([^"]+)")?'
            r'\s+GDZIE\s+(.+)$',
            re.IGNORECASE,
        )
        self.znajdz_rel_gdzie_pattern = re.compile(
            r'^ZNAJDŹ\s+POŁĄCZONE\s+JAKO\s+"([^"]+)"\s+Z\s+"([^"]+)"\s+GDZIE\s+(.+)$',
            re.IGNORECASE,
        )
        self.znajdz_rel_pattern = re.compile(
            r'^ZNAJDŹ\s+POŁĄCZONE\s+JAKO\s+"([^"]+)"\s+Z\s+"([^"]+)"$',
            re.IGNORECASE,
        )
        self.assign_pattern = re.compile(r'^NIECH\s+\$([a-zA-Z0-9_]+)\s*=\s*(.+)$', re.IGNORECASE)
        self.var_set_pattern = re.compile(
            r'^(ZŁĄCZ|PRZECIĘCIE|RÓŻNICA)\s+(\$[a-zA-Z0-9_]+)\s+(\$[a-zA-Z0-9_]+)$',
            re.IGNORECASE,
        )

        self.patterns = {
            'utrwal_przestrzen': re.compile(r'^UTRWAL\s+PRZESTRZEŃ\s+"([^"]+)"$', re.IGNORECASE),
            'wybierz_przestrzen': re.compile(r'^WYBIERZ\s+PRZESTRZEŃ\s+"([^"]+)"$', re.IGNORECASE),
            'utrwal': re.compile(r'^UTRWAL\s+"([^"]+)"$', re.IGNORECASE),
            'wstrzyknij': re.compile(r'^WSTRZYKNIJ\s+"([^"]+)"\s*(?:->|=)\s*(.+?)\s+DO\s+"([^"]+)"$', re.IGNORECASE),
            'wstrzyknij_wiele': re.compile(r'^WSTRZYKNIJ\s+WIELE\s+(.+?)\s+DO\s+"([^"]+)"$', re.IGNORECASE),
            'utrwal_wiele': re.compile(r'^UTRWAL\s+WIELE\s+(.+)$', re.IGNORECASE),
            'zaktualizuj': re.compile(r'^ZAKTUALIZUJ\s+"([^"]+)"\s*(?:->|=)\s*(.+?)\s+(?:W|DO)\s+"([^"]+)"$', re.IGNORECASE),
            'usun_atrybut': re.compile(r'^USUŃ\s+"([^"]+)"\s*Z\s+"([^"]+)"$', re.IGNORECASE),
            'usun_babel': re.compile(r'^USUŃ\s*BĄBEL\s+"([^"]+)"$', re.IGNORECASE),
            'polacz': re.compile(r'^POŁĄCZ\s+"([^"]+)"\s*Z\s+"([^"]+)"\s*JAKO\s+"([^"]+)"$', re.IGNORECASE),
            'rozlacz': re.compile(r'^ROZŁĄCZ\s+"([^"]+)"\s*Z\s+"([^"]+)"\s*JAKO\s+"([^"]+)"$', re.IGNORECASE),
            'wypisz': re.compile(r'^WYPISZ\s+(.+)\s+(?:Z|W)\s+"([^"]+)"$', re.IGNORECASE),
            'pokaz': re.compile(r'^POKAŻ\s+"([^"]+)"$', re.IGNORECASE),
            'historia': re.compile(r'^HISTORIA\s+"([^"]+)"\s*(?:Z|W)\s+"([^"]+)"$', re.IGNORECASE),
            'szukaj': re.compile(r'^SZUKAJ\s+"([^"]+)"$', re.IGNORECASE),
            'wzbudz': re.compile(r'^WZBUDŹ\s+"([^"]+)"\s+ENERGIĄ\s+(\d+(?:\.\d+)?)(?:\s+PO\s+RELACJI\s+"([^"]+)")?$', re.IGNORECASE),
            'begin': re.compile(r'^BEGIN$', re.IGNORECASE),
            'commit': re.compile(r'^COMMIT$', re.IGNORECASE),
            'rollback': re.compile(r'^ROLLBACK$', re.IGNORECASE),
        }

    def parse(self, script: str) -> List[Tuple[int, str, ASTNode]]:
        ast_nodes = []
        for line_no, line in enumerate(script.strip().split('\n'), 1):
            line = line.strip()
            if not line or line.startswith('#'): continue
            line = normalize_karminql_aliases(line)

            node = self._parse_line(line)
            if node: ast_nodes.append((line_no, line, node))
            else: raise SyntaxError(f"Nierozpoznana składnia w linii {line_no}: '{line}'")
        return ast_nodes

    def _parse_keys(self, keys_str: str) -> List[str]:
        return [k.strip().strip('"') for k in keys_str.split(",")]

    def _split_projection_items(self, text: str) -> List[str]:
        items: List[str] = []
        depth, start = 0, 0
        in_quote = False
        i = 0
        upper = text.upper()
        while i < len(text):
            ch = text[i]
            if ch == '"':
                in_quote = not in_quote
            elif not in_quote:
                if upper.startswith("CASE", i):
                    depth += 1
                if upper.startswith("END", i) and (i + 3 >= len(text) or not text[i + 3].isalnum()):
                    depth = max(0, depth - 1)
                if ch == "," and depth == 0:
                    items.append(text[start:i].strip())
                    start = i + 1
            i += 1
        tail = text[start:].strip()
        if tail:
            items.append(tail)
        return items

    def _parse_case_column(self, text: str, label: Optional[str]) -> ProjColumn:
        norm = text.strip()
        norm = re.sub(r"\bGDY\b", "WHEN", norm, flags=re.IGNORECASE)
        norm = re.sub(r"\bWTEDY\b", "THEN", norm, flags=re.IGNORECASE)
        norm = re.sub(r"\bINACZEJ\b", "ELSE", norm, flags=re.IGNORECASE)
        norm = re.sub(r"\bKONIEC\b", "END", norm, flags=re.IGNORECASE)
        m = re.match(
            r'^CASE\s+(.+?)\s+ELSE\s+("(?:[^"\\]|\\.)*"|[^\s]+)\s+END$',
            norm,
            re.IGNORECASE | re.DOTALL,
        )
        if not m:
            raise SyntaxError(f"Niepoprawne CASE WHEN: {text}")
        body, else_raw = m.group(1).strip(), m.group(2).strip()
        parts: List[Tuple[Any, str]] = []
        rest = body
        while rest:
            wm = re.match(r'^WHEN\s+(.+?)\s+THEN\s+("(?:[^"\\]|\\.)*"|[^\s]+)(?:\s+(.*))?$', rest, re.IGNORECASE | re.DOTALL)
            if not wm:
                raise SyntaxError(f"Oczekiwano WHEN … THEN … w CASE: {rest}")
            cond = self.cond_parser.parse(wm.group(1).strip())
            then_val = wm.group(2).strip().strip('"').strip("'")
            parts.append((cond, then_val))
            rest = (wm.group(3) or "").strip()
        else_val = else_raw.strip('"').strip("'")
        auto_label = label or "CASE"
        return ProjColumn(auto_label, "case", case_parts=tuple(parts), case_else=else_val)

    def _parse_arith_column(self, text: str, label: Optional[str]) -> ProjColumn:
        for op in ("+", "-", "*", "/"):
            depth, in_quote = 0, False
            i = 0
            while i < len(text):
                ch = text[i]
                if ch == '"':
                    in_quote = not in_quote
                elif not in_quote:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    elif ch == op and depth == 0:
                        left = text[:i].strip()
                        right = text[i + 1:].strip()
                        if not left or not right:
                            break
                        return ProjColumn(
                            label or f"{left}{op}{right}",
                            "arith",
                            arith_op=op,
                            arith_left=self._parse_proj_atom(left),
                            arith_right=self._parse_proj_atom(right),
                        )
                i += 1
        raise SyntaxError(f"Niepoprawne wyrażenie arytmetyczne: {text}")

    def _parse_proj_atom(self, raw: str) -> Any:
        raw = raw.strip()
        if raw.startswith('"') and raw.endswith('"'):
            return ProjColumn(raw.strip('"'), "ref", None, raw.strip('"'))
        if "." in raw:
            alias, key = raw.split(".", 1)
            return ProjColumn(key.strip('"'), "ref", alias.strip('"'), key.strip('"'))
        if re.match(r'^"([^"]+)"$', raw):
            key = raw.strip('"')
            return ProjColumn(key, "ref", None, key)
        raise SyntaxError(f"Niepoprawny operand wyrażenia: {raw}")

    def _parse_simple_ref(self, text: str) -> ProjColumn:
        text = text.strip()
        label: Optional[str] = None
        if m := re.search(r'\s+(?:AS|JAKO)\s+"([^"]+)"\s*$', text, re.IGNORECASE):
            label = m.group(1)
            text = text[:m.start()].strip()
        if text.upper().startswith("CASE"):
            return self._parse_case_column(text, label)
        if re.search(r'[*+\-/]', text) and ('"' in text or "." in text):
            return self._parse_arith_column(text, label)
        if text.startswith('"') and text.endswith('"'):
            inner = text[1:-1]
            if "." in inner:
                alias, key = inner.split(".", 1)
                return ProjColumn(label or key, "ref", alias, key)
            return ProjColumn(label or inner, "ref", None, inner)
        if "." in text:
            alias, key = text.split(".", 1)
            key = key.strip('"')
            return ProjColumn(label or key, "ref", alias.strip('"'), key)
        key = text.strip('"')
        return ProjColumn(label or key, "ref", None, key)

    def _parse_projection_columns(self, keys_str: str) -> Tuple[ProjColumn, ...]:
        return tuple(self._parse_simple_ref(item) for item in self._split_projection_items(keys_str))

    def _read_paren_expr(self, text: str) -> Tuple[str, str]:
        if not text.startswith("("):
            raise SyntaxError("Oczekiwano (podzapytanie) po DOŁĄCZ Z")
        depth = 0
        for i, ch in enumerate(text):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return text[1:i], text[i + 1 :].lstrip()
        raise SyntaxError("Niezamknięty nawias w podzapytaniu JOIN")

    def _parse_join_pairs(self, join_body: str) -> Tuple[Tuple[str, str], ...]:
        pairs: List[Tuple[str, str]] = []
        for part in self._split_top_oraz(join_body):
            jm = re.match(r'^"?([^"]+)"?\s*=\s*"?([^"]+)"?$', part.strip())
            if not jm:
                raise SyntaxError(f"JOIN relacyjny wymaga równości cech: {part}")
            pairs.append((jm.group(1).strip(), jm.group(2).strip()))
        return tuple(pairs)

    def _extract_all_rel_joins(self, conds_str: str) -> Tuple[str, Tuple[RelJoinSpec, ...]]:
        joins: List[RelJoinSpec] = []
        text = conds_str.strip()
        while text:
            m = re.search(r'\s+(LEWY\s+)?DOŁĄCZ\s+Z\s+', text, re.IGNORECASE)
            if not m:
                break
            left_outer = bool(m.group(1))
            head = text[:m.start()].strip()
            tail = text[m.end():].lstrip()
            subquery_node = None
            alias = None
            if tail.startswith("("):
                inner, tail = self._read_paren_expr(tail)
                subquery_node = self._parse_subquery_atom(inner)
                if tail.upper().startswith("JAKO "):
                    am = re.match(r'^JAKO\s+"([^"]+)"\s+', tail, re.IGNORECASE)
                    if not am:
                        raise SyntaxError("Oczekiwano JAKO \"alias\" po podzapytaniu JOIN")
                    alias = am.group(1)
                    tail = tail[am.end():].lstrip()
                else:
                    alias = "Prawa"
            else:
                am = re.match(r'^"([^"]+)"\s+', tail)
                if not am:
                    raise SyntaxError('Oczekiwano "alias" lub (podzapytanie) po DOŁĄCZ Z')
                alias = am.group(1)
                tail = tail[am.end():].lstrip()
            gm = re.match(r'^GDZIE\s+(.+)$', tail, re.IGNORECASE)
            if not gm:
                raise SyntaxError("Oczekiwano GDZIE po DOŁĄCZ Z …")
            joins.insert(0, RelJoinSpec(
                alias, self._parse_join_pairs(gm.group(1).strip()), left_outer, subquery_node,
            ))
            text = head
        return text, tuple(joins)

    def _parse_prop_pairs(self, text: str) -> List[Tuple[str, str]]:
        pairs: List[Tuple[str, str]] = []
        for m in re.finditer(r'"([^"]+)"\s*(?:->|=)\s*("(?:[^"\\]|\\.)*"|[^,]+)', text):
            pairs.append((m.group(1), m.group(2).strip()))
        if not pairs:
            raise SyntaxError(f"Brak par cecha=wartość w: {text}")
        return pairs

    def _split_top_oraz(self, text: str) -> List[str]:
        return ConditionParser()._split_top(text, "ORAZ")

    def _parse_subquery_atom(self, text: str) -> ASTNode:
        text = text.strip()
        if not text:
            raise SyntaxError("Puste podzapytanie")
        if self._contains_set_op(text):
            node = self._parse_set_union(text)
        else:
            node = self._parse_simple_line(text)
        if node is None:
            raise SyntaxError(f"Nierozpoznane podzapytanie: '{text}'")
        if isinstance(node, ConditionNode) and node.action != "ZNAJDŹ":
            raise SyntaxError("Podzapytanie musi zwracać listę wyników (użyj ZNAJDŹ, WYPISZ, SZUKAJ lub ZŁĄCZ)")
        if isinstance(node, (CreateBubbleNode, UpdatePropertyNode, UpdateWhereNode, AddPropertyNode,
                              BulkInjectNode, BulkCreateNode, InsertFromNode,
                              ExportCsvNode, ImportCsvNode, MergeNode, DescribeDatabaseNode,
                              DeleteBubbleNode, RemovePropertyNode, ConnectNode, DisconnectNode,
                              ExciteNode, BeginTxNode, CommitTxNode, RollbackTxNode, AssignNode)):
            raise SyntaxError("Podzapytanie nie może modyfikować danych")
        return node

    def _parse_insert_subquery(self, text: str) -> ASTNode:
        node = self._parse_subquery_atom(text)
        if isinstance(node, ConditionNode) and node.action != "ZNAJDŹ":
            raise SyntaxError("WSTAW Z wymaga podzapytania ZNAJDŹ lub WYPISZ (nie USUŃ/POLICZ)")
        return node

    def _extract_modifiers(self, conds_str: str) -> Tuple[str, Optional[int], Optional[int], Optional[str], bool, Optional[str], Optional[str], Optional[str], Optional[str], bool]:
        limit, offset, sort_by, sort_desc = None, None, None, False
        group_by, having_action, having_op, having_val = None, None, None, None
        distinct = False

        if m := re.search(r'\s+UNIKALNE\s*$', conds_str, re.IGNORECASE):
            distinct = True
            conds_str = conds_str[:m.start()] + conds_str[m.end():]

        if m := re.search(r'\s+POGRUPUJ\s+((?:"[^"]+")(?:\s*,\s*"[^"]+")*)', conds_str, re.IGNORECASE):
            group_by = self._parse_keys(m.group(1))
            conds_str = conds_str[:m.start()] + conds_str[m.end():]
        if m := re.search(r'\s+MAJĄCE\s+(SUMA|ŚREDNIA|MIN|MAX|POLICZ(?:\s+RÓŻNE)?)\s*(!=|>=|<=|=|>|<)\s*(.+)$', conds_str, re.IGNORECASE):
            having_action = re.sub(r"\s+", " ", m.group(1).upper()).strip()
            having_op = m.group(2)
            having_val = m.group(3).strip().strip('"').strip("'")
            conds_str = conds_str[:m.start()] + conds_str[m.end():]

        if m := re.search(r'\s+LIMIT\s+(\d+)', conds_str, re.IGNORECASE):
            limit = int(m.group(1))
            conds_str = conds_str[:m.start()] + conds_str[m.end():]
        if m := re.search(r'\s+PRZESUNIĘCIE\s+(\d+)', conds_str, re.IGNORECASE):
            offset = int(m.group(1))
            conds_str = conds_str[:m.start()] + conds_str[m.end():]
        if m := re.search(r'\s+SORTUJ\s+WEDŁUG\s+"([^"]+)"(?:\s+(ROSNĄCO|MALEJĄCO))?', conds_str, re.IGNORECASE):
            sort_by = m.group(1)
            sort_desc = bool(m.group(2) and m.group(2).upper() == "MALEJĄCO")
            conds_str = conds_str[:m.start()] + conds_str[m.end():]
        return conds_str.strip(), limit, offset, sort_by, sort_desc, group_by, having_action, having_op, having_val, distinct

    def _split_top_set_op(self, text: str, kw: str) -> List[str]:
        kw_upper = f" {kw.upper()} "
        parts: List[str] = []
        start = 0
        depth = 0
        in_quote = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '"':
                in_quote = not in_quote
            elif not in_quote:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif depth == 0 and text[i:].upper().startswith(kw_upper):
                    parts.append(text[start:i].strip())
                    i += len(kw_upper)
                    start = i
                    continue
            i += 1
        parts.append(text[start:].strip())
        return [p for p in parts if p]

    def _extract_trailing_modifiers(self, line: str) -> Tuple[str, Optional[str], bool, Optional[int], Optional[int]]:
        sort_by, sort_desc, limit, offset = None, False, None, None
        if m := re.search(r'\s+LIMIT\s+(\d+)\s*$', line, re.IGNORECASE):
            limit = int(m.group(1))
            line = line[:m.start()] + line[m.end():]
        if m := re.search(r'\s+PRZESUNIĘCIE\s+(\d+)\s*$', line, re.IGNORECASE):
            offset = int(m.group(1))
            line = line[:m.start()] + line[m.end():]
        if m := re.search(r'\s+SORTUJ\s+WEDŁUG\s+"([^"]+)"(?:\s+(ROSNĄCO|MALEJĄCO))?\s*$', line, re.IGNORECASE):
            sort_by = m.group(1)
            sort_desc = bool(m.group(2) and m.group(2).upper() == "MALEJĄCO")
            line = line[:m.start()] + line[m.end():]
        return line.strip(), sort_by, sort_desc, limit, offset

    def _contains_set_op(self, text: str) -> bool:
        upper = f" {text.upper()} "
        for op in SET_OPS:
            if f" {op} " in upper:
                return True
        return False

    def _parse_query_atom(self, text: str) -> ASTNode:
        text = text.strip()
        if m := re.match(r'^\$([a-zA-Z0-9_]+)$', text):
            return VarRefNode(m.group(1))
        node = self._parse_simple_line(text)
        if node is None:
            raise SyntaxError(f"Nierozpoznane zapytanie w operacji zbiorów: '{text}'")
        if isinstance(node, (SetOpNode, AssignNode)):
            raise SyntaxError(f"Zagnieżdżone operacje zbiorów wymagają nawiasów: '{text}'")
        return node

    def _parse_set_intersect(self, text: str) -> ASTNode:
        parts = self._split_top_set_op(text, SET_INTERSECT_OP)
        if len(parts) == 1:
            return self._parse_query_atom(parts[0])
        node = self._parse_query_atom(parts[0])
        for part in parts[1:]:
            node = SetOpNode(SET_INTERSECT_OP, node, self._parse_query_atom(part))
        return node

    def _parse_set_union(self, text: str) -> ASTNode:
        text = text.strip()
        for op in SET_UNION_OPS:
            parts = self._split_top_set_op(text, op)
            if len(parts) > 1:
                node = self._parse_set_intersect(parts[0])
                for part in parts[1:]:
                    node = SetOpNode(op.upper(), node, self._parse_set_intersect(part))
                return node
        return self._parse_set_intersect(text)

    def _attach_set_modifiers(self, node: ASTNode, sort_by, sort_desc, limit, offset) -> ASTNode:
        if sort_by is None and limit is None and offset is None:
            return node
        if isinstance(node, SetOpNode):
            return SetOpNode(node.op, node.left, node.right, sort_by, sort_desc, limit, offset)
        return SetOpNode("", node, node, sort_by, sort_desc, limit, offset)

    def _parse_simple_line(self, line: str) -> Optional[ASTNode]:
        if match := self.var_set_pattern.match(line):
            op = match.group(1).upper()
            return SetOpNode(op, VarRefNode(match.group(2)[1:]), VarRefNode(match.group(3)[1:]))

        if m := re.match(r'^WSTAW\s+Z\s+', line, re.IGNORECASE):
            rest = line[m.end():].strip()
            if not rest.startswith("("):
                raise SyntaxError("Oczekiwano WSTAW Z (podzapytanie)")
            inner = self.cond_parser._unwrap_parens(rest)
            return InsertFromNode(self._parse_insert_subquery(inner))

        if m := re.match(r'^EKSPORT\s+CSV\s+"([^"]+)"\s+Z\s+', line, re.IGNORECASE):
            rest = line[m.end():].strip()
            if not rest.startswith("("):
                raise SyntaxError('Oczekiwano EKSPORT CSV "plik" Z (podzapytanie)')
            inner = self.cond_parser._unwrap_parens(rest)
            return ExportCsvNode(m.group(1), self._parse_insert_subquery(inner))

        if m := re.match(r'^IMPORT\s+CSV\s+"([^"]+)"(?:\s+KOLUMNA\s+"([^"]+)")?\s*$', line, re.IGNORECASE):
            return ImportCsvNode(m.group(1), m.group(2))

        if re.match(r'^OPISZ\s+BAZĘ\s*$', line, re.IGNORECASE):
            return DescribeDatabaseNode()

        if m := re.match(r'^SCAL\s+"([^"]+)"\s+Z\s+(.+)$', line, re.IGNORECASE):
            return MergeNode(m.group(1), None, None, tuple(self._parse_prop_pairs(m.group(2))))

        if m := re.match(r'^SCAL\s+PO\s+"([^"]+)"\s*=\s*(.+?)\s+Z\s+(.+)$', line, re.IGNORECASE):
            return MergeNode(None, m.group(1), m.group(2).strip(), tuple(self._parse_prop_pairs(m.group(3))))

        if match := self.patterns['wstrzyknij_wiele'].match(line):
            return BulkInjectNode(match.group(2), tuple(self._parse_prop_pairs(match.group(1))))

        if match := self.patterns['utrwal_wiele'].match(line):
            body = match.group(1).strip()
            entries: List[Tuple[str, Tuple[Tuple[str, str], ...]]] = []
            if re.search(r'\s+Z\s+', body, re.IGNORECASE):
                for part in self._split_top_oraz(body):
                    m = re.match(r'^"([^"]+)"\s+Z\s+(.+)$', part.strip(), re.IGNORECASE)
                    if not m:
                        raise SyntaxError(f"Oczekiwano '\"Bąbel\" Z \"Cecha\" = Wartość, ...' w: {part}")
                    entries.append((m.group(1), tuple(self._parse_prop_pairs(m.group(2)))))
            else:
                for name in self._parse_keys(body):
                    entries.append((name, ()))
            return BulkCreateNode(tuple(entries))

        if match := self.wypisz_gdzie_pattern.match(line):
            columns = self._parse_projection_columns(match.group(1))
            join_rel, join_tgt = match.group(2), match.group(3)
            conds_str, limit, offset, sort_by, sort_desc, _, _, _, _, distinct = self._extract_modifiers(match.group(4))
            conds_str, rel_joins = self._extract_all_rel_joins(conds_str)
            if re.match(r'^WYPISZ\s+UNIKALNE\s+', line, re.IGNORECASE):
                distinct = True
            return ProjectWhereNode(
                columns, self.cond_parser.parse(conds_str), join_rel, join_tgt,
                rel_joins, distinct, sort_by, sort_desc, limit, offset,
            )

        if match := self.zaktualizuj_gdzie_pattern.match(line):
            key, value = match.group(1), match.group(2).strip()
            join_rel, join_tgt = match.group(3), match.group(4)
            conds_str, _, _, _, _, _, _, _, _, _ = self._extract_modifiers(match.group(5))
            return UpdateWhereNode(key, value, self.cond_parser.parse(conds_str), join_rel, join_tgt)

        if match := self.znajdz_rel_gdzie_pattern.match(line):
            conds_str, limit, offset, sort_by, sort_desc, _, _, _, _, _ = self._extract_modifiers(match.group(3))
            return FindRelationNode(match.group(1), match.group(2), self.cond_parser.parse(conds_str), sort_by, sort_desc, limit, offset)

        for key in self.patterns:
            if key in ('wypisz', 'zaktualizuj', 'wstrzyknij_wiele', 'utrwal_wiele'): continue
            if match := self.patterns[key].match(line):
                if key == 'utrwal_przestrzen': return CreateNamespaceNode(match.group(1))
                if key == 'wybierz_przestrzen': return UseNamespaceNode(match.group(1))
                if key == 'utrwal': return CreateBubbleNode(match.group(1))
                if key == 'wstrzyknij': return AddPropertyNode(match.group(3), match.group(1), match.group(2))
                if key == 'usun_atrybut': return RemovePropertyNode(match.group(2), match.group(1))
                if key == 'usun_babel': return DeleteBubbleNode(match.group(1))
                if key == 'polacz': return ConnectNode(match.group(1), match.group(2), match.group(3))
                if key == 'rozlacz': return DisconnectNode(match.group(1), match.group(2), match.group(3))
                if key == 'pokaz': return ShowBubbleNode(match.group(1))
                if key == 'historia': return ShowHistoryNode(match.group(2), match.group(1))
                if key == 'szukaj': return SearchNode(match.group(1))
                if key == 'wzbudz': return ExciteNode(match.group(1), float(match.group(2)), match.group(3))
                if key == 'begin': return BeginTxNode()
                if key == 'commit': return CommitTxNode()
                if key == 'rollback': return RollbackTxNode()

        if match := self.patterns['zaktualizuj'].match(line):
            return UpdatePropertyNode(match.group(3), match.group(1), match.group(2))

        if match := self.patterns['wypisz'].match(line):
            return ProjectNode(match.group(2), self._parse_keys(match.group(1)))

        if match := self.agg_pattern.match(line):
            action = re.sub(r"\s+", " ", match.group(1).upper()).strip()
            agg_key = match.group(2)
            join_rel, join_tgt = match.group(3), match.group(4)
            conds_str, limit, offset, sort_by, sort_desc, group_by, having_action, having_op, having_val, _ = self._extract_modifiers(match.group(5))
            return AggregateNode(action, agg_key, self.cond_parser.parse(conds_str), join_rel, join_tgt,
                                 sort_by, sort_desc, limit, offset, group_by, having_action, having_op, having_val)

        if match := self.cond_pattern.match(line):
            action = match.group(1).upper().split()[0]
            join_rel, join_tgt = match.group(2), match.group(3)
            conds_str, limit, offset, sort_by, sort_desc, _, _, _, _, _ = self._extract_modifiers(match.group(4))
            return ConditionNode(action, self.cond_parser.parse(conds_str), join_rel, join_tgt, sort_by, sort_desc, limit, offset)

        if match := self.znajdz_rel_pattern.match(line):
            return FindRelationNode(match.group(1), match.group(2))

        return None

    def _parse_line(self, line: str) -> Optional[ASTNode]:
        if match := self.assign_pattern.match(line):
            var_name = match.group(1)
            expr_str = match.group(2)
            expr_node = self._parse_line(expr_str)
            if not expr_node: raise SyntaxError(f"Błędne zapytanie przypisywane do zmiennej ${var_name}")
            return AssignNode(var_name, expr_node)

        body, sort_by, sort_desc, limit, offset = self._extract_trailing_modifiers(line)
        if self.var_set_pattern.match(body):
            node = self._parse_simple_line(body)
            if node:
                return self._attach_set_modifiers(node, sort_by, sort_desc, limit, offset)
        if self._contains_set_op(body):
            node = self._parse_set_union(body)
            if node:
                return self._attach_set_modifiers(node, sort_by, sort_desc, limit, offset)

        return self._parse_simple_line(line)


# ─── 5. SUBSTRATE API ────────────────────────────────────────────────────

class BubbleAlreadyExistsError(Exception): pass

class SubstrateAPI:
    def __init__(self, store):
        self.store = store
        self.namespaces = {"DEFAULT": {"bubbles": {}, "inv_index": {}, "atom_index": {}}}
        self.active_ns = "DEFAULT"
        self._backup_state = None

    @property
    def _bubble_index(self): return self.namespaces[self.active_ns]["bubbles"]

    @property
    def _inv_index(self): return self.namespaces[self.active_ns]["inv_index"]

    @property
    def _atom_index(self): return self.namespaces[self.active_ns]["atom_index"]

    def create_namespace(self, name: str):
        if name in self.namespaces: raise ValueError(f"Przestrzeń '{name}' już istnieje.")
        self.namespaces[name] = {"bubbles": {}, "inv_index": {}, "atom_index": {}}

    def use_namespace(self, name: str):
        if name not in self.namespaces: raise ValueError(f"Przestrzeń '{name}' nie istnieje. Utrwal ją najpierw.")
        self.active_ns = name

    def begin_transaction(self):
        reg_atoms = self.store.reg._atoms
        self._backup_state = {
            "ns": self.active_ns,
            "bubble_index": dict(self._bubble_index),
            "bindings": {name: b.bindings.copy() for name, b in self._bubble_index.items()},
            "atoms": dict(reg_atoms),
            "atom_T": {aid: a.T for aid, a in reg_atoms.items()},
            "roots": list(self.store.roots),
            "bubbles": list(self.store.bubbles),
            "inv_index": {k: {v: s.copy() for v, s in vals.items()}
                          for k, vals in self._inv_index.items()},
            "atom_index": {aid: s.copy() for aid, s in self._atom_index.items()},
        }

    def commit_transaction(self):
        self._backup_state = None

    def rollback_transaction(self):
        if self._backup_state is None: return
        bs = self._backup_state
        self.active_ns = bs["ns"]

        idx = self._bubble_index
        idx.clear()
        idx.update(bs["bubble_index"])

        for name, b in idx.items():
            if name in bs["bindings"]:
                b.bindings = bs["bindings"][name]

        self.store.reg._atoms = dict(bs["atoms"])
        self.store.reg._generation += 1

        for aid, T in bs["atom_T"].items():
            a = self.store.reg._atoms.get(aid)
            if a is not None:
                a.T = T
                a._update_state()

        self.store.roots[:] = bs["roots"]
        self.store.bubbles[:] = bs["bubbles"]
        self.namespaces[self.active_ns]["inv_index"] = bs["inv_index"]
        self.namespaces[self.active_ns]["atom_index"] = bs["atom_index"]
        self.commit_transaction()

    def _get_bubble(self, name: str):
        if name not in self._bubble_index: raise ValueError(f"Bąbel '{name}' nie istnieje w przestrzeni '{self.active_ns}'.")
        return self._bubble_index[name]

    def _update_index(self, bubble_name: str, key: str, value: Any, add: bool = True):
        val_str = KarminType.to_str(value)
        if key not in self._inv_index:
            self._inv_index[key] = {}
        if val_str not in self._inv_index[key]:
            self._inv_index[key][val_str] = set()
        if add:
            self._inv_index[key][val_str].add(bubble_name)
        else:
            self._inv_index[key][val_str].discard(bubble_name)

    def _track_atom(self, bubble_name: str, atom_id: str, add: bool = True):
        if not atom_id:
            return
        if add:
            self._atom_index.setdefault(atom_id, set()).add(bubble_name)
        else:
            if atom_id in self._atom_index:
                self._atom_index[atom_id].discard(bubble_name)
                if not self._atom_index[atom_id]:
                    del self._atom_index[atom_id]

    def _indexed_equals(self, cmp: CondCompare, universe: Set[str]) -> Optional[Set[str]]:
        if cmp.subquery is not None or str(cmp.val).startswith("$"):
            return None
        if cmp.op != "=":
            return None
        if cmp.key.upper() in ("BĄBEL", "TEMPERATURA"):
            return None
        if cmp.key not in self._inv_index:
            return None
        val_str = KarminType.to_str(KarminType.parse(cmp.val))
        bucket = self._inv_index[cmp.key].get(val_str)
        if bucket is None:
            return set()
        return set(bucket) & universe

    def create_bubble(self, name: str):
        if name in self._bubble_index: raise BubbleAlreadyExistsError(f"Bąbel '{name}' już istnieje w '{self.active_ns}'.")
        bubble = self.store.bubble_new(label=name)
        self.store.set_root(bubble)
        self._bubble_index[name] = bubble

    @staticmethod
    def _value_raw(val: Any) -> str:
        if isinstance(val, str) and val.upper() in ("PRAWDA", "FAŁSZ", "NIC"):
            return val.upper()
        if isinstance(val, bool):
            return "PRAWDA" if val else "FAŁSZ"
        if val is None:
            return "NIC"
        if isinstance(val, (int, float)):
            return str(val)
        return f'"{val}"'

    def _allocate_copy_name(self, base: str) -> str:
        candidate = f"{base}_kopia"
        n = 2
        while candidate in self._bubble_index:
            candidate = f"{base}_kopia_{n}"
            n += 1
        return candidate

    def insert_bubble_with_props(self, name: str, props: dict) -> None:
        self.create_bubble(name)
        for key, val in props.items():
            if val is not None:
                self.add_property(name, key, self._value_raw(val))

    def clone_bubble(self, source_name: str, new_name: Optional[str] = None) -> str:
        contents = self.get_contents(source_name)
        target = new_name or self._allocate_copy_name(source_name)
        self.insert_bubble_with_props(target, contents["properties"])
        for rel in contents["relations"]:
            self.connect_bubbles(target, rel["target"], rel["relation"])
        return target

    def insert_from_rows(self, columns: List[str], rows: List[dict]) -> List[str]:
        if not columns:
            raise ValueError("WSTAW Z (WYPISZ …) wymaga co najmniej jednej kolumny (nazwa bąbla)")
        name_key = columns[0]
        prop_keys = columns[1:]
        created = []
        for row in rows:
            name = row.get(name_key)
            if name is None or name == "NIC":
                continue
            name = str(name)
            props = {k: row.get(k) for k in prop_keys if row.get(k) is not None}
            self.insert_bubble_with_props(name, props)
            created.append(name)
        return created

    @staticmethod
    def _parse_csv_value(raw: str) -> Any:
        s = (raw or "").strip()
        if not s:
            return None
        if s.upper() in ("NIC", "NULL", ""):
            return None
        if s.upper() == "PRAWDA":
            return True
        if s.upper() == "FAŁSZ":
            return False
        try:
            if "." in s:
                return float(s)
            return int(s)
        except ValueError:
            return s

    def write_csv(self, path: str, columns: List[str], rows: List[dict]) -> int:
        if not columns:
            raise ValueError("Brak kolumn do eksportu CSV")
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({c: "" if row.get(c) is None else row.get(c) for c in columns})
        return len(rows)

    def import_csv(self, path: str, name_column: Optional[str] = None) -> List[str]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Plik CSV nie istnieje: {path}")
        created: List[str] = []
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                raise ValueError("CSV bez nagłówka kolumn")
            fields = [f.strip() for f in reader.fieldnames]
            name_key = name_column or fields[0]
            if name_key not in fields:
                raise ValueError(f"Kolumna nazwy '{name_key}' nie występuje w CSV ({', '.join(fields)})")
            prop_keys = [f for f in fields if f != name_key]
            for row in reader:
                name = (row.get(name_key) or "").strip()
                if not name:
                    continue
                props = {}
                for k in prop_keys:
                    val = self._parse_csv_value(row.get(k, ""))
                    if val is not None:
                        props[k] = val
                self.insert_bubble_with_props(name, props)
                created.append(name)
        return created

    def describe_database(self) -> dict:
        saved_ns = self.active_ns
        all_namespaces: List[dict] = []
        try:
            for ns_name, ns_data in self.namespaces.items():
                self.active_ns = ns_name
                bubbles_out: List[dict] = []
                prop_index: dict = {}
                rel_total = 0
                for name in sorted(ns_data["bubbles"].keys()):
                    contents = self.get_contents(name)
                    props = sorted(contents["properties"].keys())
                    rels = contents["relations"]
                    rel_total += len(rels)
                    bubbles_out.append({
                        "name": name,
                        "properties": props,
                        "property_count": len(props),
                        "relations": rels,
                    })
                    for prop, val in contents["properties"].items():
                        entry = prop_index.setdefault(prop, {"bubbles": 0, "samples": []})
                        entry["bubbles"] += 1
                        sval = KarminType.to_str(val)
                        if len(entry["samples"]) < 3 and sval not in entry["samples"]:
                            entry["samples"].append(sval)
                all_namespaces.append({
                    "name": ns_name,
                    "bubble_count": len(ns_data["bubbles"]),
                    "relation_count": rel_total,
                    "bubbles": bubbles_out,
                    "properties": prop_index,
                })
        finally:
            self.active_ns = saved_ns

        current = next(n for n in all_namespaces if n["name"] == saved_ns)
        catalog_rows = []
        for b in current["bubbles"]:
            catalog_rows.append({
                "BĄBEL": b["name"],
                "CECHY": ", ".join(b["properties"]),
                "RELACJE": len(b["relations"]),
            })
        return {
            "active_namespace": saved_ns,
            "namespaces": [n["name"] for n in all_namespaces],
            "bubble_count": current["bubble_count"],
            "bubbles": current["bubbles"],
            "properties": current["properties"],
            "catalog_rows": catalog_rows,
            "all_namespaces": all_namespaces,
        }

    def merge_bubble(self, bubble_name: str, props: List[Tuple[str, str]]) -> Tuple[str, str]:
        action = "updated" if bubble_name in self._bubble_index else "created"
        if action == "created":
            self.create_bubble(bubble_name)
        for key, value in props:
            if key in self._bubble_index[bubble_name].bindings:
                self.update_property(bubble_name, key, value)
            else:
                self.add_property(bubble_name, key, value)
        return bubble_name, action

    def merge_by_key(self, key: str, value_raw: str, props: List[Tuple[str, str]]) -> Tuple[str, str]:
        val = KarminType.parse(value_raw)
        val_str = KarminType.to_str(val)
        candidates = sorted(self._inv_index.get(key, {}).get(val_str, set()))
        if candidates:
            name = candidates[0]
            for pkey, pval in props:
                if pkey == key:
                    continue
                if pkey in self._bubble_index[name].bindings:
                    self.update_property(name, pkey, pval)
                else:
                    self.add_property(name, pkey, pval)
            return name, "updated"
        base_name = str(val) if val is not None else f"{key}_nowy"
        new_name = base_name
        n = 2
        while new_name in self._bubble_index:
            new_name = f"{base_name}_{n}"
            n += 1
        all_props = [(key, value_raw)] + [(k, v) for k, v in props if k != key]
        self.insert_bubble_with_props(new_name, {k: KarminType.parse(v) for k, v in all_props})
        return new_name, "created"

    def add_property(self, bubble_name: str, key: str, value_raw: str):
        bubble = self._get_bubble(bubble_name)
        val = KarminType.parse(value_raw)
        atom = self.store.atom_new(S=key, E=KarminType.to_str(val), value=KarminType.to_str(val))
        atom.metadata.update({'timestamp': int(time.time() * 1000), 'v': val})
        bubble.bind(key, atom)
        self._update_index(bubble_name, key, val, add=True)
        self._track_atom(bubble_name, atom.id, add=True)

    def update_property(self, bubble_name: str, key: str, value_raw: str):
        bubble = self._get_bubble(bubble_name)
        old_atom_id = bubble.bindings.get(key)
        old_val = None
        if old_atom_id:
            self._track_atom(bubble_name, old_atom_id, add=False)
            old_atom = self.store.get_atom(old_atom_id)
            if old_atom:
                old_val = old_atom.metadata.get('v')
                self._update_index(bubble_name, key, old_val, add=False)
                ts = old_atom.metadata.get('timestamp', int(time.time() * 1000))
                bubble.bind(f"hist:{key}:{ts}", old_atom)

        new_val = KarminType.parse(value_raw)
        if isinstance(new_val, (int, float)) and isinstance(old_val, (int, float)) and str(value_raw).startswith(("+", "-")):
            new_val = old_val + new_val

        atom = self.store.atom_new(S=key, E=KarminType.to_str(new_val), value=KarminType.to_str(new_val))
        atom.metadata.update({'timestamp': int(time.time() * 1000), 'v': new_val})
        bubble.bind(key, atom)
        self._update_index(bubble_name, key, new_val, add=True)
        self._track_atom(bubble_name, atom.id, add=True)

    def remove_property(self, bubble_name: str, key: str):
        bubble = self._get_bubble(bubble_name)
        if key not in bubble.bindings: raise KeyError(f"Brak cechy '{key}'")
        old_atom_id = bubble.bindings.get(key)
        if old_atom_id:
            self._track_atom(bubble_name, old_atom_id, add=False)
            old_atom = self.store.get_atom(old_atom_id)
            if old_atom:
                self._update_index(bubble_name, key, old_atom.metadata.get('v'), add=False)
                ts = old_atom.metadata.get('timestamp', int(time.time() * 1000))
                bubble.bind(f"hist:{key}:{ts}_deleted", old_atom)
        bubble.bindings.pop(key, None)

    def connect_bubbles(self, source_name: str, target_name: str, relation: str):
        source_bubble = self._get_bubble(source_name)
        self._get_bubble(target_name)
        bind_key = f"rel:{relation}:{target_name}"
        atom = self.store.atom_new(S=relation, E=target_name, value=target_name)
        source_bubble.bind(bind_key, atom)

    def disconnect_bubbles(self, source_name: str, target_name: str, relation: str):
        source_bubble = self._get_bubble(source_name)
        bind_key = f"rel:{relation}:{target_name}"
        if bind_key not in source_bubble.bindings: raise KeyError(f"Brak relacji")
        source_bubble.bindings.pop(bind_key, None)

    def delete_bubble(self, bubble_name: str):
        bubble = self._get_bubble(bubble_name)
        for key, atom_id in bubble.bindings.items():
            if not key.startswith("hist:") and not key.startswith("rel:"):
                self._track_atom(bubble_name, atom_id, add=False)
                atom = self.store.get_atom(atom_id)
                if atom:
                    self._update_index(bubble_name, key, atom.metadata.get('v'), add=False)
        self.store.unset_root(bubble)
        del self._bubble_index[bubble_name]

    def get_contents(self, bubble_name: str) -> dict:
        bubble = self._get_bubble(bubble_name)
        props, rels = {}, []
        for name in list(bubble.bindings.keys()):
            if name.startswith("hist:"): continue
            atom = bubble.lookup(name)
            if atom:
                if name.startswith("rel:"):
                    parts = name.split(":", 2)
                    rels.append({"relation": parts[1], "target": parts[2]})
                else: props[name] = atom.metadata.get('v', atom.E)
        return {"properties": props, "relations": rels}

    def project_contents(self, bubble_name: str, keys: List[str]) -> dict:
        return self._bubble_row(bubble_name, keys)

    def _bubble_row(self, bubble_name: str, keys: List[str]) -> dict:
        return self._project_row(bubble_name, None, tuple(
            ProjColumn(k, "ref", None, k if k.upper() != "BĄBEL" else "BĄBEL") for k in keys
        ))

    @staticmethod
    def _split_qualified(prop: str, default_alias: Optional[str]) -> Tuple[Optional[str], str]:
        if "." in prop:
            alias, key = prop.split(".", 1)
            return alias.strip(), key.strip()
        return default_alias, prop.strip()

    def _resolve_alias_bubble(self, alias: Optional[str], left_name: str,
                              join_ctx: dict) -> Optional[str]:
        if not alias:
            return left_name
        return join_ctx.get(alias)

    def _value_from_ctx(self, alias: Optional[str], key: str,
                        left_name: str, join_ctx: dict) -> Any:
        if key.upper() == "BĄBEL":
            target = self._resolve_alias_bubble(alias, left_name, join_ctx)
            return target
        target = self._resolve_alias_bubble(alias, left_name, join_ctx)
        if not target:
            return None
        return self._get_property_value(target, key)

    def _eval_proj_column(self, col: ProjColumn, left_name: str, join_ctx: dict) -> Any:
        if col.kind == "ref":
            return self._value_from_ctx(col.ref_alias, col.ref_key or col.label, left_name, join_ctx)
        if col.kind == "arith":
            left = self._eval_proj_column(col.arith_left, left_name, join_ctx)
            right = self._eval_proj_column(col.arith_right, left_name, join_ctx)
            if left is None or right is None:
                return None
            if col.arith_op == "+":
                return left + right
            if col.arith_op == "-":
                return left - right
            if col.arith_op == "*":
                return left * right
            if col.arith_op == "/":
                return left / right if right != 0 else None
            return None
        if col.kind == "case" and col.case_parts:
            for cond, then_val in col.case_parts:
                if left_name in self._eval_cond_expr(cond, {}, {left_name}, None):
                    return KarminType.parse(
                        f'"{then_val}"' if not then_val.replace(".", "").isdigit() else then_val
                    )
            if col.case_else is not None:
                return KarminType.parse(
                    f'"{col.case_else}"' if not str(col.case_else).replace(".", "").isdigit() else col.case_else
                )
        return None

    def _project_row(self, left_name: str, join_ctx: dict,
                     columns: Tuple[ProjColumn, ...]) -> dict:
        return {col.label: self._eval_proj_column(col, left_name, join_ctx) for col in columns}

    def _join_pair_values(self, left_name: str, right_name: str, join_ctx: dict,
                          l_prop: str, r_prop: str, join_alias: str) -> Tuple[Any, Any]:
        l_alias, l_key = self._split_qualified(l_prop, None)
        r_alias, r_key = self._split_qualified(r_prop, join_alias)
        l_val = self._value_from_ctx(l_alias, l_key, left_name, join_ctx)
        r_val = self._get_property_value(right_name, r_key)
        return l_val, r_val

    def _rel_join_match(self, left_name: str, right_name: str, join_ctx: dict,
                        pairs: Tuple[Tuple[str, str], ...], join_alias: str) -> bool:
        if left_name == right_name:
            return False
        for left_prop, right_prop in pairs:
            l_val, r_val = self._join_pair_values(
                left_name, right_name, join_ctx, left_prop, right_prop, join_alias,
            )
            if l_val != r_val:
                return False
        return True

    def _build_join_hash(self, candidates: Set[str], pairs: Tuple[Tuple[str, str], ...],
                         join_alias: str) -> Optional[dict]:
        if len(pairs) != 1:
            return None
        _, r_key = self._split_qualified(pairs[0][1], join_alias)
        _, l_key = self._split_qualified(pairs[0][0], None)
        lookup: dict = {}
        for name in candidates:
            val = self._get_property_value(name, r_key)
            if val is not None:
                lookup.setdefault(val, []).append(name)
        return {"l_key": l_key, "r_key": r_key, "lookup": lookup}

    def _find_join_matches(self, left_name: str, join_ctx: dict, spec: RelJoinSpec,
                           candidates: Set[str], join_hash: Optional[dict]) -> List[str]:
        if join_hash:
            l_val = self._value_from_ctx(None, join_hash["l_key"], left_name, join_ctx)
            if l_val is None:
                return []
            return [n for n in join_hash["lookup"].get(l_val, []) if n != left_name]
        return [
            n for n in sorted(candidates)
            if self._rel_join_match(left_name, n, join_ctx, spec.pairs, spec.alias)
        ]

    def get_history(self, bubble_name: str, key: str) -> list:
        bubble = self._get_bubble(bubble_name)
        history = []
        prefix = f"hist:{key}:"
        for bind_key, atom_id in bubble.bindings.items():
            if bind_key.startswith(prefix):
                atom = self.store.get_atom(atom_id)
                if atom:
                    history.append({
                        "status": "DELETED" if bind_key.endswith("_deleted") else "ARCHIVE",
                        "value": atom.metadata.get('v', atom.E),
                        "timestamp": atom.metadata.get('timestamp', 0)
                    })
        current_atom_id = bubble.bindings.get(key)
        if current_atom_id:
            current_atom = self.store.get_atom(current_atom_id)
            if current_atom:
                history.append({
                    "status": "CURRENT",
                    "value": current_atom.metadata.get('v', current_atom.E),
                    "timestamp": current_atom.metadata.get('timestamp', int(time.time()*1000))
                })
        return sorted(history, key=lambda x: x["timestamp"])

    def search_resonance(self, query: str) -> list:
        hits = self.store.resonance(query, k=5, threshold=0.1)
        found: Set[str] = set()
        for _, atom_id in hits:
            found |= self._atom_index.get(atom_id, set())
        return sorted(found)

    def find_relation(self, relation: str, target: str) -> list:
        return sorted([name for name, b in self._bubble_index.items() if f"rel:{relation}:{target}" in b.bindings])

    def _resolve_universe(self, join_relation: Optional[str], join_target: Optional[str]) -> Set[str]:
        if join_relation and join_target:
            return set(self.find_relation(join_relation, join_target))
        return set(self._bubble_index.keys())

    def propagate_energy(self, start_bubble: str, initial_energy: float, relation_filter: Optional[str] = None):
        if start_bubble not in self._bubble_index:
            raise ValueError(f"Bąbel startowy '{start_bubble}' nie istnieje.")

        queue = [(start_bubble, initial_energy)]
        visited = {}
        decay = 0.5

        while queue:
            current_bubble, energy = queue.pop(0)
            if energy < 1.0: continue
            if visited.get(current_bubble, -1.0) >= energy: continue

            visited[current_bubble] = energy
            b = self._bubble_index[current_bubble]

            for atom_id in b.bindings.values():
                a = self.store.get_atom(atom_id)
                if a: a.T += energy

            for key, atom_id in b.bindings.items():
                if key.startswith("rel:"):
                    parts = key.split(":", 2)
                    rel_name = parts[1]
                    target_b = parts[2]
                    if target_b not in self._bubble_index: continue
                    if relation_filter and relation_filter != rel_name: continue
                    queue.append((target_b, energy * decay))

    def _get_property_value(self, bubble_name: str, key: str) -> Any:
        b = self._bubble_index.get(bubble_name)
        if not b: return None
        if key.upper() == "TEMPERATURA":
            return max([self.store.get_atom(aid).T for aid in b.bindings.values() if self.store.get_atom(aid)], default=0.0)
        if key.upper() == "BĄBEL":
            return bubble_name
        atom = self.store.get_atom(b.bindings.get(key))
        if not atom: return None
        return atom.metadata.get('v', KarminType.parse(atom.E))

    def _eval_compare(self, bubble_name: str, cmp: CondCompare, env: dict,
                      query_runner: Optional[Any] = None) -> bool:
        val = self._get_property_value(bubble_name, cmp.key)
        operator = cmp.op
        target_val_str = cmp.val

        if operator == "JEST NIC":
            return val is None
        if operator == "NIE JEST NIC":
            return val is not None

        target_val = None
        target_list = None
        if cmp.subquery is not None:
            if not query_runner:
                return False
            target_list = query_runner(cmp.subquery, cmp.key)
        elif target_val_str.startswith('$'):
            target_list = env.get(target_val_str, [])
        else:
            target_val = KarminType.parse(target_val_str)

        try:
            if operator == "MIĘDZY":
                lo = KarminType.parse(target_val_str)
                hi = KarminType.parse(cmp.val2) if cmp.val2 is not None else None
                if hi is None or val is None:
                    return False
                return lo <= val <= hi
            if operator == "W" and target_list is not None:
                return val in target_list
            if operator == "NIE W" and target_list is not None:
                return val not in target_list
            if str(target_val_str).upper() == "NIC" or (target_val is None and operator in ("=", "!=")):
                if operator == "=": return val is None
                if operator == "!=": return val is not None
            if target_val is not None:
                if operator == "=": return val == target_val
                if operator == "!=": return val != target_val
                if operator == ">": return val > target_val
                if operator == "<": return val < target_val
                if operator == ">=": return val >= target_val
                if operator == "<=": return val <= target_val
                if operator == "ZAWIERA": return str(target_val).lower() in str(val).lower()
                if operator in ("PODOBNE", "LIKE"):
                    return _like_match(val, target_val_str)
                if operator in ("NIE PODOBNE", "NIE LIKE"):
                    return not _like_match(val, target_val_str)
        except (TypeError, ValueError):
            return False
        return False

    def evaluate_cond_expr(self, expr: CondExpr, env: dict,
                           join_relation: Optional[str] = None,
                           join_target: Optional[str] = None,
                           query_runner: Optional[Any] = None) -> List[str]:
        universe = self._resolve_universe(join_relation, join_target)
        matched = self._eval_cond_expr(expr, env, universe, query_runner)
        return sorted(matched)

    def _eval_cond_expr(self, expr: CondExpr, env: dict, universe: Set[str],
                        query_runner: Optional[Any] = None) -> Set[str]:
        if isinstance(expr, CondCompare):
            indexed = self._indexed_equals(expr, universe)
            if indexed is not None:
                return indexed
            return {name for name in universe if self._eval_compare(name, expr, env, query_runner)}
        if isinstance(expr, CondNot):
            all_in = set(universe)
            inner = self._eval_cond_expr(expr.inner, env, universe, query_runner)
            return all_in - inner
        if isinstance(expr, CondAnd):
            if not expr.parts:
                return set()
            current = universe
            result: Optional[Set[str]] = None
            for part in expr.parts:
                if result is not None:
                    current = result
                if isinstance(part, CondCompare):
                    indexed = self._indexed_equals(part, current)
                    if indexed is not None:
                        result = indexed
                        continue
                result = self._eval_cond_expr(part, env, current, query_runner)
            return result or set()
        if isinstance(expr, CondOr):
            result: Set[str] = set()
            for part in expr.parts:
                result |= self._eval_cond_expr(part, env, universe, query_runner)
            return result
        raise TypeError(f"Nieznany węzeł warunku: {type(expr)}")

    def process_modifiers(self, matched_bubbles: List[str], sort_by: Optional[str], sort_desc: bool, limit: Optional[int], offset: Optional[int]) -> List[str]:
        if sort_by:
            def get_sort_val(b_name):
                val = self._get_property_value(b_name, sort_by)
                if isinstance(val, (int, float)): return (1, val)
                if isinstance(val, bool): return (2, val)
                if val is None: return (0, "")
                return (3, str(val))
            matched_bubbles.sort(key=get_sort_val, reverse=sort_desc)
        else:
            matched_bubbles.sort()

        if offset: matched_bubbles = matched_bubbles[offset:]
        if limit is not None: matched_bubbles = matched_bubbles[:limit]
        return matched_bubbles

    def _dedupe_rows(self, rows: List[dict], keys: List[str]) -> List[dict]:
        seen: Set[tuple] = set()
        unique: List[dict] = []
        for row in rows:
            sig = tuple(KarminType.to_str(row.get(k)) for k in keys)
            if sig in seen:
                continue
            seen.add(sig)
            unique.append(row)
        return unique

    def _apply_row_modifiers(self, rows: List[dict], keys: List[str],
                             sort_by: Optional[str], sort_desc: bool,
                             limit: Optional[int], offset: Optional[int]) -> List[dict]:
        if sort_by:
            def row_sort_key(row: dict):
                val = row.get(sort_by)
                if isinstance(val, (int, float)): return (1, val)
                if isinstance(val, bool): return (2, val)
                if val is None: return (0, "")
                return (3, str(val))
            rows.sort(key=row_sort_key, reverse=sort_desc)
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return rows

    def project_where(self, columns: Tuple[ProjColumn, ...], cond: CondExpr, env: dict,
                      join_relation: Optional[str] = None, join_target: Optional[str] = None,
                      rel_joins: Tuple[RelJoinSpec, ...] = (),
                      distinct: bool = False,
                      sort_by: Optional[str] = None, sort_desc: bool = False,
                      limit: Optional[int] = None, offset: Optional[int] = None,
                      query_runner: Optional[Any] = None,
                      join_filter_fn: Optional[Any] = None) -> Tuple[List[dict], List[str]]:
        keys = [c.label for c in columns]
        matched = self.evaluate_cond_expr(cond, env, join_relation, join_target, query_runner)
        if not rel_joins:
            rows = [self._project_row(name, {}, columns) for name in sorted(matched)]
        else:
            contexts: List[Tuple[str, dict]] = [(n, {}) for n in sorted(matched)]
            for spec in rel_joins:
                if spec.filter_subquery and join_filter_fn:
                    candidates = set(join_filter_fn(spec.filter_subquery))
                else:
                    candidates = set(self._bubble_index.keys())
                join_hash = self._build_join_hash(candidates, spec.pairs, spec.alias)
                next_ctx: List[Tuple[str, dict]] = []
                for left_name, ctx in contexts:
                    matches = self._find_join_matches(left_name, ctx, spec, candidates, join_hash)
                    if matches:
                        for right_name in matches:
                            new_ctx = dict(ctx)
                            new_ctx[spec.alias] = right_name
                            next_ctx.append((left_name, new_ctx))
                    elif spec.left_outer:
                        next_ctx.append((left_name, ctx))
                contexts = next_ctx
            rows = [self._project_row(left, ctx, columns) for left, ctx in contexts]
        if distinct:
            rows = self._dedupe_rows(rows, keys)
        rows = self._apply_row_modifiers(rows, keys, sort_by, sort_desc, limit, offset)
        if any(c.label.upper() == "BĄBEL" for c in columns):
            names = [row.get("BĄBEL") for row in rows if row.get("BĄBEL") is not None]
        elif len(keys) == 1:
            names = [row.get(keys[0]) for row in rows]
        else:
            names = [row.get(keys[0]) for row in rows]
        return rows, names

    def update_where(self, key: str, value_raw: str, cond: CondExpr, env: dict,
                     join_relation: Optional[str] = None, join_target: Optional[str] = None,
                     query_runner: Optional[Any] = None) -> int:
        matched = self.evaluate_cond_expr(cond, env, join_relation, join_target, query_runner)
        count = 0
        for b_name in matched:
            self.update_property(b_name, key, value_raw)
            count += 1
        return count

    def _compare_having(self, agg_val: Any, op: str, target_raw: str) -> bool:
        target = KarminType.parse(target_raw)
        try:
            if op == "=": return agg_val == target
            if op == "!=": return agg_val != target
            if op == ">": return agg_val > target
            if op == "<": return agg_val < target
            if op == ">=": return agg_val >= target
            if op == "<=": return agg_val <= target
        except TypeError:
            return False
        return False

    @staticmethod
    def _group_label(group_by: List[str], bubble_name: str, api: "SubstrateAPI") -> str:
        parts = []
        for col in group_by:
            v = api._get_property_value(bubble_name, col)
            parts.append(KarminType.to_str(v) if v is not None else "NIC")
        return " | ".join(parts)

    def calculate_aggregate(self, action: str, key: str, matched_bubbles: List[str],
                            group_by: Optional[List[str]] = None,
                            having_action: Optional[str] = None,
                            having_op: Optional[str] = None,
                            having_val: Optional[str] = None) -> Any:
        groups: dict = {"GLOBAL": []} if not group_by else {}

        for b_name in matched_bubbles:
            g_key = "GLOBAL"
            if group_by:
                g_key = self._group_label(group_by, b_name, self)
            if g_key not in groups:
                groups[g_key] = []

            val = self._get_property_value(b_name, key)
            if action in ("POLICZ", "POLICZ RÓŻNE"):
                if val is not None:
                    groups[g_key].append(val)
            elif key.upper() == "TEMPERATURA" or isinstance(val, (int, float)):
                if val is not None:
                    groups[g_key].append(val)

        results = {}
        for g_name, values in groups.items():
            if action == "POLICZ":
                results[g_name] = len(values)
            elif action == "POLICZ RÓŻNE":
                results[g_name] = len(set(KarminType.to_str(v) for v in values))
            elif not values:
                results[g_name] = 0 if action in ("SUMA", "ŚREDNIA") else None
            elif action == "SUMA":
                results[g_name] = sum(values)
            elif action == "MIN":
                results[g_name] = min(values)
            elif action == "MAX":
                results[g_name] = max(values)
            elif action == "ŚREDNIA":
                results[g_name] = sum(values) / len(values)

        if group_by and having_action and having_op and having_val is not None:
            results = {g: v for g, v in results.items()
                       if self._compare_having(v, having_op, having_val)}

        return results if group_by else results.get("GLOBAL")


# ─── 6. EGZEKUTOR (Warstwa API) ─────────────────────────────────────────────

class KarminEngine:
    def __init__(self, store):
        self.parser = KarminParser()
        self.api = SubstrateAPI(store)
        self.in_transaction = False
        self.env = {}
        self._subquery_cache: dict = {}

    def _subquery_values(self, node: ASTNode, compare_key: str) -> List[Any]:
        cache_key = (id(node), compare_key)
        if cache_key in self._subquery_cache:
            return self._subquery_cache[cache_key]

        res = node.accept(self)
        action = res.get("action", "")
        values: List[Any] = []

        if action in ("FIND_WHERE", "FIND_REL", "SEARCH", "SET_OP"):
            values = list(res.get("matches", []))
        elif action == "PROJECT_WHERE":
            rows = res.get("rows", [])
            cols = res.get("columns", [])
            if compare_key.upper() == "BĄBEL" and "BĄBEL" in cols:
                values = [r.get("BĄBEL") for r in rows if r.get("BĄBEL") is not None]
            elif compare_key in cols:
                values = [r.get(compare_key) for r in rows]
            else:
                raise ValueError(
                    f"Podzapytanie WYPISZ nie zwraca kolumny '{compare_key}' (dostępne: {', '.join(cols)})"
                )
        else:
            raise ValueError(f"Podzapytanie zwróciło nieobsługiwany wynik: {action}")

        self._subquery_cache[cache_key] = values
        return values

    def execute(self, script: str, strict: bool = True) -> list:
        results = []
        self.env = {}
        self._subquery_cache = {}

        try: ast_nodes = self.parser.parse(script)
        except SyntaxError as e:
            if strict: raise
            return [{"status": "error", "message": str(e)}]

        auto_tx = (len(ast_nodes) > 1
                   and not isinstance(ast_nodes[0][2], BeginTxNode)
                   and not self.in_transaction)
        if auto_tx: self.api.begin_transaction()

        for line_no, line, node in ast_nodes:
            try:
                res = node.accept(self)
                if res: results.append(res)
            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                if self.in_transaction or auto_tx:
                    self.api.rollback_transaction()
                    self.in_transaction = False
                    results.append({"status": "error", "action": "ROLLBACK", "message": "Wycofano zmiany z powodu błędu", "line": line_no})
                if strict: raise RuntimeError(f"Błąd L{line_no}: {error_msg}") from e
                results.append({"status": "error", "message": error_msg, "line": line_no})
                break

        if auto_tx and results and results[-1].get("action") != "ROLLBACK":
            self.api.commit_transaction()

        return results

    @singledispatchmethod
    def visit(self, node: ASTNode): raise NotImplementedError()

    def _eval_set_operand(self, node: ASTNode) -> Tuple[str, Any]:
        """Zwraca ('names', list[str]) lub ('rows', (rows, columns))."""
        if isinstance(node, VarRefNode):
            val = self.env.get('$' + node.var_name)
            if val is None:
                raise ValueError(f"Niezdefiniowana zmienna ${node.var_name}")
            if isinstance(val, list) and val and isinstance(val[0], dict):
                cols = list(val[0].keys())
                return "rows", (val, cols)
            if not isinstance(val, list):
                raise ValueError(f"${node.var_name} musi być listą wyników zapytania")
            return "names", val

        res = node.accept(self)
        action = res.get("action", "")
        if action == "PROJECT_WHERE":
            return "rows", (res.get("rows", []), res.get("columns", []))
        if action in ("FIND_WHERE", "FIND_REL", "SEARCH", "SET_OP"):
            return "names", res.get("matches", [])
        if action == "COUNT_WHERE":
            return "names", []
        raise ValueError(f"Operacja zbiorów nie obsługuje wyniku akcji '{action}'")

    def _apply_set_op(self, op: str, left_data, right_data) -> Tuple[str, Any]:
        lk, lv = left_data
        rk, rv = right_data
        if lk != rk:
            raise ValueError("Operandy operacji zbiorów muszą być tego samego typu (nazwy bąbli lub wiersze)")

        if lk == "names":
            ls, rs = list(lv), list(rv)
            if op == "ZŁĄCZ":
                seen: Set[Any] = set()
                out: List[str] = []
                for name in ls + rs:
                    if name not in seen:
                        seen.add(name)
                        out.append(name)
                return "names", out
            if op == "PRZECIĘCIE":
                rs = set(rs)
                return "names", [n for n in ls if n in rs]
            if op == "RÓŻNICA":
                rs = set(rs)
                return "names", [n for n in ls if n not in rs]
            raise ValueError(f"Nieznana operacja zbiorów: {op}")

        left_rows, columns = lv
        right_rows, right_cols = rv
        if columns != right_cols:
            raise ValueError("WYPISZ … GDZIE w operacji zbiorów wymaga identycznych kolumn")

        def row_sig(row: dict) -> tuple:
            return tuple(KarminType.to_str(row.get(c)) for c in columns)

        if op == "ZŁĄCZ":
            seen_rows: Set[tuple] = set()
            out_rows: List[dict] = []
            for row in list(left_rows) + list(right_rows):
                sig = row_sig(row)
                if sig not in seen_rows:
                    seen_rows.add(sig)
                    out_rows.append(row)
            return "rows", (out_rows, columns)
        if op == "PRZECIĘCIE":
            rsigs = {row_sig(r) for r in right_rows}
            return "rows", ([r for r in left_rows if row_sig(r) in rsigs], columns)
        if op == "RÓŻNICA":
            rsigs = {row_sig(r) for r in right_rows}
            return "rows", ([r for r in left_rows if row_sig(r) not in rsigs], columns)
        raise ValueError(f"Nieznana operacja zbiorów: {op}")

    def _finalize_set_result(self, kind: str, data: Any, node: SetOpNode) -> dict:
        if kind == "names":
            matches = list(data)
            if node.sort_by:
                matches = self.api.process_modifiers(matches, node.sort_by, node.sort_desc, None, None)
            if node.offset:
                matches = matches[node.offset:]
            if node.limit is not None:
                matches = matches[:node.limit]
            return {
                "status": "ok", "action": "SET_OP", "set_op": node.op,
                "matches": matches, "count": len(matches),
            }

        rows, columns = data
        if node.sort_by:
            rows = self.api._apply_row_modifiers(rows, columns, node.sort_by, node.sort_desc, None, None)
        if node.offset:
            rows = rows[node.offset:]
        if node.limit is not None:
            rows = rows[:node.limit]
        matches = [r.get("BĄBEL") for r in rows if "BĄBEL" in columns and r.get("BĄBEL") is not None]
        return {
            "status": "ok", "action": "SET_OP", "set_op": node.op,
            "columns": columns, "rows": rows, "matches": matches, "count": len(rows),
        }

    @visit.register(VarRefNode)
    def _(self, node):
        val = self.env.get('$' + node.var_name)
        if val is None:
            raise ValueError(f"Niezdefiniowana zmienna ${node.var_name}")
        if isinstance(val, list) and val and isinstance(val[0], dict):
            return {"status": "ok", "action": "SET_OP", "rows": val, "columns": list(val[0].keys()), "matches": []}
        return {"status": "ok", "action": "SET_OP", "matches": list(val) if isinstance(val, list) else [val]}

    @visit.register(SetOpNode)
    def _(self, node):
        if not node.op:
            return self._finalize_set_result(*self._eval_set_operand(node.left), node)

        left_data = self._eval_set_operand(node.left)
        right_data = self._eval_set_operand(node.right)
        kind, merged = self._apply_set_op(node.op, left_data, right_data)
        return self._finalize_set_result(kind, merged, node)

    @visit.register(AssignNode)
    def _(self, node):
        res = node.expr.accept(self)
        val = res.get("matches", res.get("rows", res.get("result", res.get("data"))))
        self.env['$' + node.var_name] = val
        return {"status": "ok", "action": "ASSIGN", "variable": f"${node.var_name}", "value": val}

    @visit.register(CreateNamespaceNode)
    def _(self, node):
        self.api.create_namespace(node.name)
        return {"status": "ok", "action": "CREATE_NAMESPACE", "target": node.name}

    @visit.register(UseNamespaceNode)
    def _(self, node):
        self.api.use_namespace(node.name)
        return {"status": "ok", "action": "USE_NAMESPACE", "target": node.name}

    @visit.register(BeginTxNode)
    def _(self, node):
        self.api.begin_transaction()
        self.in_transaction = True
        return {"status": "ok", "action": "BEGIN"}

    @visit.register(CommitTxNode)
    def _(self, node):
        self.api.commit_transaction()
        self.in_transaction = False
        return {"status": "ok", "action": "COMMIT"}

    @visit.register(RollbackTxNode)
    def _(self, node):
        self.api.rollback_transaction()
        self.in_transaction = False
        return {"status": "ok", "action": "ROLLBACK"}

    @visit.register(CreateBubbleNode)
    def _(self, node):
        self.api.create_bubble(node.name)
        return {"status": "ok", "action": "CREATE", "target": node.name}

    @visit.register(AddPropertyNode)
    def _(self, node):
        self.api.add_property(node.target, node.key, node.value)
        return {"status": "ok", "action": "ADD_PROP", "target": node.target, "key": node.key, "value": KarminType.parse(node.value)}

    @visit.register(BulkInjectNode)
    def _(self, node):
        for key, value in node.properties:
            self.api.add_property(node.target, key, value)
        return {
            "status": "ok", "action": "BULK_INJECT", "target": node.target,
            "count": len(node.properties),
            "keys": [k for k, _ in node.properties],
        }

    @visit.register(BulkCreateNode)
    def _(self, node):
        created = []
        for name, props in node.entries:
            self.api.create_bubble(name)
            for key, value in props:
                self.api.add_property(name, key, value)
            created.append(name)
        return {"status": "ok", "action": "BULK_CREATE", "created": created, "count": len(created)}

    @visit.register(InsertFromNode)
    def _(self, node):
        res = node.subquery.accept(self)
        action = res.get("action", "")
        created: List[str] = []

        if action == "PROJECT_WHERE":
            created = self.api.insert_from_rows(res.get("columns", []), res.get("rows", []))
        elif action in ("FIND_WHERE", "FIND_REL", "SEARCH", "SET_OP"):
            for src in res.get("matches", []):
                created.append(self.api.clone_bubble(src))
        else:
            raise ValueError(f"WSTAW Z: nieobsługiwany wynik podzapytania ({action})")

        return {
            "status": "ok", "action": "INSERT_FROM", "created": created, "count": len(created),
            "source_action": action,
        }

    @visit.register(ExportCsvNode)
    def _(self, node):
        res = node.subquery.accept(self)
        action = res.get("action", "")
        columns: List[str] = []
        rows: List[dict] = []

        if action == "PROJECT_WHERE":
            columns = list(res.get("columns", []))
            rows = list(res.get("rows", []))
        elif action in ("FIND_WHERE", "FIND_REL", "SEARCH", "SET_OP"):
            columns = ["BĄBEL"]
            rows = [{"BĄBEL": n} for n in res.get("matches", [])]
        else:
            raise ValueError(f"EKSPORT CSV: nieobsługiwany wynik podzapytania ({action})")

        n = self.api.write_csv(node.path, columns, rows)
        return {
            "status": "ok", "action": "EXPORT_CSV", "file": node.path,
            "columns": columns, "rows_written": n,
        }

    @visit.register(ImportCsvNode)
    def _(self, node):
        created = self.api.import_csv(node.path, node.name_column)
        return {
            "status": "ok", "action": "IMPORT_CSV", "file": node.path,
            "created": created, "count": len(created),
            "name_column": node.name_column,
        }

    @visit.register(DescribeDatabaseNode)
    def _(self, node):
        data = self.api.describe_database()
        return {"status": "ok", "action": "DESCRIBE_DB", **data}

    @visit.register(MergeNode)
    def _(self, node):
        if node.bubble_name:
            name, mode = self.api.merge_bubble(node.bubble_name, list(node.properties))
        else:
            name, mode = self.api.merge_by_key(node.match_key, node.match_value, list(node.properties))
        return {"status": "ok", "action": "MERGE", "target": name, "mode": mode}

    @visit.register(UpdatePropertyNode)
    def _(self, node):
        self.api.update_property(node.target, node.key, node.value)
        return {"status": "ok", "action": "UPDATE_PROP", "target": node.target, "key": node.key}

    @visit.register(UpdateWhereNode)
    def _(self, node):
        count = self.api.update_where(
            node.key, node.value, node.cond, self.env, node.join_relation, node.join_target,
            self._subquery_values)
        return {"status": "ok", "action": "UPDATE_WHERE", "key": node.key, "updated_count": count}

    @visit.register(RemovePropertyNode)
    def _(self, node):
        self.api.remove_property(node.target, node.key)
        return {"status": "ok", "action": "REMOVE_PROP", "target": node.target, "key": node.key}

    @visit.register(ConnectNode)
    def _(self, node):
        self.api.connect_bubbles(node.source, node.target, node.relation)
        return {"status": "ok", "action": "CONNECT", "source": node.source, "target": node.target, "relation": node.relation}

    @visit.register(DisconnectNode)
    def _(self, node):
        self.api.disconnect_bubbles(node.source, node.target, node.relation)
        return {"status": "ok", "action": "DISCONNECT", "source": node.source, "target": node.target, "relation": node.relation}

    @visit.register(DeleteBubbleNode)
    def _(self, node):
        self.api.delete_bubble(node.target)
        return {"status": "ok", "action": "DELETE", "target": node.target}

    @visit.register(ShowBubbleNode)
    def _(self, node):
        return {"status": "ok", "action": "SHOW", "target": node.target, "data": self.api.get_contents(node.target)}

    @visit.register(ProjectNode)
    def _(self, node):
        return {"status": "ok", "action": "PROJECT", "target": node.target, "data": self.api.project_contents(node.target, node.keys)}

    def _resolve_join_filter(self, subquery_node: ASTNode) -> List[str]:
        res = subquery_node.accept(self)
        action = res.get("action", "")
        if action in ("FIND_WHERE", "FIND_REL", "SEARCH", "SET_OP"):
            return list(res.get("matches", []))
        raise ValueError(f"Filtr JOIN wymaga podzapytania ZNAJDŹ (otrzymano {action})")

    @visit.register(ProjectWhereNode)
    def _(self, node):
        rows, matched = self.api.project_where(
            node.columns, node.cond, self.env, node.join_relation, node.join_target,
            node.rel_joins,
            node.distinct, node.sort_by, node.sort_desc, node.limit, node.offset,
            self._subquery_values, self._resolve_join_filter,
        )
        return {
            "status": "ok", "action": "PROJECT_WHERE", "columns": node.keys,
            "rows": rows, "matches": matched, "count": len(rows), "distinct": node.distinct,
            "rel_joins": [j.alias for j in node.rel_joins],
        }

    @visit.register(ShowHistoryNode)
    def _(self, node):
        return {"status": "ok", "action": "HISTORY", "target": node.target, "key": node.key, "data": self.api.get_history(node.target, node.key)}

    @visit.register(SearchNode)
    def _(self, node):
        return {"status": "ok", "action": "SEARCH", "query": node.query, "matches": self.api.search_resonance(node.query)}

    @visit.register(FindRelationNode)
    def _(self, node):
        if node.cond:
            matched = self.api.evaluate_cond_expr(
                node.cond, self.env, node.relation, node.target, self._subquery_values)
        else:
            matched = self.api.find_relation(node.relation, node.target)
        matched = self.api.process_modifiers(matched, node.sort_by, node.sort_desc, node.limit, node.offset)
        return {"status": "ok", "action": "FIND_REL", "relation": node.relation, "target": node.target, "matches": matched}

    @visit.register(ExciteNode)
    def _(self, node):
        self.api.propagate_energy(node.target, node.energy, node.relation)
        return {"status": "ok", "action": "EXCITE", "target": node.target, "energy_injected": node.energy, "relation_filter": node.relation}

    @visit.register(ConditionNode)
    def _(self, node):
        matched = self.api.evaluate_cond_expr(
            node.cond, self.env, node.join_relation, node.join_target, self._subquery_values)
        matched = self.api.process_modifiers(matched, node.sort_by, node.sort_desc, node.limit, node.offset)

        if node.action == "ZNAJDŹ": return {"status": "ok", "action": "FIND_WHERE", "matches": matched}
        elif node.action == "POLICZ": return {"status": "ok", "action": "COUNT_WHERE", "count": len(matched)}
        elif node.action == "USUŃ":
            count = 0
            for b_name in matched:
                self.api.delete_bubble(b_name)
                count += 1
            return {"status": "ok", "action": "DELETE_WHERE", "deleted_count": count}

    @visit.register(AggregateNode)
    def _(self, node):
        matched = self.api.evaluate_cond_expr(
            node.cond, self.env, node.join_relation, node.join_target, self._subquery_values)
        matched = self.api.process_modifiers(matched, node.sort_by, node.sort_desc, node.limit, node.offset)
        result = self.api.calculate_aggregate(
            node.action, node.key, matched, node.group_by,
            node.having_action, node.having_op, node.having_val)

        return {
            "status": "ok",
            "action": f"AGGREGATE_{node.action.replace(' ', '_')}",
            "key": node.key,
            "group_by": node.group_by,
            "result": result,
            "matched_count": len(matched),
        }