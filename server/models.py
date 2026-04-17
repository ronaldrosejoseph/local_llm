"""
Pydantic request/response models for the API.

NOTE: This file is named "models.py" for Pydantic data models,
not to be confused with ML models (which live in services/llm.py).
"""

from pydantic import BaseModel
from typing import Optional


class Message(BaseModel):
    role: str
    content: str


class ChatCreate(BaseModel):
    message: str
    system_prompt: Optional[str] = None


class ChatResponse(BaseModel):
    chat_id: str
    response: str


class SayRequest(BaseModel):
    text: str


class ConfigUpdate(BaseModel):
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    repetition_penalty: Optional[float] = None
    pdf_text_pages_per_batch: Optional[int] = None
    pdf_image_pages_per_batch: Optional[int] = None
    image_generation_resolution: Optional[str] = None


class ModelAdd(BaseModel):
    name: str


class SystemPromptUpdate(BaseModel):
    system_prompt: str
