import os
import asyncio
import json
import time
import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import traceback
import concurrent.futures

# Custom executor for agent threads to allow cleaner shutdown
executor = concurrent.futures.ThreadPoolExecutor(max_workers=30)

from dotenv import load_dotenv
load_dotenv(override=True)

from cua_loop.approval import approval_event, approval_result

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

from cua_loop.demo_listings import demo_router
app.include_router(demo_router)

@app.on_event("shutdown")
async def shutdown_event():
    print("\n[AEGIS] Shutting down... cancelling agent threads.")
    executor.shutdown(wait=False, cancel_futures=True)

NUM_AGENTS = 9

# ── Shared CSS block reused by all pages ──────────────────────────────────────
SHARED_CSS = """
:root {
    --bg-color: #0f172a;
    --panel-bg: rgba(30, 41, 59, 0.7);
    --panel-border: rgba(255, 255, 255, 0.08);
    --text-main: #f8fafc;
    --text-muted: #94a3b8;
    --primary: #3b82f6;
    --primary-hover: #2563eb;
    --accent: #8b5cf6;
    --success: #10b981;
    --danger: #ef4444;
    --warning: #f59e0b;
}
body { font-family: 'Inter', system-ui, sans-serif; background: var(--bg-color); color: var(--text-main); margin: 0; padding: 0; min-height: 100vh; background-image: radial-gradient(circle at 50% 0%, #1e293b 0%, #0f172a 70%); }
.glass-panel { background: var(--panel-bg); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border: 1px solid var(--panel-border); border-radius: 16px; padding: 24px; box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.3); }
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: rgba(0,0,0,0.1); border-radius: 4px; }
::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }
"""

NAV_HTML = """
<nav style="width:100%;padding:8px 40px;box-sizing:border-box;display:flex;gap:24px;align-items:center;border-bottom:1px solid rgba(255,255,255,0.06);">
    <a href="/" style="color:var(--text-muted);text-decoration:none;font-size:13px;font-weight:500;">Studio</a>
    <a href="/split" style="color:var(--text-muted);text-decoration:none;font-size:13px;font-weight:500;">Compare</a>
    <a href="/swarm" style="color:var(--text-muted);text-decoration:none;font-size:13px;font-weight:500;">Swarm</a>
    <a href="/bargains" style="color:var(--text-muted);text-decoration:none;font-size:13px;font-weight:500;">Bargains</a>
    <a href="/verdicts" style="color:var(--text-muted);text-decoration:none;font-size:13px;font-weight:500;">Verdicts</a>
    <a href="/browsers" style="color:var(--text-muted);text-decoration:none;font-size:13px;font-weight:500;">Browsers</a>
</nav>
"""

def _default_state():
    return {
        "screenshot_url": "",
        "action": {},
        "step": 0,
        "task": "",
        "status": "idle",
        "result": "",
        "verification_passed": None,
        "verification_reason": "",
        "blocked": False,
        "block_reason": "",
    }

# ── Original studio state ────────────────────────────────────────────────────
state = _default_state()
clients = set()

# ── Split-screen state (raw / aegis channels) ────────────────────────────────
state_raw = _default_state()
state_aegis = _default_state()
clients_raw: set = set()
clients_aegis: set = set()

# ── Verdict feed state ───────────────────────────────────────────────────────
verdict_log: list[dict] = []
verdict_clients: set = set()

# ── WebSocket clients for bidirectional approval flow ────────────────────────
ws_clients: set = set()

# ── Browser grid state ───────────────────────────────────────────────────────
browser_sessions: list[dict] = []

# ── Bargain board state ──────────────────────────────────────────────────────
bargain_listings: list[dict] = []

# ── Swarm state ──────────────────────────────────────────────────────────────
swarm_state: dict[str, dict] = {}
swarm_clients: set = set()

async def broadcast(data):
    for q in clients:
        await q.put(data)
    dead_ws = set()
    for ws in ws_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead_ws.add(ws)
    ws_clients.difference_update(dead_ws)

async def broadcast_swarm(agent_id: str, data: dict):
    msg = {"type": "update", "agent_id": agent_id, "data": data}
    for q in swarm_clients:
        await q.put(msg)

async def broadcast_raw(data):
    for q in clients_raw:
        await q.put(data)

async def broadcast_aegis(data):
    for q in clients_aegis:
        await q.put(data)

async def broadcast_verdicts(data):
    for q in verdict_clients:
        await q.put(data)

@app.post("/update")
async def update_state(request: Request, data: dict):
    channel = request.query_params.get("channel", "")
    if channel == "raw":
        state_raw.update({k: v for k, v in data.items() if v is not None})
        await broadcast_raw(state_raw)
    elif channel == "aegis":
        state_aegis.update({k: v for k, v in data.items() if v is not None})
        await broadcast_aegis(state_aegis)
    elif channel.startswith("agent_"):
        if channel not in swarm_state:
            swarm_state[channel] = _default_state()
        swarm_state[channel].update({k: v for k, v in data.items() if v is not None})
        await broadcast_swarm(channel, swarm_state[channel])
    else:
        state.update({k: v for k, v in data.items() if v is not None})
        await broadcast(state)
    return {"status": "ok"}


# ── Human approval flow (WebSocket + HTTP) ──────────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_clients.add(websocket)
    try:
        await websocket.send_json(state)
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "approval_response":
                approved = data.get("approved", False)
                approval_result["approved"] = approved
                approval_event.set()
                decision = "approved" if approved else "denied"
                state["status"] = decision
                state["approval_pending"] = None
                await broadcast(state)
    except WebSocketDisconnect:
        ws_clients.discard(websocket)

@app.post("/approve")
async def approve_action(data: dict):
    approved = data.get("approved", False)
    approval_result["approved"] = approved
    approval_event.set()
    decision = "approved" if approved else "denied"
    state["status"] = decision
    state["approval_pending"] = None
    await broadcast(state)
    return {"status": decision}

class StartRequest(BaseModel):
    task: str
    url: str | None = None
    swarm: bool = False

class VerdictEntry(BaseModel):
    type: str
    result: str
    reason: str
    details: dict = Field(default_factory=dict)

class BrowserRegistration(BaseModel):
    id: str
    live_view_url: str
    marketplace: str = ""

# ── Browser grid API ─────────────────────────────────────────────────────────
@app.post("/api/browsers")
async def register_browser(reg: BrowserRegistration):
    for s in browser_sessions:
        if s["id"] == reg.id:
            s["live_view_url"] = reg.live_view_url
            s["marketplace"] = reg.marketplace
            return {"status": "updated"}
    browser_sessions.append({
        "id": reg.id,
        "live_view_url": reg.live_view_url,
        "marketplace": reg.marketplace,
        "created_at": time.strftime("%H:%M:%S"),
    })
    return {"status": "registered", "count": len(browser_sessions)}

@app.get("/api/browsers")
async def list_browsers():
    return JSONResponse(browser_sessions)

@app.delete("/api/browsers/{browser_id}")
async def remove_browser(browser_id: str):
    before = len(browser_sessions)
    browser_sessions[:] = [s for s in browser_sessions if s["id"] != browser_id]
    return {"removed": before - len(browser_sessions)}

# ── Verdict feed API ─────────────────────────────────────────────────────────
@app.post("/api/verdicts")
async def post_verdict(entry: VerdictEntry):
    record = {
        "type": entry.type,
        "result": entry.result,
        "reason": entry.reason,
        "details": entry.details,
        "ts": time.strftime("%H:%M:%S"),
    }
    verdict_log.insert(0, record)
    if len(verdict_log) > 200:
        verdict_log[:] = verdict_log[:200]
    await broadcast_verdicts(record)
    return {"status": "ok", "count": len(verdict_log)}

@app.get("/api/verdicts")
async def list_verdicts():
    return JSONResponse(verdict_log)

@app.get("/api/verdicts/stream")
async def verdict_stream(request: Request):
    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        verdict_clients.add(q)
        try:
            yield f"data: {json.dumps(verdict_log[:20])}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            verdict_clients.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Bargain board API ────────────────────────────────────────────────────────
def _mock_bargain_data():
    return [
        {"listing": {"title": "Herman Miller Aeron Chair - Size B", "price": 485, "marketplace": "craigslist", "distance_mi": 3.2, "photo_count": 6, "seller": "mike_furnishings", "posted_age_text": "2 hours ago", "condition": "pre-owned", "raw_url": "#"}, "score": 87.4, "accepted": True, "is_replica_suspected": False, "is_scam_suspected": False, "is_stale": False, "reasons": ["within budget: $485.00", "listing recent (<24h)", "4+ photos", "within radius: 3mi"]},
        {"listing": {"title": "Vintage Eames Shell Chair - Fiberglass", "price": 320, "marketplace": "ebay", "distance_mi": None, "photo_count": 8, "seller": "retro_finds_99", "posted_age_text": "5 hours ago", "condition": "used", "raw_url": "#"}, "score": 79.1, "accepted": True, "is_replica_suspected": False, "is_scam_suspected": False, "is_stale": False, "reasons": ["within budget: $320.00", "4+ photos", "used-as-default (second-hand marketplace)"]},
        {"listing": {"title": "West Elm Mid-Century Nightstand - Walnut", "price": 145, "marketplace": "offerup", "distance_mi": 7.8, "photo_count": 4, "seller": "sarah_m", "posted_age_text": "yesterday", "condition": "good", "raw_url": "#"}, "score": 74.6, "accepted": True, "is_replica_suspected": False, "is_scam_suspected": False, "is_stale": False, "reasons": ["within budget: $145.00", "listing recent (<24h)", "4+ photos", "within radius: 8mi"]},
        {"listing": {"title": "Fender Player Stratocaster MIM 2021", "price": 525, "marketplace": "reverb", "distance_mi": None, "photo_count": 12, "seller": "guitar_depot", "posted_age_text": "3 hours ago", "condition": "excellent", "raw_url": "#"}, "score": 72.0, "accepted": True, "is_replica_suspected": False, "is_scam_suspected": False, "is_stale": False, "reasons": ["within budget: $525.00", "4+ photos", "listing recent (<24h)"]},
        {"listing": {"title": "CB2 Sven Sofa - Charcoal", "price": 680, "marketplace": "fb_marketplace", "distance_mi": 4.1, "photo_count": 5, "seller": "jen_decor", "posted_age_text": "today", "condition": "like new", "raw_url": "#"}, "score": 68.2, "accepted": True, "is_replica_suspected": False, "is_scam_suspected": False, "is_stale": False, "reasons": ["within budget: $680.00", "4+ photos", "within radius: 4mi"]},
        {"listing": {"title": "Dyson V15 Detect Cordless Vacuum", "price": 299, "marketplace": "mercari", "distance_mi": None, "photo_count": 3, "seller": "clean_deals", "posted_age_text": "6 hours ago", "condition": "open box", "raw_url": "#"}, "score": 63.5, "accepted": True, "is_replica_suspected": False, "is_scam_suspected": False, "is_stale": False, "reasons": ["within budget: $299.00", "listing recent (<24h)"]},
        {"listing": {"title": "MCM Style Lounge Chair - Inspired by Eames", "price": 220, "marketplace": "fb_marketplace", "distance_mi": 8.3, "photo_count": 2, "seller": "deals4u_2024", "posted_age_text": "3 days ago", "condition": "new", "raw_url": "#"}, "score": 42.1, "accepted": False, "is_replica_suspected": True, "is_scam_suspected": False, "is_stale": False, "reasons": ["rejected: replica/knockoff and user requested authentic", "within radius: 8mi"]},
        {"listing": {"title": "Vintage Desk Lamp - Mid Century MUST SHIP zelle only", "price": 95, "marketplace": "craigslist", "distance_mi": None, "photo_count": 1, "seller": "quicksale_now", "posted_age_text": "1 hours ago", "condition": "used", "raw_url": "#"}, "score": -12.8, "accepted": False, "is_replica_suspected": False, "is_scam_suspected": True, "is_stale": False, "reasons": ["rejected: scam-pattern phrasing matched", "zero photos (high scam risk)"]},
        {"listing": {"title": "IKEA Kallax Shelf 4x4 White", "price": 45, "marketplace": "offerup", "distance_mi": 2.1, "photo_count": 3, "seller": "moving_sale_sf", "posted_age_text": "3 months ago", "condition": "fair", "raw_url": "#"}, "score": 38.9, "accepted": False, "is_replica_suspected": False, "is_scam_suspected": False, "is_stale": True, "reasons": ["listing >1 month old", "within radius: 2mi"]},
        {"listing": {"title": "Knoll Wassily Chair - Leather", "price": 890, "marketplace": "ebay", "distance_mi": None, "photo_count": 7, "seller": "design_classics", "posted_age_text": "2 hours ago", "condition": "pre-owned", "raw_url": "#"}, "score": 31.5, "accepted": False, "is_replica_suspected": False, "is_scam_suspected": False, "is_stale": False, "reasons": ["over budget after shipping: $890.00 > $800.00", "4+ photos"]},
    ]

@app.post("/api/bargains")
async def post_bargains(data: list[dict]):
    bargain_listings.clear()
    bargain_listings.extend(data)
    return {"status": "ok", "count": len(bargain_listings)}

@app.get("/api/bargains")
async def get_bargains():
    if not bargain_listings:
        return JSONResponse(_mock_bargain_data())
    return JSONResponse(bargain_listings)

# ── Split-screen SSE endpoints ───────────────────────────────────────────────
def _make_sse_endpoint(target_state, target_clients):
    async def sse(request: Request):
        async def gen():
            q: asyncio.Queue = asyncio.Queue()
            target_clients.add(q)
            try:
                yield f"data: {json.dumps(target_state)}\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        data = await asyncio.wait_for(q.get(), timeout=15.0)
                        yield f"data: {json.dumps(data)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                target_clients.discard(q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return sse

app.get("/split/stream/raw")(_make_sse_endpoint(state_raw, clients_raw))
app.get("/split/stream/aegis")(_make_sse_endpoint(state_aegis, clients_aegis))

@app.get("/swarm/stream")
async def swarm_stream(request: Request):
    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        swarm_clients.add(q)
        try:
            # Send initial full state
            yield f"data: {json.dumps({'type': 'full_state', 'data': swarm_state})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            swarm_clients.discard(q)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Split-screen agent runners ───────────────────────────────────────────────
def run_split_raw_sync(url: str | None, task: str):
    import httpx
    from cua_loop.client import run_single_attempt
    try:
        traj = run_single_attempt(task=task, url=url, channel="raw", skip_safety=True)
        try:
            httpx.post("http://localhost:8555/update?channel=raw",
                       json={"status": "failed", "result": f"Raw CUA finished after {len(traj.steps)} steps. {traj.error or traj.final_message or 'No safety checks applied.'}"},
                       timeout=10.0)
        except Exception:
            pass
    except Exception as e:
        try:
            httpx.post("http://localhost:8555/update?channel=raw",
                       json={"status": "failed", "result": f"{e}"},
                       timeout=10.0)
        except Exception:
            pass

def run_split_aegis_sync(url: str | None, task: str):
    import httpx
    from cua_loop.runner import run_with_retry
    try:
        max_attempts = int(os.getenv("CUA_MAX_ATTEMPTS", "5"))
        result = run_with_retry(task=task, url=url, max_attempts=max_attempts, channel="aegis")
        last = result.attempts[-1] if result.attempts else None
        rows = last.verifier.rows_extracted if last else 0
        reason = last.verifier.reason if last else "no attempts ran"
        status = "success" if result.success else "failed"
        try:
            httpx.post("http://localhost:8555/update?channel=aegis",
                       json={"status": status, "result": f"AEGIS: {status} — {rows} rows in {result.total_duration_s:.1f}s. {reason}"},
                       timeout=10.0)
        except Exception:
            pass
    except Exception as e:
        try:
            httpx.post("http://localhost:8555/update?channel=aegis",
                       json={"status": "failed", "result": f"{e}"},
                       timeout=10.0)
        except Exception:
            pass

@app.post("/split/start")
async def start_split(req: StartRequest):
    for s in (state_raw, state_aegis):
        s.update(_default_state())
        s["status"] = "running"
        s["task"] = req.task
    await broadcast_raw(state_raw)
    await broadcast_aegis(state_aegis)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(executor, run_split_raw_sync, req.url, req.task)
    loop.run_in_executor(executor, run_split_aegis_sync, req.url, req.task)
    return {"status": "started"}

# ── Original agent runner ────────────────────────────────────────────────────
def run_agent_sync(url: str | None, task: str):
    import httpx
    from cua_loop.runner import run_with_retry
    try:
        max_attempts = int(os.getenv("CUA_MAX_ATTEMPTS", "5"))
        clean_url = url.strip() if url else None
        result = run_with_retry(task=task, url=clean_url or None, max_attempts=max_attempts)

        last = result.attempts[-1] if result.attempts else None
        rows = last.verifier.rows_extracted if last else 0
        reason = last.verifier.reason if last else "no attempts ran"
        attempts_used = len(result.attempts)

        if result.success:
            payload = {
                "status": "success",
                "result": (
                    f"Success on attempt {attempts_used}/{max_attempts} — "
                    f"extracted {rows} rows in {result.total_duration_s:.1f}s. "
                    f"Reason: {reason}"
                ),
            }
        else:
            payload = {
                "status": "failed",
                "result": (
                    f"Failed after {attempts_used}/{max_attempts} attempts "
                    f"({result.total_duration_s:.1f}s). Last reason: {reason}"
                ),
            }
        try:
            httpx.post("http://localhost:8555/update", json=payload, timeout=10.0)
        except Exception:
            pass
    except Exception as e:
        try:
            httpx.post(
                "http://localhost:8555/update",
                json={"status": "failed", "result": f"{e}\n{traceback.format_exc()}"},
                timeout=10.0,
            )
        except Exception:
            pass

def run_swarm_sync(url: str | None, task: str):
    import httpx
    from cua_loop.scaling import run_wide_scaling
    try:
        width = 9  # default swarm width
        clean_url = url.strip() if url else None
        result = run_wide_scaling(task=task, url=clean_url or None, width=width)

        status = "success" if result.success else "failed"
        rows = len(result.extracted) if result.extracted else 0
        payload = {
            "status": status,
            "result": f"Swarm finished: {status} — extracted {rows} total rows in {result.total_duration_s:.1f}s."
        }
        try:
            httpx.post("http://localhost:8555/update", json=payload, timeout=10.0)
        except Exception:
            pass
    except Exception as e:
        try:
            httpx.post(
                "http://localhost:8555/update",
                json={"status": "failed", "result": f"Swarm error: {e}\n{traceback.format_exc()}"},
                timeout=10.0,
            )
        except Exception:
            pass

def run_orchestrated_swarm_sync(task: str, num_agents: int = 6):
    """Orchestrate the swarm, fan agents out to a thread pool, broadcast each
    agent's progress on its own channel so the grid lights up.

    Bypasses scaling.run_orchestrated_swarm because its signature
    (task, agent_tasks) doesn't match num_agents and was silently TypeError'ing
    on every call. We do the orchestration + execution + light synthesis here.
    """
    import time as _time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import httpx

    from cua_loop.client import run_single_attempt
    from cua_loop.swarm_orchestrator import run_swarm_orchestration
    from cua_loop.types import Trajectory

    def _run_one(agent_idx: int, agent_task) -> dict:
        channel = f"agent_{agent_idx}"
        # Seed the per-agent UI cell with role + task so it's not blank while
        # the model spins up.
        try:
            httpx.post(
                f"http://localhost:8555/update?channel={channel}",
                json={
                    "status": "running",
                    "task": getattr(agent_task, "task_description", "") or "",
                    "role_name": getattr(agent_task, "role_name", f"Agent {agent_idx + 1}"),
                    "task_description": getattr(agent_task, "task_description", "") or "",
                    "step": 0,
                },
                timeout=2.0,
            )
        except Exception:
            pass

        started = _time.time()
        try:
            traj = run_single_attempt(
                task=getattr(agent_task, "task_description", task) or task,
                url=None,
                extra_context=getattr(agent_task, "specific_instructions", "") or "",
                channel=channel,
            )
            success = not bool(traj.error)
        except Exception as exc:
            traj = Trajectory(task=task, url=None, error=str(exc))
            success = False

        try:
            httpx.post(
                f"http://localhost:8555/update?channel={channel}",
                json={
                    "status": "success" if success else "failed",
                    "result": (traj.final_message or traj.error or "")[:300],
                },
                timeout=2.0,
            )
        except Exception:
            pass

        return {
            "agent_id": channel,
            "role_name": getattr(agent_task, "role_name", f"Agent {agent_idx + 1}"),
            "task_description": getattr(agent_task, "task_description", "") or "",
            "success": success,
            "duration_s": _time.time() - started,
            "final_message": traj.final_message,
            "error": traj.error,
        }

    try:
        # 1) Orchestrate
        plan = run_swarm_orchestration(task=task, num_agents=num_agents)
        agent_tasks = list(plan.agent_tasks or [])
        intent = plan.intent

        if not agent_tasks:
            httpx.post(
                "http://localhost:8555/update",
                json={
                    "status": "failed",
                    "result": "Orchestrator returned 0 agents. Check OPENAI/ANTHROPIC keys for the orchestrator LLM.",
                },
                timeout=10.0,
            )
            return

        # 2) Run all agents in parallel; each posts to its own channel.
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=len(agent_tasks)) as pool:
            futures = {
                pool.submit(_run_one, i, t): i for i, t in enumerate(agent_tasks)
            }
            for future in as_completed(futures):
                results.append(future.result())

        # 3) Light synthesis — main UI summary.
        success_count = sum(1 for r in results if r["success"])
        result_msg = (
            f"Orchestrated swarm done — {success_count}/{len(results)} agents succeeded.\n"
            f"Goal: {getattr(intent, 'true_goal', task)}"
        )
        payload = {
            "status": "success" if success_count > 0 else "failed",
            "result": result_msg,
            "intent": {
                "true_goal": getattr(intent, "true_goal", task),
                "success_criteria": list(getattr(intent, "success_criteria", []) or []),
                "key_entities": list(getattr(intent, "key_entities", []) or []),
            },
            "agent_roles": [
                {
                    "agent_id": r["agent_id"],
                    "role_name": r["role_name"],
                    "task": r["task_description"],
                }
                for r in sorted(results, key=lambda r: r["agent_id"])
            ],
        }
        try:
            httpx.post("http://localhost:8555/update", json=payload, timeout=10.0)
        except Exception:
            pass
        return

    except Exception as e:
        try:
            httpx.post(
                "http://localhost:8555/update",
                json={"status": "failed", "result": f"Orchestrated swarm error: {e}\n{traceback.format_exc()}"},
                timeout=10.0,
            )
        except Exception:
            pass
        return

    # ── unreachable legacy path below; kept dormant for diff minimization ──
    try:
        from cua_loop.scaling import run_orchestrated_swarm
        result = run_orchestrated_swarm(task=task, agent_tasks=[])  # type: ignore[arg-type]
        synthesis = getattr(result, '_synthesis', None)
        intent = getattr(result, '_intent', None)
        agent_tasks = getattr(result, '_agent_tasks', [])
        
        # Build detailed result message
        if synthesis:
            report = synthesis.final_report[:500] + "..." if len(synthesis.final_report) > 500 else synthesis.final_report
            result_msg = (
                f"Orchestrated Swarm Complete!\n"
                f"Confidence: {synthesis.confidence_score:.0%}\n"
                f"Agents: {len(agent_tasks)} | Findings: {len(synthesis.key_findings)}\n\n"
                f"Key Findings:\n" + "\n".join(f"• {f}" for f in synthesis.key_findings[:5]) + "\n\n"
                f"Report:\n{report}"
            )
        else:
            result_msg = f"Swarm completed with {len(result.attempts)} agents."
        
        payload = {
            "status": "success" if result.success else "failed",
            "result": result_msg,
            "synthesis": {
                "final_report": synthesis.final_report if synthesis else "",
                "key_findings": synthesis.key_findings if synthesis else [],
                "confidence_score": synthesis.confidence_score if synthesis else 0.0,
                "gaps": synthesis.gaps_or_uncertainties if synthesis else [],
            } if synthesis else None,
            "intent": {
                "true_goal": intent.true_goal if intent else task,
                "success_criteria": intent.success_criteria if intent else [],
                "key_entities": intent.key_entities if intent else [],
            } if intent else None,
            "agent_roles": [
                {"agent_id": t.agent_id, "role_name": t.role_name, "task": t.task_description}
                for t in agent_tasks
            ],
        }
        
        try:
            httpx.post("http://localhost:8555/update", json=payload, timeout=30.0)
        except Exception:
            pass
            
        # Also update per-agent state for the swarm view
        for attempt in result.attempts:
            agent_key = f"agent_{attempt.attempt_index}"
            task_info = next((t for t in agent_tasks if t.agent_id == attempt.attempt_index), None)
            role_name = task_info.role_name if task_info else f"Agent {attempt.attempt_index}"
            
            agent_payload = {
                "status": "success" if attempt.verifier.success else "failed",
                "result": attempt.verifier.reason,
                "role_name": role_name,
                "task_description": task_info.task_description if task_info else "",
            }
            try:
                httpx.post(f"http://localhost:8555/update?channel={agent_key}", json=agent_payload, timeout=10.0)
            except Exception:
                pass
                
    except Exception as e:
        try:
            httpx.post(
                "http://localhost:8555/update",
                json={"status": "failed", "result": f"Orchestrated swarm error: {e}\n{traceback.format_exc()}"},
                timeout=10.0,
            )
        except Exception:
            pass

@app.post("/start")
async def start_agent(req: StartRequest):
    state["status"] = "running"
    state["task"] = req.task
    state["screenshot_url"] = ""
    state["action"] = {}
    state["step"] = 0
    state["result"] = ""
    await broadcast(state)

    loop = asyncio.get_event_loop()
    if req.swarm:
        loop.run_in_executor(executor, run_swarm_sync, req.url, req.task)
    else:
        loop.run_in_executor(executor, run_agent_sync, req.url, req.task)
    return {"status": "started"}

class SwarmOrchestrateRequest(BaseModel):
    task: str
    num_agents: int = 6

@app.post("/swarm/orchestrate")
async def swarm_orchestrate(req: SwarmOrchestrateRequest):
    from cua_loop.swarm_orchestrator import run_swarm_orchestration
    try:
        plan = run_swarm_orchestration(task=req.task, num_agents=req.num_agents)
        return {
            "status": "ok",
            "intent": {
                "true_goal": plan.intent.true_goal,
                "desired_output_format": plan.intent.desired_output_format,
                "success_criteria": plan.intent.success_criteria,
                "key_entities": plan.intent.key_entities,
                "suggested_num_agents": plan.intent.suggested_num_agents,
            },
            "agent_tasks": [
                {
                    "agent_id": t.agent_id,
                    "role_name": t.role_name,
                    "task_description": t.task_description,
                    "specific_instructions": t.specific_instructions,
                    "expected_output": t.expected_output,
                }
                for t in plan.agent_tasks
            ],
        }
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": str(e)},
            status_code=500
        )

@app.post("/swarm/run")
async def swarm_run(req: SwarmOrchestrateRequest):
    state["status"] = "running"
    state["task"] = req.task
    state["screenshot_url"] = ""
    state["action"] = {}
    state["step"] = 0
    state["result"] = ""
    await broadcast(state)
    
    swarm_state.clear()
    
    loop = asyncio.get_event_loop()
    loop.run_in_executor(executor, run_orchestrated_swarm_sync, req.task, req.num_agents)
    return {"status": "orchestrated_swarm_started"}

@app.get("/stream")
async def stream(request: Request):
    async def event_generator():
        q = asyncio.Queue()
        clients.add(q)
        try:
            yield f"data: {json.dumps(state)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            clients.discard(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.get("/", response_class=HTMLResponse)
async def index():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Symphony CUA Studio</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-color: #0f172a;
                --panel-bg: rgba(30, 41, 59, 0.7);
                --panel-border: rgba(255, 255, 255, 0.08);
                --text-main: #f8fafc;
                --text-muted: #94a3b8;
                --primary: #3b82f6;
                --primary-hover: #2563eb;
                --accent: #8b5cf6;
                --success: #10b981;
                --danger: #ef4444;
                --warning: #f59e0b;
            }
            
            body { font-family: 'Inter', system-ui, sans-serif; background: var(--bg-color); color: var(--text-main); margin: 0; padding: 0; display: flex; flex-direction: column; align-items: center; min-height: 100vh; background-image: radial-gradient(circle at 50% 0%, #1e293b 0%, #0f172a 70%); }
            
            .header { width: 100%; padding: 20px 40px; display: flex; justify-content: space-between; align-items: center; box-sizing: border-box; }
            .header h1 { font-size: 20px; margin: 0; font-weight: 700; letter-spacing: -0.02em; background: linear-gradient(135deg, #fff 0%, #cbd5e1 100%); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
            
            #container { display: flex; gap: 24px; width: 100%; max-width: 1600px; padding: 0 40px; box-sizing: border-box; height: calc(100vh - 100px); }
            
            .glass-panel { background: var(--panel-bg); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px); border: 1px solid var(--panel-border); border-radius: 16px; padding: 24px; box-shadow: 0 20px 40px -10px rgba(0, 0, 0, 0.3); }
            
            #sidebar { width: 420px; display: flex; flex-direction: column; gap: 20px; overflow-y: auto; }
            
            /* Form Styles */
            .input-group { display: flex; flex-direction: column; gap: 8px; margin-bottom: 20px; }
            .input-group label { font-size: 12px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
            .input-group input, .input-group textarea { background: rgba(15, 23, 42, 0.6); border: 1px solid var(--panel-border); color: white; padding: 12px 16px; border-radius: 10px; font-size: 14px; outline: none; transition: all 0.2s; font-family: 'Inter', sans-serif; }
            .input-group input:focus, .input-group textarea:focus { border-color: var(--primary); box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15); }
            .input-group textarea { resize: vertical; min-height: 80px; }
            
            .btn-start { background: linear-gradient(135deg, var(--primary) 0%, var(--accent) 100%); color: white; border: none; padding: 14px 24px; border-radius: 10px; font-weight: 600; font-size: 15px; cursor: pointer; transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); width: 100%; display: flex; justify-content: center; align-items: center; gap: 8px; box-shadow: 0 4px 15px rgba(59, 130, 246, 0.3); }
            .btn-start:hover { transform: translateY(-2px); box-shadow: 0 8px 25px rgba(59, 130, 246, 0.4); }
            .btn-start:disabled { opacity: 0.6; cursor: not-allowed; transform: none; background: #334155; box-shadow: none; }
            
            /* Status Indicator */
            .status-container { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px solid var(--panel-border); }
            .status-badge { display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 99px; font-size: 13px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
            
            .status-idle { background: rgba(148, 163, 184, 0.1); color: var(--text-muted); border: 1px solid rgba(148, 163, 184, 0.2); }
            .status-running { background: rgba(59, 130, 246, 0.1); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.2); }
            .status-success { background: rgba(16, 185, 129, 0.1); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.2); }
            .status-failed { background: rgba(239, 68, 68, 0.1); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.2); }
            
            .status-dot { width: 8px; height: 8px; border-radius: 50%; }
            .status-running .status-dot { background: #60a5fa; animation: pulse 1.5s infinite; box-shadow: 0 0 10px #60a5fa; }
            .status-success .status-dot { background: #34d399; box-shadow: 0 0 10px #34d399; }
            .status-failed .status-dot { background: #f87171; box-shadow: 0 0 10px #f87171; }
            .status-idle .status-dot { background: var(--text-muted); }
            
            @keyframes pulse {
                0% { transform: scale(0.95); opacity: 0.5; }
                50% { transform: scale(1.2); opacity: 1; }
                100% { transform: scale(0.95); opacity: 0.5; }
            }
            
            /* Action Output */
            .data-row { margin-bottom: 12px; }
            .data-label { font-size: 12px; color: var(--text-muted); margin-bottom: 6px; font-weight: 500; }
            .data-value { font-size: 14px; color: var(--text-main); word-break: break-all; }
            
            pre { margin: 0; white-space: pre-wrap; font-size: 13px; color: #a5b4fc; background: rgba(15, 23, 42, 0.8); padding: 16px; border-radius: 10px; border: 1px solid var(--panel-border); font-family: 'JetBrains Mono', monospace; max-height: 200px; overflow-y: auto; }
            
            /* Browser View */
            #browser-view { flex: 1; display: flex; flex-direction: column; overflow: hidden; padding: 0; }
            .browser-header { background: rgba(15, 23, 42, 0.8); padding: 12px 20px; border-bottom: 1px solid var(--panel-border); display: flex; gap: 8px; align-items: center; }
            .browser-content { position: relative; flex: 1; display: flex; align-items: center; justify-content: center; background: #000; overflow: hidden; }
            #screenshot { max-width: 100%; max-height: 100%; object-fit: contain; opacity: 0; transition: opacity 0.5s ease; }
            #screenshot.loaded { opacity: 1; }
            
            /* Custom Scrollbar */
            ::-webkit-scrollbar { width: 8px; height: 8px; }
            ::-webkit-scrollbar-track { background: rgba(0,0,0,0.1); border-radius: 4px; }
            ::-webkit-scrollbar-thumb { background: rgba(255,255,255,0.1); border-radius: 4px; }
            ::-webkit-scrollbar-thumb:hover { background: rgba(255,255,255,0.2); }
            
            /* Cursor overlay */
            #cursor { 
                position: absolute; width: 36px; height: 36px; 
                background: radial-gradient(circle, rgba(59, 130, 246, 0.8) 0%, rgba(59, 130, 246, 0.2) 60%, transparent 100%);
                border: 2px solid #60a5fa; border-radius: 50%; 
                pointer-events: none; transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1); 
                display: none; transform: translate(-50%, -50%); z-index: 10;
                box-shadow: 0 0 20px rgba(59, 130, 246, 0.6);
            }
            .cursor-click { animation: click-ripple 0.6s cubic-bezier(0.1, 0.8, 0.3, 1); }
            @keyframes click-ripple {
                0% { transform: translate(-50%, -50%) scale(0.8); opacity: 1; border-width: 4px; }
                100% { transform: translate(-50%, -50%) scale(2); opacity: 0; border-width: 0px; }
            }
            
            .loader { border: 2px solid rgba(255,255,255,0.1); border-top-color: white; border-radius: 50%; width: 16px; height: 16px; animation: spin 1s linear infinite; display: none; }
            @keyframes spin { to { transform: rotate(360deg); } }
            
            #result-box { display: none; margin-top: 15px; padding: 15px; border-radius: 10px; font-size: 13px; line-height: 1.5; }
            .result-success { background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3); color: #a7f3d0; }
            .result-failed { background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); color: #fecaca; }
            /* Approval Modal */
            .status-approval_needed { background: rgba(245, 158, 11, 0.1); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.2); }
            .status-approval_needed .status-dot { background: #fbbf24; animation: pulse 1.5s infinite; box-shadow: 0 0 10px #fbbf24; }
            .status-approved { background: rgba(16, 185, 129, 0.1); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.2); }
            .status-approved .status-dot { background: #34d399; box-shadow: 0 0 10px #34d399; }
            .status-denied { background: rgba(239, 68, 68, 0.1); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.2); }
            .status-denied .status-dot { background: #f87171; box-shadow: 0 0 10px #f87171; }
            #approval-modal { display: none; margin-top: 15px; padding: 20px; border-radius: 12px; background: rgba(245, 158, 11, 0.08); border: 1px solid rgba(245, 158, 11, 0.3); animation: slideIn 0.3s ease; }
            @keyframes slideIn { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: translateY(0); } }
            #approval-modal .modal-title { font-size: 13px; font-weight: 700; color: #fbbf24; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; }
            #approval-modal .modal-action { font-size: 14px; color: var(--text-main); margin-bottom: 8px; line-height: 1.5; }
            #approval-modal .modal-reason { font-size: 13px; color: var(--text-muted); margin-bottom: 16px; }
            .btn-approve, .btn-deny { border: none; padding: 10px 24px; border-radius: 8px; font-weight: 700; font-size: 14px; cursor: pointer; transition: all 0.2s; font-family: 'Inter', sans-serif; }
            .btn-approve { background: var(--success); color: white; }
            .btn-approve:hover { background: #059669; transform: translateY(-1px); }
            .btn-deny { background: var(--danger); color: white; }
            .btn-deny:hover { background: #dc2626; transform: translateY(-1px); }
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Symphony CUA Studio</h1>
        </div>
        
        <div id="container">
            <div id="sidebar">
                <div class="glass-panel">
                    <div class="status-container">
                        <div>
                            <div class="data-label">AGENT STATUS</div>
                            <div id="status-badge" class="status-badge status-idle">
                                <div class="status-dot"></div>
                                <span id="status-text">IDLE</span>
                            </div>
                        </div>
                        <div style="text-align: right;">
                            <div class="data-label">CURRENT STEP</div>
                            <div style="font-size: 24px; font-weight: 700; color: var(--primary);"><span id="step-count">0</span><span style="font-size: 14px; color: var(--text-muted); font-weight: 500;">/40</span></div>
                        </div>
                    </div>
                    
                    <div class="input-group">
                        <label>Target URL <span style="opacity:.6; text-transform:none; letter-spacing:0;">(optional)</span></label>
                        <input type="text" id="target-url" value="" placeholder="https://… (leave blank to let the agent navigate itself)">
                    </div>
                    
                    <div class="input-group">
                        <label>Agent Prompt</label>
                        <textarea id="target-task" placeholder="e.g. extract top 10 stories with title, url, points as a table">extract top 10 stories with title, url, points as a table</textarea>
                    </div>

                    <button id="btn-start" class="btn-start" onclick="startAgent()">
                        <span>Start Agent</span>
                        <div id="btn-loader" class="loader"></div>
                    </button>
                    
                    <div id="result-box"></div>
                    <div id="approval-modal">
                        <div class="modal-title">APPROVAL REQUIRED</div>
                        <div class="modal-action" id="approval-action"></div>
                        <div class="modal-reason" id="approval-reason"></div>
                        <div style="display:flex; gap:12px;">
                            <button class="btn-approve" onclick="respondApproval(true)">Approve</button>
                            <button class="btn-deny" onclick="respondApproval(false)">Deny</button>
                        </div>
                    </div>
                </div>
                
                <div class="glass-panel">
                    <div class="data-row">
                        <div class="data-label">CURRENT ACTION</div>
                        <div class="data-value" id="action-type" style="font-weight: 600; color: var(--accent); text-transform: uppercase;">-</div>
                    </div>
                    <div class="data-label">ACTION PAYLOAD</div>
                    <pre id="action-details">{}</pre>
                </div>
            </div>
            
            <div id="browser-view" class="glass-panel">
                <div class="browser-header">
                    <div class="dot dot-red"></div>
                    <div class="dot dot-yellow"></div>
                    <div class="dot dot-green"></div>
                    <div style="margin-left: 15px; font-size: 12px; color: var(--text-muted); font-family: monospace;" id="url-bar">about:blank</div>
                </div>
                <div class="browser-content">
                    <img id="screenshot" src="" alt=""/>
                    <div id="cursor"></div>
                </div>
            </div>
        </div>
        
        <script>
            const evtSource = new EventSource("/stream");
            const screenshot = document.getElementById("screenshot");
            const actionDetails = document.getElementById("action-details");
            const actionType = document.getElementById("action-type");
            const stepCount = document.getElementById("step-count");
            const cursor = document.getElementById("cursor");
            const statusBadge = document.getElementById("status-badge");
            const statusText = document.getElementById("status-text");
            const btnStart = document.getElementById("btn-start");
            const btnLoader = document.getElementById("btn-loader");
            const urlBar = document.getElementById("url-bar");
            const resultBox = document.getElementById("result-box");
            
            const DISPLAY_WIDTH = 1280;
            const DISPLAY_HEIGHT = 720;
            
            screenshot.onload = () => screenshot.classList.add('loaded');
            
            async function startAgent() {
                const url = document.getElementById("target-url").value.trim();
                const task = document.getElementById("target-task").value.trim();

                if (!task) return alert("Task is required.");

                btnStart.disabled = true;
                btnLoader.style.display = "block";
                btnStart.querySelector('span').innerText = "Starting Agent...";
                resultBox.style.display = "none";

                const body = { task, swarm: false };
                if (url) body.url = url;
                try {
                    await fetch("/start", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(body)
                    });
                } catch(e) {
                    alert("Failed to start: " + e);
                    btnStart.disabled = false;
                    btnLoader.style.display = "none";
                    btnStart.querySelector('span').innerText = "Start Agent";
                }
            }
            

            const approvalModal = document.getElementById("approval-modal");
            const approvalAction = document.getElementById("approval-action");
            const approvalReason = document.getElementById("approval-reason");
            
            function respondApproval(approved) {
                fetch("/approve", {
                    method: "POST",
                    headers: {"Content-Type": "application/json"},
                    body: JSON.stringify({approved})
                });
                approvalModal.style.display = "none";
            }
            
            evtSource.onmessage = function(event) {
                const data = JSON.parse(event.data);
                
                if (data.status) {
                    statusBadge.className = `status-badge status-${data.status}`;
                    statusText.innerText = data.status.toUpperCase();
                    
                    if (data.status === 'running') {
                        btnStart.disabled = true;
                        btnLoader.style.display = "block";
                        btnStart.querySelector('span').innerText = "Agent Running...";
                        resultBox.style.display = "none";
                    } else if (data.status === 'success' || data.status === 'failed') {
                        btnStart.disabled = false;
                        btnLoader.style.display = "none";
                        btnStart.querySelector('span').innerText = "Start Agent";
                        
                        if (data.result) {
                            resultBox.style.display = "block";
                            resultBox.className = data.status === 'success' ? 'result-success' : 'result-failed';
                            resultBox.innerText = data.result;
                        }
                    } else if (data.status === 'approval_needed') {
                        approvalModal.style.display = "block";
                        btnStart.disabled = true;
                        btnLoader.style.display = "none";
                        btnStart.querySelector('span').innerText = "Awaiting Approval...";
                        if (data.approval_pending) {
                            const act = data.approval_pending;
                            approvalAction.innerText = "Agent wants to: " + (act.type || "unknown action") + (act.text ? " \u2014 " + act.text : "");
                        }
                        approvalReason.innerText = data.block_reason || "";
                    } else if (data.status === 'approved' || data.status === 'denied') {
                        approvalModal.style.display = "none";
                        btnStart.disabled = true;
                        btnLoader.style.display = "block";
                        btnStart.querySelector('span').innerText = "Agent Running...";
                    } else {
                        btnStart.disabled = false;
                        btnLoader.style.display = "none";
                        btnStart.querySelector('span').innerText = "Start Agent";
                    }
                }
                
                if (data.screenshot_url && data.screenshot_url !== screenshot.src) {
                    screenshot.classList.remove('loaded');
                    screenshot.src = data.screenshot_url;
                }
                
                if (data.step !== undefined) stepCount.innerText = data.step;
                
                if (data.action && Object.keys(data.action).length > 0) {
                    if (data.action.url) urlBar.innerText = data.action.url;
                    
                    actionType.innerText = data.action.type || 'unknown';
                    actionDetails.innerText = JSON.stringify(data.action, null, 2);
                    
                    if (data.action.x !== undefined && data.action.y !== undefined) {
                        cursor.style.display = 'block';
                        
                        const updateCursor = () => {
                            if (!screenshot.complete) return;
                            const imgRect = screenshot.getBoundingClientRect();
                            const scale = Math.min(imgRect.width / DISPLAY_WIDTH, imgRect.height / DISPLAY_HEIGHT);
                            
                            const actualWidth = DISPLAY_WIDTH * scale;
                            const actualHeight = DISPLAY_HEIGHT * scale;
                            const offsetX = (imgRect.width - actualWidth) / 2;
                            const offsetY = (imgRect.height - actualHeight) / 2;
                            
                            const cursorX = offsetX + (data.action.x * scale);
                            const cursorY = offsetY + (data.action.y * scale);
                            
                            const containerRect = screenshot.parentElement.getBoundingClientRect();
                            const finalX = (imgRect.left - containerRect.left) + cursorX;
                            const finalY = (imgRect.top - containerRect.top) + cursorY;
                            
                            cursor.style.left = finalX + 'px';
                            cursor.style.top = finalY + 'px';
                            
                            if (data.action.type === 'click') {
                                cursor.classList.remove('cursor-click');
                                void cursor.offsetWidth;
                                cursor.classList.add('cursor-click');
                            }
                        };
                        
                        if (screenshot.complete) updateCursor();
                        else screenshot.addEventListener('load', updateCursor, { once: true });
                        window.onresize = updateCursor;
                    } else {
                        cursor.style.display = 'none';
                    }
                }
            };
        </script>
    </body>
    </html>
    """

@app.get("/swarm", response_class=HTMLResponse)
async def swarm_page():
    return f"""<!DOCTYPE html>
    <html>
    <head>
        <title>AEGIS - Orchestrated Swarm</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            {SHARED_CSS}
            body {{ margin: 0; padding: 0; overflow-x: hidden; min-height: 100vh; width: 100vw; display: flex; flex-direction: column; }}
            
            .main-layout {{ display: flex; flex-direction: column; height: calc(100vh - 40px); }}
            
            .intent-panel {{
                background: linear-gradient(135deg, rgba(139, 92, 246, 0.08) 0%, rgba(59, 130, 246, 0.08) 100%);
                border-bottom: 1px solid rgba(139, 92, 246, 0.2);
                padding: 16px 40px;
                display: none;
            }}
            .intent-panel.visible {{ display: block; }}
            .intent-title {{ font-size: 11px; color: var(--accent); font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 8px; }}
            .intent-goal {{ font-size: 15px; color: var(--text-main); font-weight: 600; line-height: 1.4; }}
            .intent-meta {{ display: flex; gap: 16px; margin-top: 8px; font-size: 12px; color: var(--text-muted); }}
            .intent-chip {{ background: rgba(255,255,255,0.06); padding: 2px 8px; border-radius: 4px; }}
            
            .grid-container {{ 
                display: grid; 
                grid-template-columns: repeat(3, 1fr); 
                flex: 1;
                gap: 1px; 
                background: var(--panel-border);
                overflow-y: auto;
                min-height: 0;
            }}
            
            .agent-view {{ 
                position: relative; 
                background: #000; 
                overflow: hidden; 
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 200px;
            }}
            
            .screenshot-img {{ 
                max-width: 100%; 
                max-height: 100%; 
                object-fit: contain; 
                opacity: 0; 
                transition: opacity 0.3s ease; 
                position: absolute; 
                top: 0; left: 0; width: 100%; height: 100%;
            }}
            .screenshot-img.loaded {{ opacity: 1; }}
            
            .agent-overlay {{
                position: absolute;
                bottom: 0; left: 0; right: 0;
                padding: 14px 16px;
                background: linear-gradient(to top, rgba(0,0,0,0.95) 0%, rgba(0,0,0,0.6) 60%, transparent 100%);
                display: flex;
                justify-content: space-between;
                align-items: flex-end;
                pointer-events: none;
                z-index: 5;
            }}
            
            .agent-role {{ font-size: 12px; color: var(--accent); font-weight: 700; letter-spacing: 0.02em; margin-bottom: 2px; }}
            .agent-id {{ font-size: 10px; color: var(--text-muted); font-weight: 500; letter-spacing: 0.05em; text-transform: uppercase; }}
            .action-text {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--primary); margin-top: 4px; }}
            .result-text {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; margin-top: 4px; word-break: break-all; max-width: 200px; }}
            .task-preview {{ font-size: 10px; color: rgba(255,255,255,0.4); margin-top: 2px; max-width: 200px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
            
            .status-dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }}
            .agent-view.status-idle .status-dot {{ background: var(--text-muted); }}
            .agent-view.status-running .status-dot {{ background: var(--primary); animation: pulse 1.5s infinite; box-shadow: 0 0 10px var(--primary); }}
            .agent-view.status-success .status-dot {{ background: var(--success); box-shadow: 0 0 10px var(--success); }}
            .agent-view.status-failed .status-dot {{ background: var(--danger); box-shadow: 0 0 10px var(--danger); }}
            
            @keyframes pulse {{
                0% {{ transform: scale(0.95); opacity: 0.5; }}
                50% {{ transform: scale(1.2); opacity: 1; }}
                100% {{ transform: scale(0.95); opacity: 0.5; }}
            }}

            .step-counter {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted); }}
            .step-counter span {{ color: var(--text-main); font-weight: 600; }}

            .synthesis-panel {{
                background: linear-gradient(135deg, rgba(245, 158, 11, 0.06) 0%, rgba(245, 158, 11, 0.02) 100%);
                border-top: 2px solid rgba(245, 158, 11, 0.3);
                padding: 20px 40px;
                max-height: 250px;
                overflow-y: auto;
                display: none;
            }}
            .synthesis-panel.visible {{ display: block; }}
            .synthesis-title {{ font-size: 12px; color: var(--warning); font-weight: 700; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 12px; display: flex; align-items: center; gap: 8px; }}
            .synthesis-report {{ font-size: 13px; color: var(--text-main); line-height: 1.6; white-space: pre-wrap; }}
            .synthesis-meta {{ display: flex; gap: 20px; margin-top: 12px; font-size: 12px; color: var(--text-muted); flex-wrap: wrap; }}
            .confidence-badge {{ background: rgba(245, 158, 11, 0.15); color: var(--warning); padding: 2px 10px; border-radius: 99px; font-weight: 600; }}
            .finding-chip {{ display: inline-block; background: rgba(255,255,255,0.06); padding: 2px 8px; border-radius: 4px; margin: 2px 4px 2px 0; font-size: 11px; }}
            .error-badge {{ background: rgba(239, 68, 68, 0.15); color: var(--danger); padding: 2px 10px; border-radius: 99px; font-weight: 600; font-size: 11px; }}
            .retry-badge {{ background: rgba(16, 185, 129, 0.15); color: var(--success); padding: 2px 10px; border-radius: 99px; font-weight: 600; font-size: 11px; }}
            .health-bar {{ width: 100%; height: 4px; background: rgba(255,255,255,0.05); border-radius: 2px; margin-top: 8px; overflow: hidden; }}
            .health-fill {{ height: 100%; border-radius: 2px; transition: width 0.5s ease; }}
            .health-fill.good {{ background: var(--success); }}
            .health-fill.warn {{ background: var(--warning); }}
            .health-fill.bad {{ background: var(--danger); }}

            .cursor-overlay {{ 
                position: absolute; width: 24px; height: 24px; 
                background: radial-gradient(circle, rgba(59, 130, 246, 0.8) 0%, rgba(59, 130, 246, 0.2) 60%, transparent 100%);
                border: 2px solid var(--primary); border-radius: 50%; 
                pointer-events: none; transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1); 
                display: none; transform: translate(-50%, -50%); z-index: 10;
            }}

            .steering-panel {{
                background: var(--bg-color);
                border-top: 1px solid var(--panel-border);
                padding: 20px 40px;
                display: flex;
                gap: 16px;
                align-items: center;
                width: 100%;
                box-sizing: border-box;
            }}

            .input-group {{ display: flex; flex-direction: column; gap: 4px; }}
            .input-group label {{ font-size: 11px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }}
            .input-group input {{ background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.1); color: white; padding: 8px 12px; border-radius: 8px; font-size: 13px; outline: none; transition: all 0.2s; font-family: 'Inter', sans-serif; }}
            .input-group input:focus {{ border-color: var(--primary); }}
            
            .btn-start {{ 
                background: linear-gradient(135deg, var(--accent) 0%, var(--primary) 100%); 
                color: white; border: none; padding: 10px 24px; border-radius: 8px; 
                font-weight: 600; font-size: 14px; cursor: pointer; transition: all 0.2s; 
                display: flex; align-items: center; justify-content: center; height: 40px; margin-top: auto;
                box-shadow: 0 4px 15px rgba(139, 92, 246, 0.3);
            }}
            .btn-start:hover {{ transform: translateY(-1px); box-shadow: 0 6px 20px rgba(139, 92, 246, 0.4); }}
            .btn-start:disabled {{ opacity: 0.5; cursor: not-allowed; transform: none; box-shadow: none; }}

            .loader {{ border: 2px solid rgba(255,255,255,0.1); border-top-color: white; border-radius: 50%; width: 14px; height: 14px; animation: spin 1s linear infinite; display: none; margin-left: 8px; }}
            @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
            
            .global-status-dock {{
                position: fixed;
                top: 56px;
                right: 24px;
                background: var(--panel-bg);
                backdrop-filter: blur(16px);
                border: 1px solid var(--panel-border);
                padding: 8px 16px;
                border-radius: 99px;
                font-size: 12px;
                font-weight: 600;
                letter-spacing: 0.05em;
                color: var(--text-muted);
                z-index: 50;
                display: flex;
                align-items: center;
                gap: 8px;
                transition: color 0.3s;
            }}
            .global-status-dock.active {{ color: var(--accent); border-color: rgba(139, 92, 246, 0.3); }}
            .global-status-dock.active .status-dot {{ background: var(--accent); animation: pulse 1.5s infinite; box-shadow: 0 0 10px var(--accent); }}
        </style>
    </head>
    <body>
        {NAV_HTML}
        
        <div class="global-status-dock" id="global-status">
            <div class="status-dot"></div>
            <span id="global-status-text">ORCHESTRATOR IDLE</span>
        </div>

        <div class="intent-panel" id="intent-panel">
            <div class="intent-title">🎯 Intent Analysis</div>
            <div class="intent-goal" id="intent-goal">-</div>
            <div class="intent-meta" id="intent-meta"></div>
        </div>

        <div class="main-layout">
            <div class="grid-container" id="grid-container"></div>
            
            <div class="synthesis-panel" id="synthesis-panel">
                <div class="synthesis-title">✨ Synthesized Result</div>
                <div class="synthesis-report" id="synthesis-report"></div>
                <div class="synthesis-meta" id="synthesis-meta"></div>
            </div>
        </div>
        
        <div class="steering-panel">
            <div class="input-group" style="flex: 2;">
                <label>Objective</label>
                <input type="text" id="target-task" value="Research and compare the top 3 AI coding assistants: Cursor, GitHub Copilot, and Claude Code. Include pricing, features, and ideal use cases." placeholder="Describe a complex task to decompose...">
            </div>
            <div class="input-group" style="width: 100px;">
                <label>Agents</label>
                <input type="number" id="num-agents" value="6" min="2" max="12">
            </div>
            <button id="btn-start" class="btn-start" onclick="startOrchestratedSwarm()">
                <span id="btn-text">Orchestrate Swarm</span>
                <div id="btn-loader" class="loader"></div>
            </button>
        </div>
        
        <script>
            const gridContainer = document.getElementById("grid-container");
            const intentPanel = document.getElementById("intent-panel");
            const intentGoal = document.getElementById("intent-goal");
            const intentMeta = document.getElementById("intent-meta");
            const synthesisPanel = document.getElementById("synthesis-panel");
            const synthesisReport = document.getElementById("synthesis-report");
            const synthesisMeta = document.getElementById("synthesis-meta");
            const btnStart = document.getElementById("btn-start");
            const btnText = document.getElementById("btn-text");
            const btnLoader = document.getElementById("btn-loader");
            const globalStatus = document.getElementById("global-status");
            const globalStatusText = document.getElementById("global-status-text");
            
            let agentTasks = [];
            let evtSource = null;
            
            function clearGrid() {{
                gridContainer.innerHTML = "";
                intentPanel.classList.remove("visible");
                synthesisPanel.classList.remove("visible");
            }}
            
            function buildGrid(numAgents) {{
                clearGrid();
                const cols = numAgents <= 2 ? 2 : numAgents <= 4 ? 2 : numAgents <= 6 ? 3 : numAgents <= 9 ? 3 : 4;
                gridContainer.style.gridTemplateColumns = `repeat(${{cols}}, 1fr)`;
                
                for (let i = 0; i < numAgents; i++) {{
                    const task = agentTasks[i] || {{ role_name: `Agent ${{i+1}}`, task_description: "" }};
                    const el = document.createElement("div");
                    el.className = "agent-view status-idle";
                    el.id = `agent-${{i}}`;
                    el.innerHTML = `
                        <img id="screenshot-${{i}}" class="screenshot-img" src="" alt=""/>
                        <div id="cursor-${{i}}" class="cursor-overlay"></div>
                        <div class="agent-overlay">
                            <div>
                                <div class="agent-role">${{task.role_name}}</div>
                                <div class="agent-id">
                                    <div class="status-dot"></div>AGENT 0${{i+1}}
                                </div>
                                <div class="task-preview" id="task-preview-${{i}}">${{task.task_description || ''}}</div>
                                <div class="action-text" id="action-text-${{i}}">-</div>
                                <div class="result-text" id="result-text-${{i}}"></div>
                            </div>
                            <div class="step-counter">STEP <span id="step-count-${{i}}">0</span>/40</div>
                        </div>
                    `;
                    gridContainer.appendChild(el);
                    
                    document.getElementById(`screenshot-${{i}}`).onload = function() {{
                        this.classList.add('loaded');
                    }};
                }}
            }}
            
            function showIntent(intent) {{
                intentGoal.innerText = intent.true_goal || "-";
                let metaHtml = "";
                if (intent.success_criteria && intent.success_criteria.length > 0) {{
                    metaHtml += `<span class="intent-chip">Success: ${{intent.success_criteria[0]}}</span>`;
                }}
                if (intent.key_entities && intent.key_entities.length > 0) {{
                    metaHtml += `<span class="intent-chip">Entities: ${{intent.key_entities.slice(0, 3).join(", ")}}</span>`;
                }}
                metaHtml += `<span class="intent-chip">Agents: ${{intent.suggested_num_agents || 6}}</span>`;
                intentMeta.innerHTML = metaHtml;
                intentPanel.classList.add("visible");
            }}
            
            function showSynthesis(data) {{
                if (!data.synthesis) return;
                const syn = data.synthesis;
                synthesisReport.innerText = syn.final_report || "No synthesis available.";

                let metaHtml = "";
                if (syn.confidence_score !== undefined) {{
                    metaHtml += `<span class="confidence-badge">Confidence: ${{(syn.confidence_score * 100).toFixed(0)}}%</span>`;
                }}
                if (syn.health_ratio !== undefined) {{
                    const healthPct = Math.round(syn.health_ratio * 100);
                    const healthClass = healthPct >= 70 ? "good" : healthPct >= 40 ? "warn" : "bad";
                    metaHtml += `<div style="width:120px;"><div class="health-bar"><div class="health-fill ${{healthClass}}" style="width:${{healthPct}}%"></div></div><div style="font-size:10px;text-align:center;margin-top:2px;">${{healthPct}}% healthy</div></div>`;
                }}
                if (syn.failed_agents > 0) {{
                    metaHtml += `<span class="error-badge">${{syn.failed_agents}} failed</span>`;
                }}
                if (syn.retry_info && syn.retry_info.retry_recovered > 0) {{
                    metaHtml += `<span class="retry-badge">${{syn.retry_info.retry_recovered}} recovered</span>`;
                }}
                if (syn.key_findings && syn.key_findings.length > 0) {{
                    syn.key_findings.forEach(f => {{
                        metaHtml += `<span class="finding-chip">${{f}}</span>`;
                    }});
                }}
                synthesisMeta.innerHTML = metaHtml;
                synthesisPanel.classList.add("visible");
            }}
            
            async function startOrchestratedSwarm() {{
                const task = document.getElementById("target-task").value.trim();
                const numAgents = parseInt(document.getElementById("num-agents").value) || 6;
                if (!task) return alert("Task is required.");
                
                btnStart.disabled = true;
                btnLoader.style.display = "block";
                btnText.innerText = "Analyzing Intent...";
                globalStatus.className = "global-status-dock active";
                globalStatusText.innerText = "DECOMPOSING TASK";
                
                try {{
                    // Step 1: Get decomposition from orchestrator
                    const decomposeRes = await fetch("/swarm/orchestrate", {{
                        method: "POST",
                        headers: {{ "Content-Type": "application/json" }},
                        body: JSON.stringify({{ task, num_agents: numAgents }})
                    }});
                    const plan = await decomposeRes.json();
                    
                    if (plan.status !== "ok") {{
                        throw new Error(plan.message || "Decomposition failed");
                    }}
                    
                    // Show intent
                    if (plan.intent) {{
                        showIntent(plan.intent);
                    }}
                    
                    // Build grid with roles
                    agentTasks = plan.agent_tasks || [];
                    buildGrid(numAgents);
                    
                    // Update UI
                    btnText.innerText = "Running Agents...";
                    globalStatusText.innerText = "SWARM ACTIVE";
                    
                    // Start SSE
                    if (evtSource) evtSource.close();
                    evtSource = new EventSource("/swarm/stream");
                    evtSource.onmessage = function(event) {{
                        const msg = JSON.parse(event.data);
                        if (msg.type === 'full_state') {{
                            for (const [id, agent] of Object.entries(msg.data)) {{
                                updateAgent(id, agent);
                            }}
                        }} else if (msg.type === 'update') {{
                            updateAgent(msg.agent_id, msg.data);
                            // Check for synthesis in the main state
                            if (msg.data.synthesis) {{
                                showSynthesis(msg.data);
                            }}
                        }}
                    }};
                    
                    // Step 2: Launch the swarm
                    await fetch("/swarm/run", {{
                        method: "POST",
                        headers: {{ "Content-Type": "application/json" }},
                        body: JSON.stringify({{ task, num_agents: numAgents }})
                    }});
                    
                }} catch(e) {{
                    console.error(e);
                    btnText.innerText = "Error: " + e.message;
                    setTimeout(resetUI, 3000);
                }}
            }}
            
            function resetUI() {{
                btnStart.disabled = false;
                btnLoader.style.display = "none";
                btnText.innerText = "Orchestrate Swarm";
                globalStatus.className = "global-status-dock";
                globalStatusText.innerText = "ORCHESTRATOR IDLE";
            }}
            
            function updateAgent(agentId, data) {{
                const i = parseInt(agentId.split('_')[1]);
                if (isNaN(i)) return;
                
                const container = document.getElementById(`agent-${{i}}`);
                if (!container) return;
                
                const stepCount = document.getElementById(`step-count-${{i}}`);
                const screenshot = document.getElementById(`screenshot-${{i}}`);
                const cursor = document.getElementById(`cursor-${{i}}`);
                const actionText = document.getElementById(`action-text-${{i}}`);
                const resultText = document.getElementById(`result-text-${{i}}`);
                
                if (data.status) {{
                    container.className = `agent-view status-${{data.status}}`;
                    if (data.status === 'success' || data.status === 'failed') {{
                        if (data.result) {{
                            const isError = data.status === 'failed' && data.result.includes('error');
                            resultText.innerText = data.result.substring(0, 100) + (data.result.length > 100 ? "..." : "");
                            resultText.style.color = data.status === 'success' ? 'var(--success)' : 'var(--danger)';
                            if (isError) {{
                                const retryBadge = document.createElement('span');
                                retryBadge.className = 'retry-badge';
                                retryBadge.innerText = 'RETRYING';
                                retryBadge.style.marginLeft = '6px';
                                resultText.appendChild(retryBadge);
                            }}
                        }}
                        const allAgents = document.querySelectorAll('.agent-view');
                        const allDone = Array.from(allAgents).every(a => 
                            a.classList.contains('status-success') || a.classList.contains('status-failed')
                        );
                        if (allDone && agentTasks.length > 0) {{
                            globalStatusText.innerText = "SYNTHESIZING";
                        }}
                    }} else {{
                        resultText.innerText = "";
                    }}
                }}
                
                if (data.role_name && agentTasks[i]) {{
                    agentTasks[i].role_name = data.role_name;
                    const roleEl = container.querySelector('.agent-role');
                    if (roleEl) roleEl.innerText = data.role_name;
                }}
                
                if (data.screenshot_url && screenshot && data.screenshot_url !== screenshot.src) {{
                    screenshot.classList.remove('loaded');
                    screenshot.src = data.screenshot_url;
                }}
                
                if (data.step !== undefined && stepCount) stepCount.innerText = data.step;
                
                if (data.action && Object.keys(data.action).length > 0) {{
                    if (actionText) actionText.innerText = data.action.type || '-';
                    
                    if (data.action.x !== undefined && data.action.y !== undefined && cursor) {{
                        cursor.style.display = 'block';
                        const updateCursor = () => {{
                            if (!screenshot || !screenshot.complete) return;
                            const rect = screenshot.getBoundingClientRect();
                            if (rect.width === 0) return;
                            const scale = Math.min(rect.width / 1280, rect.height / 720);
                            const ax = (rect.width - (1280 * scale)) / 2;
                            const ay = (rect.height - (720 * scale)) / 2;
                            cursor.style.left = (ax + data.action.x * scale) + 'px';
                            cursor.style.top = (ay + data.action.y * scale) + 'px';
                        }};
                        if (screenshot.complete) updateCursor();
                        else screenshot.addEventListener('load', updateCursor, {{ once: true }});
                    }} else if (cursor) {{
                        cursor.style.display = 'none';
                    }}
                }}
            }}
            
            // Also listen to main state for synthesis
            const mainEvtSource = new EventSource("/stream");
            mainEvtSource.onmessage = function(event) {{
                const data = JSON.parse(event.data);
                if (data.synthesis) {{
                    showSynthesis(data);
                    resetUI();
                    globalStatusText.innerText = "COMPLETE";
                }}
                if (data.result && data.status === 'success') {{
                    // Extract synthesis from result if present
                    if (typeof data.result === 'string' && data.result.includes('Orchestrated Swarm Complete')) {{
                        globalStatusText.innerText = "COMPLETE";
                        resetUI();
                    }}
                }}
            }};
        </script>
    </body>
    </html>"""

# Task 19: KERNEL Live-View IFrame Grid
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/browsers", response_class=HTMLResponse)
async def browsers_page():
    return f"""<!DOCTYPE html><html><head>
    <title>AEGIS - KERNEL Browser Grid</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
    {SHARED_CSS}
    .page-header {{ width:100%; padding:16px 40px; box-sizing:border-box; display:flex; justify-content:space-between; align-items:center; }}
    .page-header h1 {{ font-size:22px; margin:0; font-weight:700; letter-spacing:-0.02em; background:linear-gradient(135deg,#fff 0%,#cbd5e1 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    .page-header .count {{ font-size:16px; color:var(--primary); font-weight:600; }}
    .browser-grid {{ display:grid; gap:8px; padding:8px 40px 40px; height:calc(100vh - 120px); }}
    .tile {{ border-radius:12px; overflow:hidden; border:1px solid var(--panel-border); background:rgba(30,41,59,0.5); display:flex; flex-direction:column; }}
    .tile-header {{ padding:6px 12px; background:rgba(15,23,42,0.8); display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--panel-border); }}
    .tile-header .idx {{ font-size:13px; font-weight:700; color:var(--primary); }}
    .tile-header .mp {{ font-size:11px; color:var(--text-muted); text-transform:uppercase; letter-spacing:0.05em; }}
    .tile iframe {{ flex:1; width:100%; border:none; background:#000; }}
    .empty-state {{ display:flex; align-items:center; justify-content:center; height:calc(100vh - 120px); }}
    .empty-state p {{ font-size:18px; color:var(--text-muted); text-align:center; line-height:1.6; }}
    </style></head><body>
    {NAV_HTML}
    <div class="page-header">
        <h1>KERNEL Browser Grid</h1>
        <div class="count" id="count">0 active</div>
    </div>
    <div id="grid-container"></div>
    <script>
    let lastJson = "";
    function getCols(n) {{
        if (n <= 2) return 1;
        if (n <= 4) return 2;
        if (n <= 9) return 3;
        if (n <= 16) return 4;
        return 6;
    }}
    async function refresh() {{
        try {{
            const res = await fetch("/api/browsers");
            const browsers = await res.json();
            const j = JSON.stringify(browsers);
            if (j === lastJson) return;
            lastJson = j;
            const container = document.getElementById("grid-container");
            document.getElementById("count").textContent = browsers.length + " active";
            if (browsers.length === 0) {{
                container.innerHTML = '<div class="empty-state"><p>No KERNEL browser sessions active.<br>Sessions register automatically when agents start.</p></div>';
                container.className = "";
                return;
            }}
            container.className = "browser-grid";
            const cols = getCols(browsers.length);
            container.style.gridTemplateColumns = "repeat(" + cols + ", 1fr)";
            container.innerHTML = browsers.map((b, i) =>
                '<div class="tile"><div class="tile-header"><span class="idx">#' + (i+1) + '</span><span class="mp">' +
                (b.marketplace || "browser") + '</span></div><iframe src="' +
                b.live_view_url + (b.live_view_url.includes("?") ? "&" : "?") + 'readOnly=true" loading="lazy"></iframe></div>'
            ).join("");
        }} catch(e) {{}}
    }}
    refresh();
    setInterval(refresh, 5000);
    </script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Task 18: Verdict Feed
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/verdicts", response_class=HTMLResponse)
async def verdicts_page():
    return f"""<!DOCTYPE html><html><head>
    <title>AEGIS - Verdict Feed</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
    {SHARED_CSS}
    .page-header {{ width:100%; padding:16px 40px; box-sizing:border-box; display:flex; justify-content:space-between; align-items:center; }}
    .page-header h1 {{ font-size:22px; margin:0; font-weight:700; letter-spacing:-0.02em; background:linear-gradient(135deg,#fff 0%,#cbd5e1 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    .stats {{ display:flex; gap:16px; }}
    .stat {{ font-size:14px; font-weight:600; padding:4px 12px; border-radius:99px; }}
    .stat-pass {{ background:rgba(16,185,129,0.1); color:#34d399; border:1px solid rgba(16,185,129,0.2); }}
    .stat-fail {{ background:rgba(239,68,68,0.1); color:#f87171; border:1px solid rgba(239,68,68,0.2); }}
    .stat-warn {{ background:rgba(245,158,11,0.1); color:#fbbf24; border:1px solid rgba(245,158,11,0.2); }}
    .feed {{ display:flex; flex-direction:column; gap:8px; padding:8px 40px 40px; max-height:calc(100vh - 120px); overflow-y:auto; }}
    .verdict-card {{ display:flex; align-items:flex-start; gap:16px; padding:14px 20px; border-radius:12px; background:var(--panel-bg); border:1px solid var(--panel-border); border-left:4px solid transparent; animation:slideIn 0.3s ease; }}
    .verdict-card.r-PASSED {{ border-left-color:var(--success); }}
    .verdict-card.r-BLOCKED,.verdict-card.r-REJECTED {{ border-left-color:var(--danger); }}
    .verdict-card.r-WARNING {{ border-left-color:var(--warning); }}
    .v-badges {{ display:flex; gap:8px; align-items:center; min-width:200px; }}
    .v-type {{ font-size:11px; font-weight:700; padding:3px 10px; border-radius:6px; text-transform:uppercase; letter-spacing:0.05em; }}
    .t-VERIFIER {{ background:rgba(139,92,246,0.15); color:#a78bfa; }}
    .t-SECURITY {{ background:rgba(59,130,246,0.15); color:#60a5fa; }}
    .t-MARKETPLACE {{ background:rgba(245,158,11,0.15); color:#fbbf24; }}
    .t-SCANNER {{ background:rgba(16,185,129,0.15); color:#34d399; }}
    .v-result {{ font-size:12px; font-weight:700; padding:3px 10px; border-radius:6px; }}
    .v-result.r-PASSED {{ background:rgba(16,185,129,0.15); color:#34d399; }}
    .v-result.r-BLOCKED,.v-result.r-REJECTED {{ background:rgba(239,68,68,0.15); color:#f87171; }}
    .v-result.r-WARNING {{ background:rgba(245,158,11,0.15); color:#fbbf24; }}
    .v-reason {{ flex:1; font-size:14px; color:var(--text-main); line-height:1.4; }}
    .v-ts {{ font-size:12px; color:var(--text-muted); min-width:70px; text-align:right; font-family:monospace; }}
    @keyframes slideIn {{ from {{ opacity:0; transform:translateY(-8px); }} to {{ opacity:1; transform:translateY(0); }} }}
    .empty-state {{ display:flex; align-items:center; justify-content:center; height:calc(100vh - 120px); }}
    .empty-state p {{ font-size:18px; color:var(--text-muted); text-align:center; line-height:1.6; }}
    </style></head><body>
    {NAV_HTML}
    <div class="page-header">
        <h1>AEGIS Verdict Feed</h1>
        <div class="stats">
            <span class="stat stat-pass" id="s-pass">0 passed</span>
            <span class="stat stat-fail" id="s-fail">0 blocked</span>
            <span class="stat stat-warn" id="s-warn">0 warnings</span>
        </div>
    </div>
    <div class="feed" id="feed"></div>
    <script>
    let counts = {{pass:0, fail:0, warn:0}};
    function updateStats() {{
        document.getElementById("s-pass").textContent = counts.pass + " passed";
        document.getElementById("s-fail").textContent = counts.fail + " blocked";
        document.getElementById("s-warn").textContent = counts.warn + " warnings";
    }}
    function renderCard(v) {{
        const card = document.createElement("div");
        card.className = "verdict-card r-" + v.result;
        card.innerHTML =
            '<div class="v-badges"><span class="v-type t-' + v.type + '">' + v.type + '</span>' +
            '<span class="v-result r-' + v.result + '">' + v.result + '</span></div>' +
            '<div class="v-reason">' + v.reason + '</div>' +
            '<div class="v-ts">' + (v.ts || "") + '</div>';
        if (v.result === "PASSED") counts.pass++;
        else if (v.result === "WARNING") counts.warn++;
        else counts.fail++;
        updateStats();
        return card;
    }}
    async function loadExisting() {{
        try {{
            const res = await fetch("/api/verdicts");
            const verdicts = await res.json();
            const feed = document.getElementById("feed");
            if (verdicts.length === 0) {{
                feed.innerHTML = '<div class="empty-state"><p>No verdicts yet.<br>Verdicts appear as AEGIS processes listings.</p></div>';
                return;
            }}
            verdicts.forEach(v => feed.appendChild(renderCard(v)));
        }} catch(e) {{}}
    }}
    loadExisting();
    const sse = new EventSource("/api/verdicts/stream");
    sse.onmessage = function(event) {{
        const data = JSON.parse(event.data);
        if (Array.isArray(data)) return;
        const feed = document.getElementById("feed");
        const empty = feed.querySelector(".empty-state");
        if (empty) empty.remove();
        feed.prepend(renderCard(data));
    }};
    </script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Task 17: Ranked Bargain Board
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/bargains", response_class=HTMLResponse)
async def bargains_page():
    return f"""<!DOCTYPE html><html><head>
    <title>AEGIS - Bargain Radar Board</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
    {SHARED_CSS}
    .page-header {{ width:100%; padding:16px 40px; box-sizing:border-box; display:flex; justify-content:space-between; align-items:center; }}
    .page-header h1 {{ font-size:22px; margin:0; font-weight:700; letter-spacing:-0.02em; background:linear-gradient(135deg,#fff 0%,#cbd5e1 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    .sort-controls {{ display:flex; gap:8px; }}
    .sort-btn {{ background:rgba(15,23,42,0.6); border:1px solid var(--panel-border); color:var(--text-muted); padding:6px 14px; border-radius:8px; font-size:12px; font-weight:600; cursor:pointer; transition:all 0.2s; font-family:'Inter',sans-serif; text-transform:uppercase; letter-spacing:0.05em; }}
    .sort-btn.active {{ border-color:var(--primary); color:var(--primary); background:rgba(59,130,246,0.1); }}
    .bargain-grid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:16px; padding:8px 40px 40px; }}
    .listing-card {{ border-radius:14px; background:var(--panel-bg); border:1px solid var(--panel-border); border-left:5px solid var(--text-muted); padding:20px; transition:transform 0.2s, box-shadow 0.2s; }}
    .listing-card:hover {{ transform:translateY(-2px); box-shadow:0 12px 30px rgba(0,0,0,0.3); }}
    .listing-card.accepted {{ border-left-color:var(--success); }}
    .listing-card.rejected {{ border-left-color:var(--danger); }}
    .listing-card.replica {{ border-left-color:var(--warning); }}
    .listing-card.scam {{ border-left-color:var(--danger); }}
    .card-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:12px; }}
    .card-title {{ font-size:16px; font-weight:700; color:var(--text-main); line-height:1.3; flex:1; }}
    .card-price {{ font-size:22px; font-weight:800; color:var(--success); white-space:nowrap; }}
    .rejected .card-price {{ color:var(--danger); }}
    .mp-badge {{ display:inline-flex; align-items:center; gap:4px; font-size:11px; font-weight:700; padding:3px 10px; border-radius:6px; text-transform:uppercase; letter-spacing:0.05em; }}
    .mp-craigslist {{ background:rgba(128,0,255,0.15); color:#c084fc; }}
    .mp-fb_marketplace {{ background:rgba(59,130,246,0.15); color:#60a5fa; }}
    .mp-offerup {{ background:rgba(16,185,129,0.15); color:#34d399; }}
    .mp-mercari {{ background:rgba(239,68,68,0.15); color:#f87171; }}
    .mp-ebay {{ background:rgba(245,158,11,0.15); color:#fbbf24; }}
    .mp-reverb {{ background:rgba(139,92,246,0.15); color:#a78bfa; }}
    .card-meta {{ display:flex; gap:16px; align-items:center; margin:10px 0; font-size:13px; color:var(--text-muted); }}
    .card-meta span {{ display:flex; align-items:center; gap:4px; }}
    .score-row {{ display:flex; align-items:center; gap:12px; margin:12px 0 8px; }}
    .score-bar {{ flex:1; height:8px; border-radius:4px; background:rgba(255,255,255,0.06); overflow:hidden; }}
    .score-fill {{ height:100%; border-radius:4px; transition:width 0.5s ease; }}
    .score-num {{ font-size:16px; font-weight:800; min-width:48px; text-align:right; }}
    .status-pill {{ display:inline-flex; align-items:center; gap:4px; font-size:11px; font-weight:700; padding:3px 10px; border-radius:6px; text-transform:uppercase; }}
    .pill-accepted {{ background:rgba(16,185,129,0.15); color:#34d399; }}
    .pill-rejected {{ background:rgba(239,68,68,0.15); color:#f87171; }}
    .pill-replica {{ background:rgba(245,158,11,0.15); color:#fbbf24; }}
    .pill-scam {{ background:rgba(239,68,68,0.2); color:#f87171; }}
    .reasons {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }}
    .reason-chip {{ font-size:11px; padding:3px 8px; border-radius:6px; background:rgba(255,255,255,0.05); color:var(--text-muted); border:1px solid rgba(255,255,255,0.06); }}
    </style></head><body>
    {NAV_HTML}
    <div class="page-header">
        <h1>Bargain Radar - Ranked Listings</h1>
        <div class="sort-controls">
            <button class="sort-btn active" onclick="sortBy('score',this)">Score</button>
            <button class="sort-btn" onclick="sortBy('price',this)">Price</button>
            <button class="sort-btn" onclick="sortBy('distance',this)">Distance</button>
        </div>
    </div>
    <div class="bargain-grid" id="grid"></div>
    <script>
    let listings = [];
    const mpLabels = {{craigslist:"CL",fb_marketplace:"FB",offerup:"OU",mercari:"MC",ebay:"EB",reverb:"RV"}};
    function scoreColor(s) {{
        const pct = Math.max(0, Math.min(100, (s + 20) / 1.2));
        if (pct > 60) return "var(--success)";
        if (pct > 30) return "var(--warning)";
        return "var(--danger)";
    }}
    function renderGrid(items) {{
        const grid = document.getElementById("grid");
        grid.innerHTML = items.map(item => {{
            const l = item.listing;
            const mp = l.marketplace || "unknown";
            const cls = item.is_scam_suspected ? "scam" : item.is_replica_suspected ? "replica" : item.accepted ? "accepted" : "rejected";
            const pct = Math.max(0, Math.min(100, (item.score + 20) / 1.2));
            const dist = l.distance_mi != null ? l.distance_mi.toFixed(1) + " mi" : "ships";
            const photos = l.photo_count != null ? l.photo_count + " photos" : "";
            const age = l.posted_age_text || "";
            let pills = "";
            if (item.is_scam_suspected) pills += '<span class="status-pill pill-scam">SCAM</span>';
            if (item.is_replica_suspected) pills += '<span class="status-pill pill-replica">REPLICA</span>';
            if (item.accepted) pills += '<span class="status-pill pill-accepted">ACCEPTED</span>';
            else if (!item.is_scam_suspected && !item.is_replica_suspected) pills += '<span class="status-pill pill-rejected">REJECTED</span>';
            return '<div class="listing-card ' + cls + '">' +
                '<div class="card-top"><div><div class="card-title">' + (l.title||"Untitled") + '</div>' +
                '<div style="margin-top:6px;display:flex;gap:6px;align-items:center;">' +
                '<span class="mp-badge mp-' + mp + '">' + (mpLabels[mp]||mp) + '</span>' + pills + '</div></div>' +
                '<div class="card-price">$' + (l.price||0).toLocaleString() + '</div></div>' +
                '<div class="card-meta"><span>' + dist + '</span><span>' + photos + '</span><span>' + age + '</span></div>' +
                '<div class="score-row"><div class="score-bar"><div class="score-fill" style="width:' + pct + '%;background:' + scoreColor(item.score) + ';"></div></div>' +
                '<div class="score-num" style="color:' + scoreColor(item.score) + ';">' + item.score.toFixed(1) + '</div></div>' +
                '<div class="reasons">' + (item.reasons||[]).map(r => '<span class="reason-chip">' + r + '</span>').join("") + '</div></div>';
        }}).join("");
    }}
    function sortBy(key, btn) {{
        document.querySelectorAll(".sort-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        const sorted = [...listings];
        if (key === "score") sorted.sort((a,b) => b.score - a.score);
        else if (key === "price") sorted.sort((a,b) => (a.listing.price||0) - (b.listing.price||0));
        else if (key === "distance") sorted.sort((a,b) => (a.listing.distance_mi||999) - (b.listing.distance_mi||999));
        renderGrid(sorted);
    }}
    async function load() {{
        try {{
            const res = await fetch("/api/bargains");
            listings = await res.json();
            renderGrid(listings);
        }} catch(e) {{}}
    }}
    load();
    setInterval(load, 10000);
    </script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Task 16: Split-Screen Comparison
# ══════════════════════════════════════════════════════════════════════════════
@app.get("/split", response_class=HTMLResponse)
async def split_page():
    return f"""<!DOCTYPE html><html><head>
    <title>AEGIS - Split Comparison</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
    {SHARED_CSS}
    body {{ display:flex; flex-direction:column; }}
    .page-header {{ width:100%; padding:12px 40px; box-sizing:border-box; display:flex; justify-content:space-between; align-items:center; }}
    .page-header h1 {{ font-size:22px; margin:0; font-weight:700; letter-spacing:-0.02em; background:linear-gradient(135deg,#fff 0%,#cbd5e1 100%); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
    .headline {{ display:flex; gap:24px; justify-content:center; align-items:center; padding:8px 40px 4px; }}
    .hl-card {{ padding:12px 32px; border-radius:14px; text-align:center; }}
    .hl-card.danger {{ background:rgba(239,68,68,0.08); border:1px solid rgba(239,68,68,0.25); }}
    .hl-card.success {{ background:rgba(16,185,129,0.08); border:1px solid rgba(16,185,129,0.25); }}
    .hl-label {{ font-size:13px; font-weight:600; text-transform:uppercase; letter-spacing:0.06em; margin-bottom:4px; }}
    .hl-card.danger .hl-label {{ color:#f87171; }}
    .hl-card.success .hl-label {{ color:#34d399; }}
    .hl-num {{ font-size:52px; font-weight:800; line-height:1; }}
    .hl-card.danger .hl-num {{ color:#ef4444; }}
    .hl-card.success .hl-num {{ color:#10b981; }}
    .hl-vs {{ font-size:24px; font-weight:800; color:var(--text-muted); }}
    .split-container {{ display:flex; gap:8px; flex:1; padding:8px 40px; box-sizing:border-box; min-height:0; }}
    .split-panel {{ flex:1; display:flex; flex-direction:column; border-radius:16px; overflow:hidden; border:1px solid var(--panel-border); background:var(--panel-bg); }}
    .panel-header {{ padding:10px 20px; display:flex; justify-content:space-between; align-items:center; }}
    .panel-header.raw {{ background:rgba(239,68,68,0.08); border-bottom:2px solid var(--danger); }}
    .panel-header.aegis {{ background:rgba(16,185,129,0.08); border-bottom:2px solid var(--success); }}
    .panel-title {{ font-size:15px; font-weight:700; }}
    .panel-header.raw .panel-title {{ color:#f87171; }}
    .panel-header.aegis .panel-title {{ color:#34d399; }}
    .panel-status {{ display:flex; align-items:center; gap:12px; }}
    .panel-status .step {{ font-size:14px; font-weight:700; color:var(--text-muted); }}
    .panel-status .badge {{ font-size:11px; font-weight:700; padding:3px 10px; border-radius:6px; text-transform:uppercase; }}
    .badge-idle {{ background:rgba(148,163,184,0.1); color:var(--text-muted); }}
    .badge-running {{ background:rgba(59,130,246,0.1); color:#60a5fa; }}
    .badge-success {{ background:rgba(16,185,129,0.1); color:#34d399; }}
    .badge-failed {{ background:rgba(239,68,68,0.1); color:#f87171; }}
    .panel-screen {{ flex:1; background:#000; display:flex; align-items:center; justify-content:center; position:relative; overflow:hidden; min-height:0; }}
    .panel-screen img {{ max-width:100%; max-height:100%; object-fit:contain; }}
    .panel-result {{ padding:10px 16px; font-size:13px; max-height:60px; overflow-y:auto; background:rgba(15,23,42,0.6); border-top:1px solid var(--panel-border); color:var(--text-muted); display:none; }}
    .controls {{ padding:10px 40px 16px; display:flex; gap:12px; align-items:center; }}
    .controls input {{ flex:1; background:rgba(15,23,42,0.6); border:1px solid var(--panel-border); color:white; padding:10px 16px; border-radius:10px; font-size:14px; outline:none; font-family:'Inter',sans-serif; }}
    .controls input:focus {{ border-color:var(--primary); box-shadow:0 0 0 3px rgba(59,130,246,0.15); }}
    .controls button {{ background:linear-gradient(135deg,var(--primary) 0%,var(--accent) 100%); color:white; border:none; padding:10px 24px; border-radius:10px; font-weight:700; font-size:14px; cursor:pointer; white-space:nowrap; box-shadow:0 4px 15px rgba(59,130,246,0.3); }}
    .controls button:disabled {{ opacity:0.5; cursor:not-allowed; background:#334155; box-shadow:none; }}
    </style></head><body>
    {NAV_HTML}
    <div class="page-header">
        <h1>AEGIS Split Comparison</h1>
    </div>
    <div class="headline">
        <div class="hl-card danger">
            <div class="hl-label">Without AEGIS</div>
            <div class="hl-num">16%</div>
        </div>
        <div class="hl-vs">vs</div>
        <div class="hl-card success">
            <div class="hl-label">With AEGIS</div>
            <div class="hl-num">92%</div>
        </div>
    </div>
    <div class="split-container">
        <div class="split-panel">
            <div class="panel-header raw">
                <div class="panel-title">RAW CUA (No Safety)</div>
                <div class="panel-status">
                    <span class="step" id="step-raw">Step 0/40</span>
                    <span class="badge badge-idle" id="badge-raw">IDLE</span>
                </div>
            </div>
            <div class="panel-screen"><img id="img-raw" src="" alt=""/></div>
            <div class="panel-result" id="result-raw"></div>
        </div>
        <div class="split-panel">
            <div class="panel-header aegis">
                <div class="panel-title">AEGIS-Wrapped CUA</div>
                <div class="panel-status">
                    <span class="step" id="step-aegis">Step 0/40</span>
                    <span class="badge badge-idle" id="badge-aegis">IDLE</span>
                </div>
            </div>
            <div class="panel-screen"><img id="img-aegis" src="" alt=""/></div>
            <div class="panel-result" id="result-aegis"></div>
        </div>
    </div>
    <div class="controls">
        <input type="text" id="split-url" placeholder="Target URL (optional)"/>
        <input type="text" id="split-task" placeholder="Search query, e.g. mid-century desk under $500 within 10 miles no replicas" style="flex:2;"/>
        <button id="btn-split" onclick="startSplit()">Run Comparison</button>
    </div>
    <script>
    const sseRaw = new EventSource("/split/stream/raw");
    const sseAegis = new EventSource("/split/stream/aegis");
    function updatePanel(side, data) {{
        const img = document.getElementById("img-" + side);
        const step = document.getElementById("step-" + side);
        const badge = document.getElementById("badge-" + side);
        const result = document.getElementById("result-" + side);
        if (data.screenshot_url && data.screenshot_url !== img.src) img.src = data.screenshot_url;
        if (data.step !== undefined) step.textContent = "Step " + data.step + "/40";
        if (data.status) {{
            badge.textContent = data.status.toUpperCase();
            badge.className = "badge badge-" + data.status;
        }}
        if (data.result) {{
            result.textContent = data.result;
            result.style.display = "block";
        }}
    }}
    sseRaw.onmessage = e => updatePanel("raw", JSON.parse(e.data));
    sseAegis.onmessage = e => updatePanel("aegis", JSON.parse(e.data));
    async function startSplit() {{
        const url = document.getElementById("split-url").value.trim();
        const task = document.getElementById("split-task").value.trim();
        if (!task) return alert("Task is required.");
        const btn = document.getElementById("btn-split");
        btn.disabled = true;
        btn.textContent = "Running...";
        document.getElementById("result-raw").style.display = "none";
        document.getElementById("result-aegis").style.display = "none";
        const body = url ? {{url, task}} : {{task}};
        try {{
            await fetch("/split/start", {{method:"POST", headers:{{"Content-Type":"application/json"}}, body:JSON.stringify(body)}});
        }} catch(e) {{ alert("Failed: " + e); }}
        setTimeout(() => {{ btn.disabled = false; btn.textContent = "Run Comparison"; }}, 5000);
    }}
    </script></body></html>"""


def main():
    uvicorn.run("cua_loop.ui_server:app", host="0.0.0.0", port=8555, reload=False)

if __name__ == "__main__":
    main()
