"""Planner + Executor agent pattern example.

The Planner agent writes a step-by-step plan for the task, informed by the list of available
tools. The Executor agent then carries out that plan, with the plan text injected into its
system prompt via the shared context.

This example uses the MCP filesystem server to give the agents tools to work with.

Run with:
    uv run python examples/agent_patterns/planner_executor.py
"""

import asyncio
import os
import shutil

from dotenv import load_dotenv

load_dotenv()

from agents import gen_trace_id, trace
from agents.extensions.planner_executor import (
    PlannerExecutorConfig,
    run_planner_executor,
)
from agents.mcp import MCPServerStdio


async def main() -> None:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # Point the filesystem server at the mcp example's sample files so we have something to read.
    samples_dir = os.path.join(current_dir, "..", "mcp", "filesystem_example", "sample_files")

    config = PlannerExecutorConfig(
        # Use the same model for both roles; they just have different instructions.
        planner_model=None,  # falls back to SDK default
        executor_model=None,  # falls back to SDK default
        max_executor_turns=15,
    )

    task = (
        "List all files available to you, then read each one and produce a brief summary "
        "of its contents."
    )

    async with MCPServerStdio(
        name="Filesystem Server, via npx",
        params={
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", samples_dir],
        },
    ) as server:
        trace_id = gen_trace_id()
        with trace(workflow_name="Planner + Executor Example", trace_id=trace_id):
            print(f"View trace: https://platform.openai.com/traces/trace?trace_id={trace_id}\n")
            print(f"Task: {task}\n")

            result = await run_planner_executor(
                task=task,
                mcp_servers=[server],
                config=config,
                trace_id=trace_id,
            )

            print("=== Final output ===")
            print(result.final_output)


if __name__ == "__main__":
    if not shutil.which("npx"):
        raise RuntimeError("npx is not installed. Please install it with `npm install -g npx`.")

    asyncio.run(main())
