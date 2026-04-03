"""Planner + Executor agent pattern.

The Planner generates a step-by-step text plan for the task, aware of all available tools.
The Executor then carries out the plan, with the plan injected into its system prompt via a
callable ``instructions`` function that reads from the shared context object.

Typical usage::

    from agents.extensions.planner_executor import run_planner_executor

    async with MCPServerStdio(...) as server:
        result = await run_planner_executor(
            task="Find all Python files and count lines of code",
            mcp_servers=[server],
        )
        print(result.final_output)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from agents import Agent, RunConfig, Runner, function_tool, trace
from agents.mcp import MCPServer
from agents.run_context import RunContextWrapper
from agents.tool import FunctionTool, Tool

# ---------------------------------------------------------------------------
# Default instruction templates
# ---------------------------------------------------------------------------

_DEFAULT_PLANNER_INSTRUCTIONS = """\
You are a planning agent. Given a task and a catalog of available tools, produce a clear \
step-by-step plan that an executor agent can follow to complete the task.

Do NOT execute the plan yourself — only produce the plan.

Available tools:
{tool_catalog}

Return a concrete, ordered plan. Each step should be actionable and reference specific tools \
where relevant. Make sure you follow the instructions to format your output correctly, paying attention to requirements on where you place files, what you name them, and how to format your final results.\
"""

_DEFAULT_EXECUTOR_INSTRUCTIONS = """\
You are an executor agent. Your job is to carry out the plan below step by step using the \
available tools. Follow the plan closely. If a step cannot be completed, note it and move on. Use your memory to track your progress and update it after each step so you don't perform the same step twice.\
"""


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class PlannerOutput(BaseModel):
    """Structured output produced by the Planner agent."""

    plan: str
    """Step-by-step plan text for the Executor to follow."""


@dataclass
class PlannerExecutorContext:
    """Shared context passed to both the Planner and Executor ``Runner.run()`` calls.

    After the Planner runs, ``plan`` is populated and the Executor's callable
    ``instructions`` function will inject it into the system prompt automatically.
    """

    task: str = ""
    """The original user task."""

    plan: str | None = None
    """Populated after the Planner run completes."""

    tool_catalog: str = ""
    """Human-readable summary of available tools, built before the Planner runs."""

    memory: str = ""
    """Running notes the Executor can write to track progress across turns."""


@dataclass
class PlannerExecutorConfig:
    """Configuration for the Planner + Executor pattern."""

    planner_model: str | None = None
    """Model for the Planner agent. Falls back to the SDK default when ``None``."""

    executor_model: str | None = None
    """Model for the Executor agent. Falls back to the SDK default when ``None``."""

    planner_base_instructions: str = _DEFAULT_PLANNER_INSTRUCTIONS
    """System prompt template for the Planner. Must contain ``{tool_catalog}``."""

    executor_base_instructions: str = _DEFAULT_EXECUTOR_INSTRUCTIONS
    """Base system prompt for the Executor. The plan is appended automatically."""

    max_executor_turns: int = 20
    """Maximum number of turns the Executor is allowed to run."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_tool_catalog(tools: list[Tool]) -> str:
    """Return a human-readable catalog of tool names and descriptions."""
    if not tools:
        return "(no tools available)"
    lines: list[str] = []
    for tool in tools:
        if isinstance(tool, FunctionTool):
            desc = (tool.description or "").strip().splitlines()[0]  # first line only
            lines.append(f"- {tool.name}: {desc}" if desc else f"- {tool.name}")
        else:
            name = getattr(tool, "name", str(tool))
            lines.append(f"- {name}")
    return "\n".join(lines)

@function_tool
def update_memory(
    ctx: RunContextWrapper[PlannerExecutorContext], note: str
) -> str:
    """Append a note to your working memory (e.g. 'Completed step 2: found 3 files').
    Call this after each plan step so you can track progress across turns."""
    ctx.context.memory = (ctx.context.memory + "\n" + note).strip()
    return "Memory updated."


async def _enumerate_tools(
    mcp_servers: list[MCPServer],
    function_tools: list[Tool],
    ctx: RunContextWrapper[PlannerExecutorContext],
) -> list[Tool]:
    """Enumerate all available tools using a temporary Agent."""
    temp_agent: Agent[PlannerExecutorContext] = Agent(
        name="_tool_enumerator",
        tools=function_tools,
        mcp_servers=mcp_servers,
    )
    return await temp_agent.get_all_tools(ctx)

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_planner_executor_pair(
    config: PlannerExecutorConfig,
    mcp_servers: list[MCPServer],
    function_tools: list[Tool],
    tool_catalog: str,
) -> tuple[Agent[PlannerExecutorContext], Agent[PlannerExecutorContext]]:
    """Create a (planner_agent, executor_agent) pair from the given config.

    The Planner receives the tool catalog embedded in its instructions but does
    **not** have access to the MCP servers directly (preventing accidental tool
    calls during planning).

    The Executor gets the full ``mcp_servers`` and ``function_tools`` lists and
    has the plan injected dynamically via a callable ``instructions`` function.

    Args:
        config: Configuration object controlling models, instructions, and limits.
        mcp_servers: Connected MCP servers available to the Executor.
        function_tools: Additional function tools available to the Executor.
        tool_catalog: Pre-formatted tool catalog string to embed in the Planner's prompt.

    Returns:
        A ``(planner_agent, executor_agent)`` tuple ready to be run sequentially.
    """
    planner_instructions = config.planner_base_instructions.format(
        tool_catalog=tool_catalog or "(no tools available)"
    )

    planner: Agent[PlannerExecutorContext] = Agent(
        name="planner",
        instructions=planner_instructions,
        model=config.planner_model,
        output_type=PlannerOutput,
    )

    def executor_instructions(
        ctx: RunContextWrapper[PlannerExecutorContext],
        agent: Agent[PlannerExecutorContext],
    ) -> str:
        parts = [config.executor_base_instructions]
        plan = (ctx.context.plan or "").strip()
        if plan:
            parts.append(f"## Plan\n{plan}")
        memory = (ctx.context.memory or "").strip()
        if memory:
            parts.append(f"## Memory of your previous actions\n{memory}")
        return "\n\n".join(parts)

    executor: Agent[PlannerExecutorContext] = Agent(
        name="executor",
        instructions=executor_instructions,
        model=config.executor_model,
        tools=[*function_tools, update_memory],
        mcp_servers=mcp_servers,
    )

    return planner, executor


# ---------------------------------------------------------------------------
# Convenience orchestrator
# ---------------------------------------------------------------------------


async def run_planner_executor(
    task: str,
    mcp_servers: list[MCPServer] | None = None,
    function_tools: list[Tool] | None = None,
    *,
    config: PlannerExecutorConfig | None = None,
    run_config: RunConfig | None = None,
    trace_id: str | None = None,
) -> Any:
    """Run the full Planner → Executor pipeline and return the executor's ``RunResult``.

    Steps:
    1. Enumerate all available tools (MCP + function tools) to build a catalog.
    2. Run the Planner agent (single turn) to produce a ``PlannerOutput``.
    3. Store the plan text in the shared context.
    4. Run the Executor agent with the plan injected into its system prompt.

    Args:
        task: The user task / natural-language instruction to carry out.
        mcp_servers: Connected MCP servers. Each must already have ``connect()`` called.
        function_tools: Additional function tools to expose to the Executor.
        config: Optional ``PlannerExecutorConfig``; defaults to ``PlannerExecutorConfig()``.
        run_config: Optional SDK ``RunConfig`` forwarded to both ``Runner.run()`` calls.

    Returns:
        The ``RunResult`` from the Executor run. Access ``result.final_output`` for the answer.
    """
    if config is None:
        config = PlannerExecutorConfig()
    if mcp_servers is None:
        mcp_servers = []
    if function_tools is None:
        function_tools = [update_memory]
    else:
        function_tools.append(update_memory)

    ctx = RunContextWrapper(context=PlannerExecutorContext(task=task))

    # 1. Enumerate tools and build catalog
    all_tools = await _enumerate_tools(mcp_servers, function_tools, ctx)
    tool_catalog = _format_tool_catalog(all_tools)
    ctx.context.tool_catalog = tool_catalog

    # 2. Create the agent pair
    planner, executor = create_planner_executor_pair(
        config=config,
        mcp_servers=mcp_servers,
        function_tools=function_tools,
        tool_catalog=tool_catalog,
    )

    # 3. Run the Planner (single turn — it should not loop)
    with trace(workflow_name="Planner + Executor", trace_id=trace_id):
        planner_result = await Runner.run(
            planner,
            task,
            context=ctx.context,
            max_turns=1,
            run_config=run_config,
        )

    assert isinstance(planner_result.final_output, PlannerOutput), (
        f"Planner returned unexpected output type: {type(planner_result.final_output)}"
    )
    ctx.context.plan = planner_result.final_output.plan

    # 4. Run the Executor with the plan injected via callable instructions
    with trace(workflow_name="Planner + Executor", trace_id=trace_id):
        executor_result = await Runner.run(
            executor,
            task,
            context=ctx.context,
            max_turns=config.max_executor_turns,
            run_config=run_config,
        )

    return executor_result
