"""
HuggingFace token management routes — verify, save, delete, status.
"""

from fastapi import APIRouter, HTTPException

from server.services.hf_auth import (
    verify_token,
    save_hf_token,
    load_hf_token,
    delete_hf_token,
    has_token,
)

router = APIRouter()


@router.get("/api/hf-token/status")
def token_status():
    """Check if a token is stored in the keyring."""
    return {"stored": has_token()}


@router.post("/api/hf-token/verify")
def verify(data: dict):
    """Verify a token without saving it."""
    token = data.get("token", "")
    if not token or not token.strip():
        raise HTTPException(status_code=400, detail="Token is empty")
    valid, msg = verify_token(token)
    return {"valid": valid, "message": msg}


@router.post("/api/hf-token/save")
def save(data: dict):
    """Verify and save a token to the keyring."""
    token = data.get("token", "")
    if not token or not token.strip():
        raise HTTPException(status_code=400, detail="Token is empty")
    success, msg = save_hf_token(token)
    if not success:
        raise HTTPException(status_code=400, detail=msg)
    return {"status": "saved", "message": msg}


@router.delete("/api/hf-token")
def delete():
    """Delete the stored token."""
    ok = delete_hf_token()
    return {"status": "deleted" if ok else "error"}
