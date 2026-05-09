import os
import asyncio
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import traceback
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv(override=True)

shutdown_event = asyncio.Event()

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    shutdown_event.set()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

NUM_AGENTS = 9

state = {
    f"agent_{i}": {
        "agent_id": f"agent_{i}",
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
    for i in range(NUM_AGENTS)
}

clients = set()

async def broadcast(data):
    for q in clients:
        await q.put(data)

@app.post("/update")
async def update_state(data: dict):
    agent_id = data.get("agent_id", "agent_0")
    if agent_id in state:
        state[agent_id].update({k: v for k, v in data.items() if v is not None and k != "agent_id"})
        await broadcast({"type": "update", "agent_id": agent_id, "data": state[agent_id]})
    return {"status": "ok"}

class StartRequest(BaseModel):
    task: str
    url: str | None = None

def run_agent_sync(url: str | None, task: str, agent_id: str):
    import httpx
    from cua_loop.runner import run_with_retry
    try:
        max_attempts = int(os.getenv("CUA_MAX_ATTEMPTS", "5"))
        clean_url = url.strip() if url else None
        result = run_with_retry(task=task, url=clean_url or None, max_attempts=max_attempts, agent_id=agent_id)

        last = result.attempts[-1] if result.attempts else None
        rows = last.verifier.rows_extracted if last else 0
        reason = last.verifier.reason if last else "no attempts ran"
        attempts_used = len(result.attempts)

        if result.success:
            payload = {
                "agent_id": agent_id,
                "status": "success",
                "result": (
                    f"Success on attempt {attempts_used}/{max_attempts} — "
                    f"extracted {rows} rows in {result.total_duration_s:.1f}s. "
                    f"Reason: {reason}"
                ),
            }
        else:
            payload = {
                "agent_id": agent_id,
                "status": "failed",
                "result": (
                    f"Failed after {attempts_used}/{max_attempts} attempts "
                    f"({result.total_duration_s:.1f}s). Last reason: {reason}"
                ),
            }
        httpx.post("http://localhost:8555/update", json=payload, timeout=5.0)
    except Exception as e:
        httpx.post(
            "http://localhost:8555/update",
            json={"agent_id": agent_id, "status": "failed", "result": f"{e}\n{traceback.format_exc()}"},
            timeout=5.0,
        )

@app.post("/start")
async def start_agent(req: StartRequest):
    for i in range(NUM_AGENTS):
        agent_id = f"agent_{i}"
        state[agent_id]["status"] = "running"
        state[agent_id]["task"] = req.task
        state[agent_id]["screenshot_url"] = ""
        state[agent_id]["action"] = {}
        state[agent_id]["step"] = 0
        state[agent_id]["result"] = ""
    await broadcast({"type": "full_state", "data": state})

    loop = asyncio.get_event_loop()
    import concurrent.futures
    # Running 9 concurrent agents
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=NUM_AGENTS)
    for i in range(NUM_AGENTS):
        loop.run_in_executor(executor, run_agent_sync, req.url, req.task, f"agent_{i}")
    return {"status": "started"}

@app.get("/stream")
async def stream(request: Request):
    async def event_generator():
        q = asyncio.Queue()
        clients.add(q)
        try:
            yield f"data: {json.dumps({'type': 'full_state', 'data': state})}\n\n"
            loops = 0
            while not shutdown_event.is_set():
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    loops += 1
                    if loops >= 15:
                        yield ": keepalive\n\n"
                        loops = 0
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
        <title>Symphony CUA Swarm</title>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
        <style>
            :root {
                --bg-color: #0f172a;
                --panel-bg: rgba(15, 23, 42, 0.85);
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
            
            body { margin: 0; padding: 0; background: var(--bg-color); color: var(--text-main); font-family: 'Inter', system-ui, sans-serif; overflow-x: hidden; min-height: 100vh; width: 100vw; }
            
            .grid-container { 
                display: grid; 
                grid-template-columns: repeat(3, 1fr); 
                grid-template-rows: repeat(3, 1fr); 
                width: 100vw; 
                height: 100vh; 
                gap: 1px; 
                background: var(--panel-border);
            }
            
            .agent-view { 
                position: relative; 
                background: #000; 
                overflow: hidden; 
                display: flex;
                align-items: center;
                justify-content: center;
            }
            
            .screenshot-img { 
                max-width: 100%; 
                max-height: 100%; 
                object-fit: contain; 
                opacity: 0; 
                transition: opacity 0.3s ease; 
                position: absolute; 
                top: 0; left: 0; width: 100%; height: 100%;
            }
            .screenshot-img.loaded { opacity: 1; }
            
            /* Agent Overlay Data */
            .agent-overlay {
                position: absolute;
                bottom: 0; left: 0; right: 0;
                padding: 12px 16px;
                background: linear-gradient(to top, rgba(0,0,0,0.9) 0%, transparent 100%);
                display: flex;
                justify-content: space-between;
                align-items: flex-end;
                pointer-events: none;
                z-index: 5;
            }
            
            .agent-id { font-size: 11px; color: var(--text-muted); font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; margin-bottom: 4px; }
            .action-text { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--accent); }
            .result-text { font-family: 'JetBrains Mono', monospace; font-size: 11px; margin-top: 4px; word-break: break-all; }
            
            .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
            .status-idle .status-dot { background: var(--text-muted); }
            .status-running .status-dot { background: var(--primary); animation: pulse 1.5s infinite; box-shadow: 0 0 10px var(--primary); }
            .status-success .status-dot { background: var(--success); box-shadow: 0 0 10px var(--success); }
            .status-failed .status-dot { background: var(--danger); box-shadow: 0 0 10px var(--danger); }
            
            @keyframes pulse {
                0% { transform: scale(0.95); opacity: 0.5; }
                50% { transform: scale(1.2); opacity: 1; }
                100% { transform: scale(0.95); opacity: 0.5; }
            }

            .step-counter { font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted); }
            .step-counter span { color: var(--text-main); font-weight: 600; }

            /* Cursor */
            .cursor-overlay { 
                position: absolute; width: 24px; height: 24px; 
                background: radial-gradient(circle, rgba(59, 130, 246, 0.8) 0%, rgba(59, 130, 246, 0.2) 60%, transparent 100%);
                border: 2px solid var(--primary); border-radius: 50%; 
                pointer-events: none; transition: all 0.3s cubic-bezier(0.34, 1.56, 0.64, 1); 
                display: none; transform: translate(-50%, -50%); z-index: 10;
            }

            /* Steering Prompt (Control Panel) */
            .steering-panel {
                background: var(--bg-color);
                border-top: 1px solid var(--panel-border);
                padding: 32px 40px;
                display: flex;
                flex-direction: column;
                gap: 16px;
                width: 100%;
                box-sizing: border-box;
                min-height: 160px;
            }

            .steering-inputs {
                display: flex;
                gap: 24px;
                align-items: center;
                width: 100%;
            }

            .suggestions {
                display: flex;
                gap: 8px;
                flex-wrap: wrap;
            }
            .suggestion-pill {
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid var(--panel-border);
                padding: 6px 12px;
                border-radius: 99px;
                font-size: 11px;
                color: var(--text-muted);
                cursor: pointer;
                transition: all 0.2s;
                white-space: nowrap;
            }
            .suggestion-pill:hover {
                background: rgba(255, 255, 255, 0.1);
                border-color: var(--primary);
                color: var(--text-main);
            }

            .input-group { display: flex; flex-direction: column; gap: 6px; }
            .input-group label { font-size: 11px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; }
            .input-group input { background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.1); color: white; padding: 10px 14px; border-radius: 8px; font-size: 13px; outline: none; transition: all 0.2s; font-family: 'Inter', sans-serif; }
            .input-group input:focus { border-color: var(--primary); }
            
            .btn-start { 
                background: var(--primary); color: white; border: none; padding: 12px 24px; border-radius: 8px; font-weight: 600; font-size: 14px; cursor: pointer; transition: background 0.2s; display: flex; align-items: center; justify-content: center; height: 44px; margin-top: auto;
            }
            .btn-start:hover { background: var(--primary-hover); }
            .btn-start:disabled { opacity: 0.5; cursor: not-allowed; }

            .loader { border: 2px solid rgba(255,255,255,0.1); border-top-color: white; border-radius: 50%; width: 14px; height: 14px; animation: spin 1s linear infinite; display: none; margin-left: 8px; }
            @keyframes spin { to { transform: rotate(360deg); } }
            
            .global-status-dock {
                position: fixed;
                top: 24px;
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
            }
            .global-status-dock.active { color: var(--primary); border-color: rgba(59, 130, 246, 0.3); }
            .global-status-dock.active .status-dot { background: var(--primary); animation: pulse 1.5s infinite; box-shadow: 0 0 10px var(--primary); }
        </style>
    </head>
    <body>
        <div class="global-status-dock" id="global-status">
            <div class="status-dot"></div>
            <span id="global-status-text">SWARM IDLE</span>
        </div>

        <div class="grid-container" id="grid-container"></div>
        
        <div class="steering-panel">
            <div class="steering-inputs">
                <div class="input-group" style="flex: 1; max-width: 400px;">
                    <label>Target URL</label>
                    <input type="text" id="target-url" placeholder="https://…">
                </div>
                <div class="input-group" style="flex: 2;">
                    <label>Objective</label>
                    <input type="text" id="target-task" value="extract top 10 stories with title, url, points as a table">
                </div>
                <button id="btn-start" class="btn-start" onclick="startSwarm()">
                    <span id="btn-text">Launch Swarm</span>
                    <div id="btn-loader" class="loader"></div>
                </button>
            </div>
            <div class="suggestions">
                <div class="suggestion-pill" onclick="setTask('https://www.amazon.com', 'find the cheapest ergonomic office chair under $200 and add to cart')">Amazon: Office Chair</div>
                <div class="suggestion-pill" onclick="setTask('https://www.ebay.com', 'find a mechanical keyboard with cherry mx brown switches and add to watchlist')">eBay: Keyboard</div>
                <div class="suggestion-pill" onclick="setTask('https://www.bestbuy.com', 'find the latest M3 MacBook Pro and check local pickup availability')">Best Buy: MacBook</div>
                <div class="suggestion-pill" onclick="setTask('https://www.nike.com', 'find white air force 1 size 10 and add to cart')">Nike: Air Force 1</div>
            </div>
        </div>
        
        <script>
            function setTask(url, task) {
                document.getElementById('target-url').value = url;
                document.getElementById('target-task').value = task;
            }

            const NUM_AGENTS = 9;
            const gridContainer = document.getElementById("grid-container");
            const DISPLAY_WIDTH = 1280;
            const DISPLAY_HEIGHT = 720;
            
            for (let i = 0; i < NUM_AGENTS; i++) {
                const el = document.createElement("div");
                el.className = "agent-view status-idle";
                el.id = `agent-${i}`;
                el.innerHTML = `
                    <img id="screenshot-${i}" class="screenshot-img" src="" alt=""/>
                    <div id="cursor-${i}" class="cursor-overlay"></div>
                    <div class="agent-overlay">
                        <div>
                            <div class="agent-id">
                                <div class="status-dot"></div>AGENT 0${i+1}
                            </div>
                            <div class="action-text" id="action-text-${i}">-</div>
                            <div class="result-text" id="result-text-${i}"></div>
                        </div>
                        <div class="step-counter">STEP <span id="step-count-${i}">0</span>/40</div>
                    </div>
                `;
                gridContainer.appendChild(el);
                
                document.getElementById(`screenshot-${i}`).onload = function() {
                    this.classList.add('loaded');
                };
            }

            const evtSource = new EventSource("/stream");
            const btnStart = document.getElementById("btn-start");
            const btnText = document.getElementById("btn-text");
            const btnLoader = document.getElementById("btn-loader");
            const globalStatus = document.getElementById("global-status");
            const globalStatusText = document.getElementById("global-status-text");
            
            async function startSwarm() {
                const url = document.getElementById("target-url").value.trim();
                const task = document.getElementById("target-task").value.trim();
                if (!task) return;

                btnStart.disabled = true;
                btnLoader.style.display = "block";
                btnText.innerText = "Deploying";
                globalStatus.className = "global-status-dock active";
                globalStatusText.innerText = "SWARM ACTIVE";

                try {
                    await fetch("/start", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify(url ? { url, task } : { task })
                    });
                } catch(e) {
                    console.error(e);
                    resetUI();
                }
            }
            
            function resetUI() {
                btnStart.disabled = false;
                btnLoader.style.display = "none";
                btnText.innerText = "Launch Swarm";
                globalStatus.className = "global-status-dock";
                globalStatusText.innerText = "SWARM IDLE";
            }
            
            function updateAgent(agentId, data) {
                const i = parseInt(agentId.split('_')[1]);
                if (isNaN(i)) return;
                
                const container = document.getElementById(`agent-${i}`);
                const stepCount = document.getElementById(`step-count-${i}`);
                const screenshot = document.getElementById(`screenshot-${i}`);
                const cursor = document.getElementById(`cursor-${i}`);
                const actionText = document.getElementById(`action-text-${i}`);
                const resultText = document.getElementById(`result-text-${i}`);
                
                if (data.status) {
                    container.className = `agent-view status-${data.status}`;
                    if (data.status === 'success' || data.status === 'failed') {
                        if (data.result) {
                            resultText.innerText = data.result.substring(0, 80) + (data.result.length > 80 ? "..." : "");
                            resultText.style.color = data.status === 'success' ? 'var(--success)' : 'var(--danger)';
                        }
                    } else {
                        resultText.innerText = "";
                    }
                }
                
                if (data.screenshot_url && data.screenshot_url !== screenshot.src) {
                    screenshot.classList.remove('loaded');
                    screenshot.src = data.screenshot_url;
                }
                
                if (data.step !== undefined) stepCount.innerText = data.step;
                
                if (data.action && Object.keys(data.action).length > 0) {
                    actionText.innerText = data.action.type || '-';
                    
                    if (data.action.x !== undefined && data.action.y !== undefined) {
                        cursor.style.display = 'block';
                        const updateCursor = () => {
                            if (!screenshot.complete) return;
                            const rect = screenshot.getBoundingClientRect();
                            if (rect.width === 0) return;
                            const scale = Math.min(rect.width / DISPLAY_WIDTH, rect.height / DISPLAY_HEIGHT);
                            const ax = (rect.width - (DISPLAY_WIDTH * scale)) / 2;
                            const ay = (rect.height - (DISPLAY_HEIGHT * scale)) / 2;
                            
                            cursor.style.left = (ax + data.action.x * scale) + 'px';
                            cursor.style.top = (ay + data.action.y * scale) + 'px';
                        };
                        if (screenshot.complete) updateCursor();
                        else screenshot.addEventListener('load', updateCursor, { once: true });
                    } else {
                        cursor.style.display = 'none';
                    }
                }
            }
            
            evtSource.onmessage = function(event) {
                const msg = JSON.parse(event.data);
                if (msg.type === 'full_state') {
                    let anyActive = false;
                    for (const [id, agent] of Object.entries(msg.data)) {
                        updateAgent(id, agent);
                        if (agent.status === 'running') anyActive = true;
                    }
                    if (anyActive) {
                        globalStatus.className = "global-status-dock active";
                        globalStatusText.innerText = "SWARM ACTIVE";
                    } else {
                        resetUI();
                    }
                } else if (msg.type === 'update') {
                    updateAgent(msg.agent_id, msg.data);
                }
            };
        </script>
    </body>
    </html>
    """

def main():
    uvicorn.run("cua_loop.ui_server:app", host="0.0.0.0", port=8555, reload=True)

if __name__ == "__main__":
    main()
