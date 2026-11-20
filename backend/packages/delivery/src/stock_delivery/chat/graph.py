"""固定单轮 LangGraph 拓扑（不配置 checkpointer）。"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from .runtime import ChatRuntime
from .state import ChatState


def build_graph(runtime: ChatRuntime) -> Any:
    graph = StateGraph(ChatState)
    graph.add_node("load_context", runtime.load_context)
    graph.add_node("understand", runtime.understand)
    graph.add_node("execute_tools", runtime.execute_tools)
    graph.add_node("compose", runtime.compose)
    graph.add_node("answer", runtime.answer)
    graph.add_node("validate", runtime.validate)
    graph.add_node("fallback", runtime.fallback)
    graph.add_node("persist", runtime.persist)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "understand")
    graph.add_conditional_edges(
        "understand",
        runtime.route_after_understand,
        {"clarify": "compose", "tools": "execute_tools"},
    )
    graph.add_edge("execute_tools", "compose")
    graph.add_conditional_edges(
        "compose",
        runtime.route_after_compose,
        {"answer": "answer", "validate": "validate"},
    )
    graph.add_edge("answer", "validate")
    graph.add_conditional_edges(
        "validate",
        runtime.route_after_validate,
        {"persist": "persist", "retry": "answer", "fallback": "fallback"},
    )
    graph.add_edge("fallback", "persist")
    graph.add_edge("persist", END)
    return graph.compile()
