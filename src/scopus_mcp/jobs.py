"""Background jobs for long tool calls.

MCP bridges time out (Claude Desktop's remote bridge after 60 s), while a
verified citer set or a 150-paper network can take minutes. Long tools run
as a task: if it finishes within the sync budget the caller gets the result
as usual; otherwise the call returns a job ID at once, the task keeps
running in this server process, and job_status / job_result pick it up.
Every API response is cached as it arrives, so even an abandoned run makes
the next one fast.
"""
import asyncio
import contextvars
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

import mcp.types as types

# Tools that may outlive a bridge timeout.
LONG_TOOLS = {'citation_network', 'resolve_citers', 'citation_lineage',
              'bibliographic_coupling', 'co_citation', 'path_transmission',
              'index_coverage', 'rpys', 'historiograph', 'thematic_evolution',
              'research_fronts'}


def sync_budget() -> float:
    """Seconds to wait before handing back a job ID (SCOPUS_SYNC_BUDGET,
    default 45: under the 60 s bridge timeout)."""
    try:
        return max(0.0, float(os.getenv('SCOPUS_SYNC_BUDGET', 45)))
    except ValueError:
        return 45.0


JOBS: Dict[str, Dict[str, Any]] = {}
_progress: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    'job_progress', default=None)


def progress(message: str) -> None:
    """Record what the current job is doing (no-op outside a job)."""
    state = _progress.get()
    if state is not None:
        state['progress'] = message
        state['updated'] = time.time()


async def run(name: str, arguments: dict,
              handler: Callable[[dict], Awaitable[List[types.TextContent]]]) -> List[types.TextContent]:
    """Run a long tool; past the sync budget, return a job ID instead."""
    job_id = uuid.uuid4().hex[:8]
    state = {'id': job_id, 'tool': name, 'started': time.time(),
             'progress': 'starting', 'updated': time.time()}
    token = _progress.set(state)
    try:
        task = asyncio.create_task(handler(arguments))  # copies the context
    finally:
        _progress.reset(token)
    state['task'] = task
    task.add_done_callback(lambda _t: state.setdefault('finished', time.time()))
    done, _ = await asyncio.wait({task}, timeout=sync_budget())
    if task in done:
        return task.result()
    JOBS[job_id] = state
    return [types.TextContent(type="text", text=(
        f"{name} is still running after {sync_budget():.0f} s, so it continues in the "
        f"background as job {job_id} (last step: {state['progress']}). Call "
        f"job_status(job_id='{job_id}') to follow it and job_result(job_id='{job_id}') "
        "for the output. Responses are cached as they arrive, so nothing is lost."))]


def _age(seconds: float) -> str:
    return f"{seconds:.0f} s" if seconds < 120 else f"{seconds / 60:.1f} min"


def status_text(job_id: str) -> str:
    job = JOBS.get(job_id)
    if job is None:
        known = ', '.join(sorted(JOBS)) or 'none'
        return (f"No job {job_id!r} in this server process (jobs do not survive a "
                f"restart; known jobs: {known}).")
    task = job['task']
    age = _age(job.get('finished', time.time()) - job['started'])
    if not task.done():
        return f"Job {job_id} ({job['tool']}): running for {age}; last step: {job['progress']}."
    if task.exception() is not None:
        return f"Job {job_id} ({job['tool']}): failed after {age}: {task.exception()}"
    return f"Job {job_id} ({job['tool']}): finished after {age}. Call job_result for the output."


def result(job_id: str) -> List[types.TextContent]:
    job = JOBS.get(job_id)
    if job is None or not job['task'].done():
        return [types.TextContent(type="text", text=status_text(job_id))]
    task = job['task']
    if task.exception() is not None:
        return [types.TextContent(type="text", text=f"Error: {task.exception()}")]
    return task.result()


TOOLS = [
    types.Tool(
        name="job_status",
        description=("State of a background job started by a long tool call "
                     "(citation_network, resolve_citers, ...) that passed the "
                     "sync budget: running, finished or failed, and its last step."),
        inputSchema={"type": "object",
                     "properties": {"job_id": {"type": "string"}},
                     "required": ["job_id"]},
    ),
    types.Tool(
        name="job_result",
        description="Output of a finished background job (its status if still running).",
        inputSchema={"type": "object",
                     "properties": {"job_id": {"type": "string"}},
                     "required": ["job_id"]},
    ),
]


async def _job_status(arguments: dict) -> list:
    return [types.TextContent(type="text", text=status_text(str(arguments.get('job_id'))))]


async def _job_result(arguments: dict) -> list:
    return result(str(arguments.get('job_id')))


HANDLERS = {'job_status': _job_status, 'job_result': _job_result}
