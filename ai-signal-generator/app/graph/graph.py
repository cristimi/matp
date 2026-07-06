from langgraph.graph import StateGraph, START, END

from .gating import should_skip_llm_no_range
from .state import AgentState
from .nodes.node_ingest import node_ingest
from .nodes.node_analyze import node_analyze
from .nodes.node_guard import node_guard
from .nodes.node_dispatch import node_dispatch
from .nodes.node_skip import node_skip_geometry


def _route_after_ingest(state: AgentState) -> str:
    return 'skip' if should_skip_llm_no_range(state) else 'analyze'


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node('ingest',        node_ingest)
    graph.add_node('analyze',       node_analyze)
    graph.add_node('guard',         node_guard)
    graph.add_node('dispatch',      node_dispatch)
    graph.add_node('skip_geometry', node_skip_geometry)
    graph.add_edge(START,      'ingest')
    graph.add_conditional_edges(
        'ingest', _route_after_ingest, {'analyze': 'analyze', 'skip': 'skip_geometry'}
    )
    graph.add_edge('analyze',       'guard')
    graph.add_edge('guard',         'dispatch')
    graph.add_edge('skip_geometry', 'dispatch')
    graph.add_edge('dispatch', END)
    return graph.compile()
