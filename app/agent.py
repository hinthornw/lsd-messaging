"""Simple echo agent for development/testing."""

from langgraph.graph import StateGraph, MessagesState


async def echo(state: MessagesState) -> MessagesState:
    last = state["messages"][-1]
    return {"messages": [{"role": "assistant", "content": f"Echo: {last.content}"}]}


graph = StateGraph(MessagesState)
graph.add_node("echo", echo)
graph.set_entry_point("echo")
graph.set_finish_point("echo")
graph = graph.compile()
