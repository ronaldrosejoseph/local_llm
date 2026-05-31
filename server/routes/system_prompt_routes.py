"""
System prompt template routes — search, save, update, delete reusable prompts.
"""

from fastapi import APIRouter, HTTPException
from contextlib import closing
from typing import Optional
from server.db import get_db_connection

router = APIRouter()


@router.get("/api/system-prompts")
def list_system_prompts(q: Optional[str] = None):
    """List saved system prompt templates, optionally filtered by search query."""
    with closing(get_db_connection()) as conn:
        if q and q.strip():
            search = f"%{q.strip()}%"
            rows = conn.execute(
                "SELECT id, name, content, created_at, updated_at "
                "FROM system_prompt_templates "
                "WHERE name LIKE ? OR content LIKE ? "
                "ORDER BY updated_at DESC LIMIT 20",
                (search, search),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, name, content, created_at, updated_at "
                "FROM system_prompt_templates "
                "ORDER BY updated_at DESC LIMIT 20"
            ).fetchall()

    return [
        {
            "id": r["id"],
            "name": r["name"],
            "content": r["content"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


@router.post("/api/system-prompts")
def create_system_prompt(data: dict):
    """Save a new system prompt template."""
    name = (data.get("name") or "").strip()
    content = (data.get("content") or "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Name is required")
    if not content:
        raise HTTPException(status_code=400, detail="Content is required")

    with closing(get_db_connection()) as conn:
        cursor = conn.execute(
            "INSERT INTO system_prompt_templates (name, content) VALUES (?, ?)",
            (name, content),
        )
        conn.commit()
        row_id = cursor.lastrowid

    return {"id": row_id, "name": name, "status": "created"}


@router.put("/api/system-prompts/{template_id}")
def update_system_prompt(template_id: int, data: dict):
    """Update an existing system prompt template."""
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT id FROM system_prompt_templates WHERE id = ?", (template_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found")

        name = data.get("name")
        content = data.get("content")

        if name is not None:
            conn.execute(
                "UPDATE system_prompt_templates SET name = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (name.strip(), template_id),
            )
        if content is not None:
            conn.execute(
                "UPDATE system_prompt_templates SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (content.strip(), template_id),
            )
        conn.commit()

    return {"id": template_id, "status": "updated"}


@router.delete("/api/system-prompts/{template_id}")
def delete_system_prompt(template_id: int):
    """Delete a system prompt template."""
    with closing(get_db_connection()) as conn:
        row = conn.execute(
            "SELECT id FROM system_prompt_templates WHERE id = ?", (template_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Template not found")

        conn.execute("DELETE FROM system_prompt_templates WHERE id = ?", (template_id,))
        conn.commit()

    return {"id": template_id, "status": "deleted"}
