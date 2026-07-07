#!/usr/bin/env python3
"""
cynober_query_engine.py — Silnik zapytań KarminQL v4.8 (PL)
==========================================================================
Epoka III: Skrypty. Wdrożenie instrukcji NIECH (LET) oraz operatorów 
zbiorów (W, NIE W), umożliwiających budowanie złożonych procedur.
"""

import re
import time
from typing import Any, Optional, Tuple, Set, List
from dataclasses import dataclass
from functools import singledispatchmethod

# ─── 1. FUNDAMENT TYPÓW ──────────────────────────────────────────────────

class KarminType:
    @staticmethod
    def parse(val: Any) -> Any:
        if isinstance(val, (int, float, bool, type(None))): return val
        s = str(val).strip()
        if s.upper() == "PRAWDA": return True
        if s.upper() == "FAŁSZ": return False
        if s.upper() == "NIC": return None
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
class CreateNamespaceNode(ASTNode): name: str

@dataclass(frozen=True)
class UseNamespaceNode(ASTNode): name: str

@dataclass(frozen=True)
class CreateBubbleNode(ASTNode): name: str

@dataclass(frozen=True)
class AddPropertyNode(ASTNode): target: str; key: str; value: str

@dataclass(frozen=True)
class UpdatePropertyNode(ASTNode): target: str; key: str; value: str

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
class SearchNode(ASTNode): query: str

@dataclass(frozen=True)
class FindRelationNode(ASTNode): relation: str; target: str

@dataclass(frozen=True)
class ExciteNode(ASTNode): target: str; energy: float; relation: Optional[str]

@dataclass(frozen=True)
class AssignNode(ASTNode): var_name: str; expr: ASTNode

@dataclass(frozen=True) 
class ConditionNode(ASTNode): 
    action: str 
    cond1: Tuple[str, str, str]
    logic: str 
    cond2: Optional[Tuple[str, str, str]]
    sort_by: Optional[str] = None
    sort_desc: bool = False
    limit: Optional[int] = None
    offset: Optional[int] = None

@dataclass(frozen=True)
class AggregateNode(ASTNode):
    action: str
    key: str
    cond1: Tuple[str, str, str]
    logic: str
    cond2: Optional[Tuple[str, str, str]]
    sort_by: Optional[str] = None
    sort_desc: bool = False
    limit: Optional[int] = None
    offset: Optional[int] = None
    group_by: Optional[str] = None

@dataclass(frozen=True)
class ShowHistoryNode(ASTNode): target: str; key: str

@dataclass(frozen=True)
class BeginTxNode(ASTNode): pass

@dataclass(frozen=True)
class CommitTxNode(ASTNode): pass

@dataclass(frozen=True)
class RollbackTxNode(ASTNode): pass


# ─── 3. PARSER ───────────────────────────────────────────────────────────────

class KarminParser:
    def __init__(self):
        self.cond_pattern = re.compile(r'^(ZNAJDŹ|POLICZ\s+BĄBLE|USUŃ\s+BĄBLE)\s+GDZIE\s+(.+)$', re.IGNORECASE)
        self.agg_pattern = re.compile(r'^(SUMA|ŚREDNIA|MIN|MAX)\s+"([^"]+)"\s+GDZIE\s+(.+)$', re.IGNORECASE)
        # UWAGA: kolejność alternatywy ma znaczenie — operatory dwuznakowe (!=, >=, <=)
        # MUSZĄ być przed jednoznakowymi (=, >, <), inaczej ">" łapie przed ">="
        # i "RAM >= 500" parsuje się jako op=">" val="= 500" (cichy brak dopasowań).
        self.single_cond = re.compile(r'^"([^"]+)"\s*(!=|>=|<=|=|>|<|ZAWIERA|NIE\s+W|W)\s*(.+)$', re.IGNORECASE)
        self.assign_pattern = re.compile(r'^NIECH\s+\$([a-zA-Z0-9_]+)\s*=\s*(.+)$', re.IGNORECASE)
        
        self.patterns = {
            'utrwal_przestrzen': re.compile(r'^UTRWAL\s+PRZESTRZEŃ\s+"([^"]+)"$', re.IGNORECASE),
            'wybierz_przestrzen': re.compile(r'^WYBIERZ\s+PRZESTRZEŃ\s+"([^"]+)"$', re.IGNORECASE),
            'utrwal': re.compile(r'^UTRWAL\s+"([^"]+)"$', re.IGNORECASE),
            # Wartość: cytowana ("tekst") LUB bare (100, +50, PRAWDA/FAŁSZ/NIC) —
            # zgodnie z manualem §3. KarminType.parse rozstrzyga typ (cudzysłów = tekst).
            'wstrzyknij': re.compile(r'^WSTRZYKNIJ\s+"([^"]+)"\s*(?:->|=)\s*(.+?)\s+DO\s+"([^"]+)"$', re.IGNORECASE),
            'zaktualizuj': re.compile(r'^ZAKTUALIZUJ\s+"([^"]+)"\s*(?:->|=)\s*(.+?)\s+(?:W|DO)\s+"([^"]+)"$', re.IGNORECASE),
            'usun_atrybut': re.compile(r'^USUŃ\s+"([^"]+)"\s*Z\s+"([^"]+)"$', re.IGNORECASE),
            'usun_babel': re.compile(r'^USUŃ\s*BĄBEL\s+"([^"]+)"$', re.IGNORECASE),
            'polacz': re.compile(r'^POŁĄCZ\s+"([^"]+)"\s*Z\s+"([^"]+)"\s*JAKO\s+"([^"]+)"$', re.IGNORECASE),
            'rozlacz': re.compile(r'^ROZŁĄCZ\s+"([^"]+)"\s*Z\s+"([^"]+)"\s*JAKO\s+"([^"]+)"$', re.IGNORECASE),
            'wypisz': re.compile(r'^WYPISZ\s+(.+)\s+(?:Z|W)\s+"([^"]+)"$', re.IGNORECASE),
            'pokaz': re.compile(r'^POKAŻ\s+"([^"]+)"$', re.IGNORECASE),
            'historia': re.compile(r'^HISTORIA\s+"([^"]+)"\s*(?:Z|W)\s+"([^"]+)"$', re.IGNORECASE),
            'szukaj': re.compile(r'^SZUKAJ\s+"([^"]+)"$', re.IGNORECASE),
            'znajdz_relacje': re.compile(r'^ZNAJDŹ\s+POŁĄCZONE\s+JAKO\s+"([^"]+)"\s*Z\s+"([^"]+)"$', re.IGNORECASE),
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
            
            node = self._parse_line(line)
            if node: ast_nodes.append((line_no, line, node))
            else: raise SyntaxError(f"Nierozpoznana składnia w linii {line_no}: '{line}'")
        return ast_nodes

    def _parse_cond(self, text: str) -> Tuple[str, str, str]:
        m = self.single_cond.match(text.strip())
        if not m: raise SyntaxError(f"Niepoprawny warunek logiczny: {text}")
        key = m.group(1)
        # Standaryzacja spacji w operatorach dwuczłonowych: "NIE   W" -> "NIE W"
        op = re.sub(r'\s+', ' ', m.group(2).upper()).strip()
        
        val_str = m.group(3).strip()
        if not val_str.startswith('$'):
            val_str = val_str.strip('"').strip("'")
            
        return (key, op, val_str)

    def _extract_modifiers(self, conds_str: str) -> Tuple[str, Optional[int], Optional[int], Optional[str], bool, Optional[str]]:
        limit, offset, sort_by, sort_desc, group_by = None, None, None, False, None
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
        if m := re.search(r'\s+POGRUPUJ\s+"([^"]+)"', conds_str, re.IGNORECASE):
            group_by = m.group(1)
            conds_str = conds_str[:m.start()] + conds_str[m.end():]
        return conds_str, limit, offset, sort_by, sort_desc, group_by

    def _parse_line(self, line: str) -> Optional[ASTNode]:
        if match := self.assign_pattern.match(line):
            var_name = match.group(1)
            expr_str = match.group(2)
            expr_node = self._parse_line(expr_str)
            if not expr_node: raise SyntaxError(f"Błędne zapytanie przypisywane do zmiennej ${var_name}")
            return AssignNode(var_name, expr_node)
            
        for key in self.patterns:
            if key in ['wypisz']: continue
            if match := self.patterns[key].match(line):
                if key == 'utrwal_przestrzen': return CreateNamespaceNode(match.group(1))
                if key == 'wybierz_przestrzen': return UseNamespaceNode(match.group(1))
                if key == 'utrwal': return CreateBubbleNode(match.group(1))
                if key == 'wstrzyknij': return AddPropertyNode(match.group(3), match.group(1), match.group(2))
                if key == 'zaktualizuj': return UpdatePropertyNode(match.group(3), match.group(1), match.group(2))
                if key == 'usun_atrybut': return RemovePropertyNode(match.group(2), match.group(1))
                if key == 'usun_babel': return DeleteBubbleNode(match.group(1))
                if key == 'polacz': return ConnectNode(match.group(1), match.group(2), match.group(3))
                if key == 'rozlacz': return DisconnectNode(match.group(1), match.group(2), match.group(3))
                if key == 'pokaz': return ShowBubbleNode(match.group(1))
                if key == 'historia': return ShowHistoryNode(match.group(2), match.group(1))
                if key == 'szukaj': return SearchNode(match.group(1))
                if key == 'znajdz_relacje': return FindRelationNode(match.group(1), match.group(2))
                if key == 'wzbudz': return ExciteNode(match.group(1), float(match.group(2)), match.group(3))
                if key == 'begin': return BeginTxNode()
                if key == 'commit': return CommitTxNode()
                if key == 'rollback': return RollbackTxNode()
        
        if match := self.patterns['wypisz'].match(line): 
            return ProjectNode(match.group(2), [k.strip().strip('"') for k in match.group(1).split(",")])
        
        if match := self.agg_pattern.match(line):
            action = match.group(1).upper()
            key = match.group(2)
            conds_str, limit, offset, sort_by, sort_desc, group_by = self._extract_modifiers(match.group(3))
            
            if " ORAZ " in conds_str.upper():
                parts = re.split(r'\s+ORAZ\s+', conds_str, flags=re.IGNORECASE, maxsplit=1)
                return AggregateNode(action, key, self._parse_cond(parts[0]), "ORAZ", self._parse_cond(parts[1]), sort_by, sort_desc, limit, offset, group_by)
            elif " LUB " in conds_str.upper():
                parts = re.split(r'\s+LUB\s+', conds_str, flags=re.IGNORECASE, maxsplit=1)
                return AggregateNode(action, key, self._parse_cond(parts[0]), "LUB", self._parse_cond(parts[1]), sort_by, sort_desc, limit, offset, group_by)
            else: 
                return AggregateNode(action, key, self._parse_cond(conds_str), "NONE", None, sort_by, sort_desc, limit, offset, group_by)

        if match := self.cond_pattern.match(line):
            action = match.group(1).upper().split()[0]
            conds_str, limit, offset, sort_by, sort_desc, _ = self._extract_modifiers(match.group(2))

            if " ORAZ " in conds_str.upper():
                parts = re.split(r'\s+ORAZ\s+', conds_str, flags=re.IGNORECASE, maxsplit=1)
                return ConditionNode(action, self._parse_cond(parts[0]), "ORAZ", self._parse_cond(parts[1]), sort_by, sort_desc, limit, offset)
            elif " LUB " in conds_str.upper():
                parts = re.split(r'\s+LUB\s+', conds_str, flags=re.IGNORECASE, maxsplit=1)
                return ConditionNode(action, self._parse_cond(parts[0]), "LUB", self._parse_cond(parts[1]), sort_by, sort_desc, limit, offset)
            else: 
                return ConditionNode(action, self._parse_cond(conds_str), "NONE", None, sort_by, sort_desc, limit, offset)
        return None


# ─── 4. SUBSTRATE API ────────────────────────────────────────────────────

class BubbleAlreadyExistsError(Exception): pass

class SubstrateAPI:
    def __init__(self, store):
        self.store = store
        self.namespaces = {"DEFAULT": {"bubbles": {}, "inv_index": {}}}
        self.active_ns = "DEFAULT"
        self._backup_state = None

    @property
    def _bubble_index(self): return self.namespaces[self.active_ns]["bubbles"]

    @property
    def _inv_index(self): return self.namespaces[self.active_ns]["inv_index"]

    def create_namespace(self, name: str):
        if name in self.namespaces: raise ValueError(f"Przestrzeń '{name}' już istnieje.")
        self.namespaces[name] = {"bubbles": {}, "inv_index": {}}

    def use_namespace(self, name: str):
        if name not in self.namespaces: raise ValueError(f"Przestrzeń '{name}' nie istnieje. Utrwal ją najpierw.")
        self.active_ns = name

    def begin_transaction(self):
        # Pełny snapshot stanu aktywnej przestrzeni. Uwaga: transakcja obejmuje
        # przestrzeń aktywną w momencie BEGIN — modyfikacje w innych przestrzeniach
        # (po WYBIERZ PRZESTRZEŃ wewnątrz transakcji) nie są objęte rollbackiem.
        reg_atoms = self.store.reg._atoms
        self._backup_state = {
            "ns": self.active_ns,
            # skład indeksu bąbli — cofa UTWORZ/USUŃ bąbla
            "bubble_index": dict(self._bubble_index),
            # bindings każdego bąbla — cofa zmiany cech i relacji
            "bindings": {name: b.bindings.copy() for name, b in self._bubble_index.items()},
            # pełny skład rejestru atomów — cofa utworzone/usunięte atomy
            "atoms": dict(reg_atoms),
            # temperatury istniejących atomów — cofa mutacje w miejscu (np. WZBUDŹ)
            "atom_T": {aid: a.T for aid, a in reg_atoms.items()},
            # korzenie i lista bąbli w store — cofa set_root/unset_root/bubble_new
            "roots": list(self.store.roots),
            "bubbles": list(self.store.bubbles),
            # indeks odwrotny aktywnej przestrzeni
            "inv_index": {k: {v: s.copy() for v, s in vals.items()}
                          for k, vals in self._inv_index.items()},
        }

    def commit_transaction(self):
        self._backup_state = None

    def rollback_transaction(self):
        if self._backup_state is None: return
        bs = self._backup_state
        self.active_ns = bs["ns"]

        # 1) skład indeksu bąbli: usuń utworzone w transakcji, przywróć usunięte
        idx = self._bubble_index
        idx.clear()
        idx.update(bs["bubble_index"])

        # 2) bindings każdego przywróconego bąbla
        for name, b in idx.items():
            if name in bs["bindings"]:
                b.bindings = bs["bindings"][name]

        # 3) skład rejestru atomów (usuwa atomy utworzone w transakcji)
        self.store.reg._atoms = dict(bs["atoms"])
        self.store.reg._generation += 1        # unieważnij cache AtomsWrapper

        # 4) temperatury istniejących atomów + przeliczenie stanu FSM
        #    (mutacje w miejscu, np. a.T += energy w propagate_energy)
        for aid, T in bs["atom_T"].items():
            a = self.store.reg._atoms.get(aid)
            if a is not None:
                a.T = T
                a._update_state()

        # 5) korzenie i lista bąbli w store (osiągalność dla reach-GC)
        self.store.roots[:] = bs["roots"]
        self.store.bubbles[:] = bs["bubbles"]

        # 6) indeks odwrotny
        self.namespaces[self.active_ns]["inv_index"] = bs["inv_index"]

        self.commit_transaction()

    def _get_bubble(self, name: str):
        if name not in self._bubble_index: raise ValueError(f"Bąbel '{name}' nie istnieje w przestrzeni '{self.active_ns}'.")
        return self._bubble_index[name]

    def _update_index(self, bubble_name: str, key: str, value: Any, add: bool = True):
        val_str = KarminType.to_str(value)
        if key not in self._inv_index: self._inv_index[key] = {}
        if val_str not in self._inv_index[key]: self._inv_index[key][val_str] = set()
        if add: self._inv_index[key][val_str].add(bubble_name)
        else: self._inv_index[key][val_str].discard(bubble_name)

    def create_bubble(self, name: str):
        if name in self._bubble_index: raise BubbleAlreadyExistsError(f"Bąbel '{name}' już istnieje w '{self.active_ns}'.")
        bubble = self.store.bubble_new(label=name)
        self.store.set_root(bubble)
        self._bubble_index[name] = bubble

    def add_property(self, bubble_name: str, key: str, value_raw: str):
        bubble = self._get_bubble(bubble_name)
        val = KarminType.parse(value_raw)
        atom = self.store.atom_new(S=key, E=KarminType.to_str(val), value=KarminType.to_str(val))
        atom.metadata.update({'timestamp': int(time.time() * 1000), 'v': val})
        bubble.bind(key, atom)
        self._update_index(bubble_name, key, val, add=True)

    def update_property(self, bubble_name: str, key: str, value_raw: str):
        bubble = self._get_bubble(bubble_name)
        old_atom_id = bubble.bindings.get(key)
        old_val = None
        if old_atom_id:
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

    def remove_property(self, bubble_name: str, key: str):
        bubble = self._get_bubble(bubble_name)
        if key not in bubble.bindings: raise KeyError(f"Brak cechy '{key}'")
        old_atom_id = bubble.bindings.get(key)
        if old_atom_id:
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
                atom = self.store.get_atom(atom_id)
                if atom: self._update_index(bubble_name, key, atom.metadata.get('v'), add=False)
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
        bubble = self._get_bubble(bubble_name)
        results = {}
        for key in keys:
            atom_id = bubble.bindings.get(key)
            if atom_id:
                atom = self.store.get_atom(atom_id)
                results[key] = atom.metadata.get('v', atom.E) if atom else None
            else: results[key] = None
        return results

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
        found = set()
        for _, atom_id in hits:
            for name, b in self._bubble_index.items():
                if atom_id in b.bindings.values(): found.add(name)
        return sorted(list(found))

    def find_relation(self, relation: str, target: str) -> list:
        return sorted([name for name, b in self._bubble_index.items() if f"rel:{relation}:{target}" in b.bindings])
        
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

    def _eval_single(self, key: str, operator: str, target_val_str: str, env: dict) -> Set[str]:
        target_val = None
        target_list = None
        
        if target_val_str.startswith('$'):
            target_list = env.get(target_val_str, [])
        else:
            target_val = KarminType.parse(target_val_str)

        found = set()
        for name, b in self._bubble_index.items():
            if key.upper() == "TEMPERATURA":
                val = max([self.store.get_atom(aid).T for aid in b.bindings.values() if self.store.get_atom(aid)], default=0.0)
            elif key.upper() == "BĄBEL":
                val = name
            else:
                atom = self.store.get_atom(b.bindings.get(key))
                if not atom: continue
                val = atom.metadata.get('v', KarminType.parse(atom.E))
            
            try:
                if operator == "W" and target_list is not None:
                    if val in target_list: found.add(name)
                elif operator == "NIE W" and target_list is not None:
                    if val not in target_list: found.add(name)
                elif target_val is not None:
                    if operator == "=" and val == target_val: found.add(name)
                    elif operator == "!=" and val != target_val: found.add(name)
                    elif operator == ">" and val > target_val: found.add(name)
                    elif operator == "<" and val < target_val: found.add(name)
                    elif operator == ">=" and val >= target_val: found.add(name)
                    elif operator == "<=" and val <= target_val: found.add(name)
                    elif operator == "ZAWIERA" and str(target_val).lower() in str(val).lower(): found.add(name)
            except (TypeError, ValueError): continue
        return found

    def evaluate_condition_tree(self, cond1: Tuple[str, str, str], logic: str, cond2: Optional[Tuple[str, str, str]], env: dict) -> list:
        set1 = self._eval_single(*cond1, env)
        if logic == "NONE" or not cond2: return list(set1)
        set2 = self._eval_single(*cond2, env)
        if logic == "ORAZ": return list(set1 & set2)
        return list(set1 | set2)

    def process_modifiers(self, matched_bubbles: List[str], sort_by: Optional[str], sort_desc: bool, limit: Optional[int], offset: Optional[int]) -> List[str]:
        if sort_by:
            def get_sort_val(b_name):
                b = self._bubble_index[b_name]
                if sort_by.upper() == "TEMPERATURA":
                    return (1, max([self.store.get_atom(aid).T for aid in b.bindings.values() if self.store.get_atom(aid)], default=0.0))
                atom_id = b.bindings.get(sort_by)
                if not atom_id: return (0, "")
                atom = self.store.get_atom(atom_id)
                if not atom: return (0, "")
                
                val = atom.metadata.get('v', KarminType.parse(atom.E))
                if isinstance(val, (int, float)): return (1, val)
                elif isinstance(val, bool): return (2, val)
                elif val is None: return (0, "")
                else: return (3, str(val))
            matched_bubbles.sort(key=get_sort_val, reverse=sort_desc)
        else:
            matched_bubbles.sort()

        if offset: matched_bubbles = matched_bubbles[offset:]
        if limit is not None: matched_bubbles = matched_bubbles[:limit]
        return matched_bubbles

    def calculate_aggregate(self, action: str, key: str, matched_bubbles: List[str], group_by: Optional[str] = None) -> Any:
        groups = {"GLOBAL": []} if not group_by else {}
        for b_name in matched_bubbles:
            b = self._bubble_index[b_name]
            g_key = "GLOBAL"
            if group_by:
                g_atom_id = b.bindings.get(group_by)
                if g_atom_id:
                    g_atom = self.store.get_atom(g_atom_id)
                    g_key = KarminType.to_str(g_atom.metadata.get('v', g_atom.E)) if g_atom else "NIC"
                else: g_key = "NIC"
            
            if g_key not in groups: groups[g_key] = []
            
            if key.upper() == "TEMPERATURA":
                val = max([self.store.get_atom(aid).T for aid in b.bindings.values() if self.store.get_atom(aid)], default=0.0)
                groups[g_key].append(val)
            else:
                atom_id = b.bindings.get(key)
                if atom_id:
                    atom = self.store.get_atom(atom_id)
                    if atom:
                        val = atom.metadata.get('v', KarminType.parse(atom.E))
                        if isinstance(val, (int, float)): groups[g_key].append(val)

        results = {}
        for g_name, values in groups.items():
            if not values: 
                results[g_name] = 0 if action in ("SUMA", "ŚREDNIA") else None
                continue
            if action == "SUMA": results[g_name] = sum(values)
            elif action == "MIN": results[g_name] = min(values)
            elif action == "MAX": results[g_name] = max(values)
            elif action == "ŚREDNIA": results[g_name] = sum(values) / len(values)
            
        return results if group_by else results.get("GLOBAL")


# ─── 5. EGZEKUTOR (Warstwa API) ─────────────────────────────────────────────

class KarminEngine:
    def __init__(self, store):
        self.parser = KarminParser()
        self.api = SubstrateAPI(store)
        self.in_transaction = False
        self.env = {}

    def execute(self, script: str, strict: bool = True) -> list:
        results = []
        self.env = {} 
        
        try: ast_nodes = self.parser.parse(script)
        except SyntaxError as e:
            if strict: raise
            return [{"status": "error", "message": str(e)}]

        # auto-transakcja owija skrypty wieloliniowe — ale NIE, gdy trwa już
        # ręczna transakcja (inaczej nadpisałaby jej migawkę bieżącym stanem).
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

    @visit.register(AssignNode)
    def _(self, node):
        res = node.expr.accept(self)
        val = res.get("matches", res.get("result", res.get("data")))
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
        # #12: BEGIN musi wykonać migawkę stanu, inaczej ręczne transakcje
        # (manual §8) są martwe — ROLLBACK nie ma czego przywrócić.
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

    @visit.register(UpdatePropertyNode)
    def _(self, node):
        self.api.update_property(node.target, node.key, node.value) 
        return {"status": "ok", "action": "UPDATE_PROP", "target": node.target, "key": node.key}

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

    @visit.register(ShowHistoryNode)
    def _(self, node):
        return {"status": "ok", "action": "HISTORY", "target": node.target, "key": node.key, "data": self.api.get_history(node.target, node.key)}

    @visit.register(SearchNode)
    def _(self, node):
        return {"status": "ok", "action": "SEARCH", "query": node.query, "matches": self.api.search_resonance(node.query)}

    @visit.register(FindRelationNode)
    def _(self, node):
        return {"status": "ok", "action": "FIND_REL", "relation": node.relation, "target": node.target, "matches": self.api.find_relation(node.relation, node.target)}

    @visit.register(ExciteNode)
    def _(self, node):
        self.api.propagate_energy(node.target, node.energy, node.relation)
        return {"status": "ok", "action": "EXCITE", "target": node.target, "energy_injected": node.energy, "relation_filter": node.relation}

    @visit.register(ConditionNode)
    def _(self, node):
        matched = self.api.evaluate_condition_tree(node.cond1, node.logic, node.cond2, self.env)
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
        matched = self.api.evaluate_condition_tree(node.cond1, node.logic, node.cond2, self.env)
        matched = self.api.process_modifiers(matched, node.sort_by, node.sort_desc, node.limit, node.offset)
        result = self.api.calculate_aggregate(node.action, node.key, matched, node.group_by)
        
        return {
            "status": "ok", 
            "action": f"AGGREGATE_{node.action}", 
            "key": node.key, 
            "group_by": node.group_by,
            "result": result, 
            "matched_count": len(matched)
        }