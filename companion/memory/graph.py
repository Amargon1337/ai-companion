"""Memory Graph Analyzer - Tarjan's Strongly Connected Components (SCC) algorithm."""
from typing import Any, Dict, List, Set

class TarjanSCC:
    def __init__(self, adj_list: Dict[str, List[str]]):
        self.adj_list = adj_list
        self.index = 0
        self.stack: List[str] = []
        self.indices: Dict[str, int] = {}
        self.lowlink: Dict[str, int] = {}
        self.on_stack: Set[str] = set()
        self.sccs: List[List[str]] = []

    def find_sccs(self) -> List[List[str]]:
        for node in self.adj_list:
            if node not in self.indices:
                self._strongconnect(node)
        return self.sccs

    def _strongconnect(self, node: str) -> None:
        self.indices[node] = self.index
        self.lowlink[node] = self.index
        self.index += 1
        self.stack.append(node)
        self.on_stack.add(node)

        for w in self.adj_list.get(node, []):
            if w not in self.indices:
                self._strongconnect(w)
                self.lowlink[node] = min(self.lowlink[node], self.lowlink[w])
            elif w in self.on_stack:
                self.lowlink[node] = min(self.lowlink[node], self.indices[w])

        if self.lowlink[node] == self.indices[node]:
            scc = []
            while True:
                w = self.stack.pop()
                self.on_stack.remove(w)
                scc.append(w)
                if w == node:
                    break
            self.sccs.append(scc)

def detect_memory_cycles(fact_relations: List[Dict[str, Any]]) -> List[List[str]]:
    """
    Analyzes a list of relations (e.g. from_id -> to_id where relation='supersedes' or 'causal')
    and returns a list of cycles (SCCs with size > 1).
    """
    adj_list: Dict[str, List[str]] = {}
    for rel in fact_relations:
        # Depending on relation type, edges might indicate logical flow
        u = rel.get("from_id")
        v = rel.get("to_id")
        if u and v:
            if u not in adj_list:
                adj_list[u] = []
            adj_list[u].append(v)
            if v not in adj_list:
                adj_list[v] = []
            
    tarjan = TarjanSCC(adj_list)
    sccs = tarjan.find_sccs()
    
    # Cycles are SCCs with more than 1 node, or a node with a self-loop.
    cycles = []
    for scc in sccs:
        if len(scc) > 1:
            cycles.append(scc)
        elif len(scc) == 1:
            node = scc[0]
            if node in adj_list and node in adj_list[node]:
                cycles.append(scc)
                
    return cycles
