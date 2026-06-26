"""
Enterprise Knowledge Assistant - Chat API Router

Provides REST and WebSocket endpoints for the chat interface:
- POST /conversations - create conversation
- GET /conversations - list conversations
- GET /conversations/{id} - get conversation with messages
- DELETE /conversations/{id} - delete conversation
- POST /conversations/{id}/messages - send message (REST streaming)
- WS /conversations/{id}/stream - WebSocket streaming
"""

from __future__ import annotations

import json
import time
import uuid
from typing import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user, CurrentUser
from app.core.config.settings import get_ollama_settings
from app.domain.models.all_models import Conversation, Message, MessageRole, QueryAnalytics
from app.infrastructure.database.session import get_db
from app.services.retrieval.pipeline import RAGPipeline
from app.infrastructure.embedding.service import EmbeddingService

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Request/Response Schemas
# ---------------------------------------------------------------------------

class CreateConversationRequest(BaseModel):
    title: str = Field(default="New Conversation", max_length=500)


class ConversationResponse(BaseModel):
    id: str
    title: str
    is_pinned: bool
    created_at: str
    message_count: int = 0


class MessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    model: str | None = None
    use_agents: bool = False  # Use multi-agent pipeline vs direct RAG


class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    citations: list[dict] = []
    suggested_questions: list[str] = []
    latency_ms: int | None = None
    created_at: str


class UpdateConversationRequest(BaseModel):
    title: str | None = Field(None, max_length=500)
    is_pinned: bool | None = None


# ---------------------------------------------------------------------------
# Conversation CRUD
# ---------------------------------------------------------------------------

@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: CreateConversationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new conversation."""
    conversation = Conversation(
        id=str(uuid.uuid4()),
        organization_id=current_user.organization_id,
        user_id=current_user.id,
        title=request.title,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)

    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        is_pinned=conversation.is_pinned,
        created_at=conversation.created_at.isoformat(),
    )


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    skip: int = 0,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List conversations for the current user."""
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.user_id == current_user.id,
            Conversation.organization_id == current_user.organization_id,
            Conversation.is_deleted == False,
        )
        .order_by(Conversation.is_pinned.desc(), Conversation.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    conversations = result.scalars().all()

    return [
        ConversationResponse(
            id=c.id,
            title=c.title,
            is_pinned=c.is_pinned,
            created_at=c.created_at.isoformat(),
        )
        for c in conversations
    ]


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a conversation with all its messages."""
    result = await db.execute(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
            Conversation.is_deleted == False,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    messages = messages_result.scalars().all()

    return {
        "id": conversation.id,
        "title": conversation.title,
        "is_pinned": conversation.is_pinned,
        "created_at": conversation.created_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "citations": m.citations or [],
                "created_at": m.created_at.isoformat(),
                "latency_ms": m.latency_ms,
            }
            for m in messages
        ],
    }


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    request: UpdateConversationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update conversation title or pin status."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if request.title is not None:
        conversation.title = request.title
    if request.is_pinned is not None:
        conversation.is_pinned = request.is_pinned

    await db.commit()
    return {"id": conversation.id, "title": conversation.title, "is_pinned": conversation.is_pinned}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete a conversation."""
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
        )
    )
    conversation = result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    conversation.is_deleted = True
    await db.commit()


# ---------------------------------------------------------------------------
# Message Sending (REST Streaming via Server-Sent Events)
# ---------------------------------------------------------------------------

def get_rag_pipeline(db: AsyncSession) -> RAGPipeline:
    """Dependency: create RAG pipeline with dependencies."""
    from app.infrastructure.llm.ollama_client import OllamaClient
    ollama_settings = get_ollama_settings()
    llm = OllamaClient(
        base_url=ollama_settings.base_url,
        model=ollama_settings.default_llm_model,
    )
    embedding_service = EmbeddingService(ollama_settings.base_url)
    return RAGPipeline(db=db, embedding_service=embedding_service, llm_client=llm)


@router.post("/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request: MessageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Send a message and get a streaming response via Server-Sent Events.

    The response is streamed as SSE with event types:
    - 'token': individual text tokens
    - 'citations': JSON array of source citations (final event)
    - 'done': stream complete
    - 'error': error occurred
    """
    # Verify conversation ownership
    conv_result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == current_user.id,
            Conversation.is_deleted == False,
        )
    )
    conversation = conv_result.scalar_one_or_none()
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Load conversation history
    history_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(20)  # Last 20 messages for context
    )
    history_messages = history_result.scalars().all()
    history = [{"role": m.role, "content": m.content} for m in history_messages]

    # Save user message
    user_message = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role=MessageRole.USER,
        content=request.content,
    )
    db.add(user_message)
    await db.commit()

    # Stream response
    async def generate_sse() -> AsyncGenerator[bytes, None]:
        start_ms = int(time.time() * 1000)
        full_response = []
        citations = []

        try:
            pipeline = get_rag_pipeline(db)
            stream = await pipeline.answer(
                query=request.content,
                organization_id=current_user.organization_id,
                user_role=current_user.role,
                conversation_history=history,
                stream=True,
            )

            async for token in stream:
                # Check for citations sentinel
                if "__CITATIONS__" in token:
                    parts = token.split("__CITATIONS__")
                    if parts[0]:
                        full_response.append(parts[0])
                        yield f"data: {json.dumps({'type': 'token', 'content': parts[0]})}\n\n".encode()

                    citation_part = parts[1].split("__END_CITATIONS__")[0]
                    citations = json.loads(citation_part)
                    yield f"data: {json.dumps({'type': 'citations', 'citations': citations})}\n\n".encode()
                else:
                    full_response.append(token)
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n".encode()

            # Save assistant message
            latency_ms = int(time.time() * 1000) - start_ms
            full_text = "".join(full_response)

            assistant_message = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                content=full_text,
                citations=citations,
                latency_ms=latency_ms,
                model_used=request.model or get_ollama_settings().default_llm_model,
            )
            db.add(assistant_message)

            # Auto-generate conversation title from first message
            if len(history) == 0 and conversation.title == "New Conversation":
                short_title = request.content[:60] + ("..." if len(request.content) > 60 else "")
                conversation.title = short_title

            await db.commit()

            # Save analytics
            analytics = QueryAnalytics(
                id=str(uuid.uuid4()),
                organization_id=current_user.organization_id,
                user_id=current_user.id,
                conversation_id=conversation_id,
                query=request.content,
                documents_retrieved=[c.get("document_id") for c in citations if c.get("document_id")],
                chunks_retrieved=len(citations),
                model_used=assistant_message.model_used,
                latency_ms=latency_ms,
            )
            db.add(analytics)
            await db.commit()

            yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_message.id})}\n\n".encode()

        except Exception as e:
            logger.error("Streaming error", error=str(e), exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': 'An error occurred'})}\n\n".encode()

    return StreamingResponse(
        generate_sse(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
        },
    )


# ---------------------------------------------------------------------------
# WebSocket Streaming
# ---------------------------------------------------------------------------

@router.websocket("/{conversation_id}/stream")
async def websocket_stream(
    conversation_id: str,
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    """
    WebSocket endpoint for real-time streaming chat.

    Client sends: {"content": "user message", "token": "jwt_token"}
    Server sends:
      {"type": "token", "content": "..."}
      {"type": "citations", "citations": [...]}
      {"type": "done"}
      {"type": "error", "message": "..."}
    """
    await websocket.accept()

    try:
        # Authenticate via initial message (JWT in first message)
        auth_data = await websocket.receive_json()
        token = auth_data.get("token", "")

        from app.core.security.auth import decode_access_token
        from jose import JWTError
        try:
            claims = decode_access_token(token)
            user_id = claims["sub"]
            org_id = claims["org"]
            role = claims["role"]
        except JWTError:
            await websocket.send_json({"type": "error", "message": "Invalid authentication"})
            await websocket.close(code=4001)
            return

        # Verify conversation ownership
        conv_result = await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
                Conversation.is_deleted == False,
            )
        )
        if not conv_result.scalar_one_or_none():
            await websocket.send_json({"type": "error", "message": "Conversation not found"})
            await websocket.close(code=4004)
            return

        # Main message loop
        pipeline = get_rag_pipeline(db)

        while True:
            try:
                message_data = await websocket.receive_json()
                query = message_data.get("content", "").strip()

                if not query:
                    continue

                # Load history
                history_result = await db.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at.asc())
                    .limit(20)
                )
                history = [
                    {"role": m.role, "content": m.content}
                    for m in history_result.scalars().all()
                ]

                # Save user message
                user_msg = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation_id,
                    role=MessageRole.USER,
                    content=query,
                )
                db.add(user_msg)
                await db.commit()

                # Stream response
                full_response = []
                citations = []

                stream = await pipeline.answer(
                    query=query,
                    organization_id=org_id,
                    user_role=role,
                    conversation_history=history,
                    stream=True,
                )

                async for token in stream:
                    if "__CITATIONS__" in token:
                        parts = token.split("__CITATIONS__")
                        if parts[0]:
                            await websocket.send_json({"type": "token", "content": parts[0]})
                            full_response.append(parts[0])
                        citation_raw = parts[1].split("__END_CITATIONS__")[0]
                        citations = json.loads(citation_raw)
                        await websocket.send_json({"type": "citations", "citations": citations})
                    else:
                        await websocket.send_json({"type": "token", "content": token})
                        full_response.append(token)

                # Save assistant message
                asst_msg = Message(
                    id=str(uuid.uuid4()),
                    conversation_id=conversation_id,
                    role=MessageRole.ASSISTANT,
                    content="".join(full_response),
                    citations=citations,
                )
                db.add(asst_msg)
                await db.commit()

                await websocket.send_json({"type": "done", "message_id": asst_msg.id})

            except WebSocketDisconnect:
                logger.info("WebSocket disconnected", conversation_id=conversation_id)
                break

    except Exception as e:
        logger.error("WebSocket error", error=str(e), exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": "Server error"})
        except Exception:
            pass
