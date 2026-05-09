import os
import asyncio
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import traceback

from dotenv import load_dotenv
load_dotenv(override=True)

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

state = {
    "screenshot_url": "",
    "action": {},
    "step": 0,
    "task": "",
    "status": "idle", # idle, running, success, failed
    "result": "",
    "verification_passed": None,
    "verification_reason": "",
    "blocked": False,
    "block_reason": "",
}

clients = set()

async def broadcast(data):
    for q in clients:
        await q.put(data)

@app.post("/update")
async def update_state(data: dict):
    state.update({k: v for k, v in data.items() if v is not None})
    await broadcast(state)
    return {"status": "ok"}

class StartRequest(BaseModel):
    task: str
    url: str | None = None

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
        httpx.post("http://localhost:8555/update", json=payload, timeout=5.0)
    except Exception as e:
        httpx.post(
            "http://localhost:8555/update",
            json={"status": "failed", "result": f"{e}\n{traceback.format_exc()}"},
            timeout=5.0,
        )

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
    import concurrent.futures
    loop.run_in_executor(None, run_agent_sync, req.url, req.task)
    return {"status": "started"}

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
                btnStart.querySelector('span').innerText = "Starting...";
                resultBox.style.display = "none";

                const body = url ? { url, task } : { task };
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
def main():
    uvicorn.run("cua_loop.ui_server:app", host="0.0.0.0", port=8555, reload=False)

if __name__ == "__main__":
    main()
