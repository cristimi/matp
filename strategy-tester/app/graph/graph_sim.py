from langgraph.graph import StateGraph, END

from app.graph.state import AgentState
from app.engine.node_ingest_replay import node_ingest_replay
from app.engine.node_analyze_sim import node_analyze_sim
from app.engine.node_guard_sim import node_guard_sim
from app.engine.node_dispatch_sim import node_dispatch_sim


def build_sim_graph():
    """Build and compile the simulation StateGraph."""
    g = StateGraph(AgentState)

    g.add_node("ingest",   node_ingest_replay)
    g.add_node("analyze",  node_analyze_sim)
    g.add_node("guard",    node_guard_sim)
    g.add_node("dispatch", node_dispatch_sim)

    g.set_entry_point("ingest")
    g.add_edge("ingest",   "analyze")
    g.add_edge("analyze",  "guard")
    g.add_edge("guard",    "dispatch")
    g.add_edge("dispatch", END)

    return g.compile()
