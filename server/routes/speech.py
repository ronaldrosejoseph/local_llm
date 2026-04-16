"""
Speech routes — text-to-speech via macOS `say` command.
"""

import subprocess

from fastapi import APIRouter, HTTPException

from server import state
from server.models import SayRequest

router = APIRouter()


@router.post("/api/say")
async def say_endpoint(data: SayRequest):
    text = data.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")

    # Prevent injection of command options
    if text.startswith("-"):
        raise HTTPException(status_code=400, detail="Invalid text content for speech.")

    try:
        # Stop any currently running say processes
        for p in list(state.say_processes):
            if p.poll() is None:
                try:
                    p.terminate()
                except Exception:
                    pass
            state.say_processes.remove(p)

        # Spawn new speech process
        proc = subprocess.Popen(["say", text])
        state.say_processes.add(proc)

        # Cleanup finished processes to avoid memory leak
        for p in list(state.say_processes):
            if p.poll() is not None:
                state.say_processes.remove(p)

        return {"status": "ok"}

    except Exception as e:
        print(f"Error in say_endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/stop-say")
def stop_say_endpoint():
    try:
        terminated = 0
        for p in list(state.say_processes):
            if p.poll() is None:
                p.terminate()
                terminated += 1
            state.say_processes.remove(p)

        return {"status": "ok", "terminated": terminated}
    except Exception as e:
        print(f"Error in stop_say_endpoint: {e}")
        raise HTTPException(status_code=500, detail=str(e))
