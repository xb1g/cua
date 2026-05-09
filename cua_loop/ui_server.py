import os
import asyncio
import json
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

state = {
    "screenshot_url": "",
    "action": {},
    "step": 0,
    "task": "",
}

clients = set()

@app.post("/update")
async def update_state(data: dict):
    state["screenshot_url"] = data.get("screenshot_url", state["screenshot_url"])
    state["action"] = data.get("action", state["action"])
    state["step"] = data.get("step", state["step"])
    state["task"] = data.get("task", state["task"])
    
    # Notify clients
    for q in clients:
        await q.put(data)
    return {"status": "ok"}

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
                data = await q.get()
                yield f"data: {json.dumps(data)}\n\n"
        finally:
            clients.remove(q)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/", response_class=HTMLResponse)
async def index():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>CUA Live Viewer</title>
        <style>
            body { font-family: 'Inter', system-ui, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center; }
            h1 { font-size: 24px; margin-bottom: 5px; font-weight: 600; letter-spacing: -0.02em; }
            .subtitle { color: #94a3b8; margin-bottom: 25px; font-size: 14px; }
            #container { display: flex; gap: 24px; width: 100%; max-width: 1600px; height: calc(100vh - 120px); }
            #browser-view { flex: 1; background: #1e293b; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1); position: relative; border: 1px solid #334155; display: flex; flex-direction: column; }
            .browser-header { background: #0f172a; padding: 10px 15px; border-bottom: 1px solid #334155; display: flex; gap: 8px; align-items: center; }
            .dot { width: 12px; height: 12px; border-radius: 50%; }
            .dot-red { background: #ef4444; }
            .dot-yellow { background: #f59e0b; }
            .dot-green { background: #10b981; }
            .browser-content { position: relative; flex: 1; display: flex; align-items: center; justify-content: center; background: #000; overflow: hidden; }
            #screenshot { max-width: 100%; max-height: 100%; object-fit: contain; }
            
            #cursor { 
                position: absolute; 
                width: 30px; height: 30px; 
                background: rgba(59, 130, 246, 0.4); 
                border: 2px solid #3b82f6; 
                border-radius: 50%; 
                pointer-events: none; 
                transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1); 
                display: none; 
                transform: translate(-50%, -50%); 
                z-index: 10;
                box-shadow: 0 0 15px rgba(59, 130, 246, 0.5);
            }
            .cursor-click {
                animation: click-pulse 0.5s ease-out;
            }
            @keyframes click-pulse {
                0% { transform: translate(-50%, -50%) scale(1); opacity: 1; }
                50% { transform: translate(-50%, -50%) scale(0.5); opacity: 0.8; }
                100% { transform: translate(-50%, -50%) scale(1.5); opacity: 0; }
            }
            
            #sidebar { width: 380px; display: flex; flex-direction: column; gap: 20px; overflow-y: auto; }
            .card { background: #1e293b; padding: 20px; border-radius: 12px; border: 1px solid #334155; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1); }
            .card-title { font-size: 14px; font-weight: 600; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 15px; }
            
            .badge { display: inline-block; padding: 6px 12px; background: rgba(59, 130, 246, 0.1); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.2); border-radius: 6px; font-size: 14px; font-weight: 600; margin-bottom: 15px; }
            
            .data-row { margin-bottom: 10px; }
            .data-label { font-size: 12px; color: #94a3b8; margin-bottom: 4px; }
            .data-value { font-size: 14px; color: #f8fafc; word-break: break-all; }
            
            pre { margin: 0; white-space: pre-wrap; font-size: 13px; color: #a5b4fc; background: #0f172a; padding: 12px; border-radius: 8px; border: 1px solid #334155; font-family: 'JetBrains Mono', monospace; }
        </style>
    </head>
    <body>
        <h1>Live CUA Viewer</h1>
        <div class="subtitle" id="task-desc">Waiting for task...</div>
        
        <div id="container">
            <div id="browser-view">
                <div class="browser-header">
                    <div class="dot dot-red"></div>
                    <div class="dot dot-yellow"></div>
                    <div class="dot dot-green"></div>
                </div>
                <div class="browser-content">
                    <img id="screenshot" src="" alt="Waiting for screenshot..."/>
                    <div id="cursor"></div>
                </div>
            </div>
            <div id="sidebar">
                <div class="card">
                    <div class="card-title">Current State</div>
                    <div class="badge">Step <span id="step-count">0</span></div>
                    
                    <div class="data-row">
                        <div class="data-label">Action Type</div>
                        <div class="data-value" id="action-type">-</div>
                    </div>
                </div>
                
                <div class="card">
                    <div class="card-title">Action Payload</div>
                    <pre id="action-details">Waiting for actions...</pre>
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
            const taskDesc = document.getElementById("task-desc");
            
            // Assume the virtual display is 1280x720 
            const DISPLAY_WIDTH = 1280;
            const DISPLAY_HEIGHT = 720;
            
            evtSource.onmessage = function(event) {
                const data = JSON.parse(event.data);
                
                if (data.task) taskDesc.innerText = data.task;
                if (data.screenshot_url) screenshot.src = data.screenshot_url;
                if (data.step !== undefined) stepCount.innerText = data.step;
                
                if (data.action && Object.keys(data.action).length > 0) {
                    actionType.innerText = data.action.type || 'unknown';
                    actionDetails.innerText = JSON.stringify(data.action, null, 2);
                    
                    // Show click cursor
                    if (data.action.x !== undefined && data.action.y !== undefined) {
                        cursor.style.display = 'block';
                        
                        // Wait for image to load to get accurate dimensions
                        const updateCursor = () => {
                            const imgRect = screenshot.getBoundingClientRect();
                            
                            // Calculate scaling (contain mode)
                            const scaleX = imgRect.width / DISPLAY_WIDTH;
                            const scaleY = imgRect.height / DISPLAY_HEIGHT;
                            const scale = Math.min(scaleX, scaleY);
                            
                            const actualWidth = DISPLAY_WIDTH * scale;
                            const actualHeight = DISPLAY_HEIGHT * scale;
                            
                            const offsetX = (imgRect.width - actualWidth) / 2;
                            const offsetY = (imgRect.height - actualHeight) / 2;
                            
                            const cursorX = offsetX + (data.action.x * scale);
                            const cursorY = offsetY + (data.action.y * scale);
                            
                            // Set relative to the browser-content container
                            const containerRect = screenshot.parentElement.getBoundingClientRect();
                            const finalX = (imgRect.left - containerRect.left) + cursorX;
                            const finalY = (imgRect.top - containerRect.top) + cursorY;
                            
                            cursor.style.left = finalX + 'px';
                            cursor.style.top = finalY + 'px';
                            
                            if (data.action.type === 'click') {
                                cursor.classList.remove('cursor-click');
                                void cursor.offsetWidth; // trigger reflow
                                cursor.classList.add('cursor-click');
                            }
                        };
                        
                        if (screenshot.complete) {
                            updateCursor();
                        } else {
                            screenshot.onload = updateCursor;
                        }
                        
                        // Handle window resize
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
    uvicorn.run("cua_loop.ui_server:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":
    main()
