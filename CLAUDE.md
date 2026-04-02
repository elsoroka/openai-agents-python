# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Also read `AGENTS.md` for additional contributor policies and detailed guidelines.

## Commands

Use `uv run python ...` for all Python invocations to ensure a consistent environment.

```bash
make sync           # Install/refresh all dev dependencies
make format         # Apply ruff formatting and auto-fix lint issues
make lint           # Check linting only (no fixes)
make typecheck      # Run mypy + pyright in parallel
make tests          # Run the full test suite (parallel + serial)
make coverage       # Generate coverage report (fails below 85%)
make snapshots-fix  # Update existing inline snapshots
make snapshots-create # Create new inline snapshots
make build-docs     # Generate API reference files and build MkDocs site
make serve-docs     # Preview docs locally
```

Run a focused test:
```bash
uv run pytest -s -k <pattern>
```

Test against Python 3.10 (minimum supported version):
```bash
UV_PROJECT_ENVIRONMENT=.venv_310 uv sync --python 3.10 --all-extras --all-packages --group dev
UV_PROJECT_ENVIRONMENT=.venv_310 uv run --python 3.10 -m pytest
```

Before submitting any runtime code change, run in order:
```bash
make format && make lint && make typecheck && make tests
```

## Architecture

### Top-level layout

- `src/agents/` — Core SDK library
- `src/agents/run_internal/` — Internal runtime helpers (not part of the public API surface)
- `tests/` — Test suite; see `tests/README.md` for snapshot conventions
- `examples/` — Runnable examples demonstrating SDK usage patterns
- `docs/` — MkDocs source; **do not edit** `docs/ja`, `docs/ko`, `docs/zh` (auto-generated translations)

### Runtime flow

`src/agents/run.py` is the public entrypoint (`Runner` / `AgentRunner`). It wires together components from `run_internal/` and stays focused on orchestration. Internal logic lives in:

| File | Responsibility |
|------|----------------|
| `run_internal/run_loop.py` | `run_single_turn` / `run_single_turn_streamed` / `get_new_response` / `start_streaming` |
| `run_internal/turn_resolution.py` | Model output processing and run item extraction |
| `run_internal/tool_execution.py` | Tool call dispatch |
| `run_internal/tool_planning.py` | Tool call planning |
| `run_internal/run_steps.py` | `ProcessedResponse` and tool run structs |
| `run_internal/items.py` | Item normalization, deduplication, approval filtering |
| `run_internal/oai_conversation.py` | Server-managed conversation tracking (`OpenAIServerConversationTracker`) |
| `run_internal/session_persistence.py` | Session save / rewind |
| `run_internal/guardrails.py` | Guardrail execution |

Key public-facing modules:

| File | What it defines |
|------|----------------|
| `agent.py` | `Agent` dataclass |
| `items.py` | `RunItem` types and input/output conversions |
| `run_state.py` | `RunState` — serialized agent run snapshot; has `CURRENT_SCHEMA_VERSION` |
| `stream_events.py` | All streaming event names and types |
| `tool.py` | `FunctionTool`, `HostedTool`, and tool helpers |
| `guardrail.py` | Input/output guardrail types |
| `result.py` | `RunResult` / `RunResultStreaming` |
| `run_config.py` | `RunConfig` |
| `lifecycle.py` | Agent and run lifecycle hooks |
| `exceptions.py` | SDK exception hierarchy |

### Key invariants

- **Streaming and non-streaming paths must stay behaviorally aligned.** Changes to `run_loop.py` should be mirrored across both paths; new streaming item types must be added to `stream_events.py`.
- **Input guardrails run only on the first turn** and only for the starting agent. Resuming from `RunState` must not increment the turn counter.
- **Adding a new tool/output/approval item type** requires coordinated updates across `items.py`, `run_steps.py`, `turn_resolution.py`, `tool_execution.py`, `tool_planning.py`, `run_internal/items.py`, `stream_events.py`, `run_state.py`, and `session_persistence.py`.
- **RunState schema changes** require updating `CURRENT_SCHEMA_VERSION` in `run_state.py`.
- **Public API positional compatibility**: never insert new constructor parameters or dataclass fields in the middle of an existing public parameter list. Append optional fields to the end.

### Documentation pipeline

`docs/scripts/generate_ref_files.py` generates API reference stubs; `docs/scripts/translate_docs.py` produces translated pages. `mkdocs.yml` controls the site navigation.
