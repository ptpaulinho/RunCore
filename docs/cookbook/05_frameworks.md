# Cookbook 5 — Framework adapters

## LangGraph

```python
from runcore.sdk.adapters import RunCoreLangGraphTracer
from runcore import GuardConfig

tracer = RunCoreLangGraphTracer(
    agent_name="order_graph",
    task="process order INV-1001",
    guards=GuardConfig(),
)

# Wrap your compiled graph — zero changes to graph definition
app = tracer.wrap(graph.compile())

# Invoke normally
result = app.invoke({"messages": [HumanMessage(content="Process order INV-1001")]})

# Inspect results
trace = tracer.get_atir()
print(f"CpST: ${trace.aggregates.cost_per_successful_task:.5f}")
print(f"Nodes executed: {trace.aggregates.tool_calls}")
print(tracer.savings_report())

# Async also works
result = await app.ainvoke({"messages": [...]})
```

## CrewAI

```python
from runcore.sdk.adapters import trace_crew, RunCoreCrewCallback
from crewai import Crew, Agent, Task

# Option A: context manager (recommended)
with trace_crew("support_crew", task="handle ticket #1234") as tracer:
    result = crew.kickoff()

trace = tracer.get_atir()

# Option B: callback (attach to existing crew)
callback = RunCoreCrewCallback()
crew = Crew(agents=[...], tasks=[...], callbacks=[callback])
result = crew.kickoff()
```

## AutoGen

```python
from runcore.sdk.adapters import RunCoreAutoGenTracer
from autogen import AssistantAgent, UserProxyAgent

tracer = RunCoreAutoGenTracer(
    agent_name="code_reviewer",
    task="review PR #42",
)

# Replaces user_proxy.initiate_chat(...)
result = tracer.initiate_chat(
    user_proxy,
    assistant,
    message="Please review this Python function for bugs.",
)

trace = tracer.get_atir()
print(f"Messages exchanged: {trace.aggregates.llm_calls}")
print(f"Functions called:   {trace.aggregates.tool_calls}")
```

## LangChain / LCEL

```python
from runcore.sdk.adapters import RunCoreLangChainTracer, RunCoreLangChainCallback, trace_chain
from langchain_core.runnables import RunnablePassthrough

# Option A: wrap a runnable
tracer = RunCoreLangChainTracer("qa_chain", task="answer question")
wrapped = tracer.wrap(chain)
result = wrapped.invoke({"question": "What is CpST?"})
trace = tracer.get_atir()

# Option B: attach to existing runcore.capture()
import runcore
with runcore.capture("my_chain") as cap:
    callback = RunCoreLangChainCallback()
    result = chain.invoke({"question": "..."}, config={"callbacks": [callback]})
trace = cap.get_atir()

# Option C: context manager shorthand
with trace_chain("support_chain", task="route ticket") as tracer:
    result = chain.invoke({"input": "..."}, config={"callbacks": [tracer.callback]})
trace = tracer.get_atir()
```

## All adapters — common API

All four adapters share the same interface:

```python
tracer.get_atir()          # → ATIRTrace
tracer.savings_report()    # → SavingsReport | None
tracer.set_quality(0.95)   # set quality score manually
tracer.set_success(True)   # override success flag
```
