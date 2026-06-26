# Enterprise Knowledge Assistant (EKA)

> Production-grade, self-hosted RAG platform for enterprise knowledge management.  
> 100% open source · Local LLMs via Ollama · Zero proprietary AI APIs required.

---

## Overview

EKA is a full-stack enterprise RAG (Retrieval-Augmented Generation) system that lets your organization query its internal documents with AI-powered, cited answers. Built with security, multi-tenancy, and production readiness at its core.

```
┌─────────────────────────────────────────────────────────┐
│  Next.js 15 Frontend (App Router + Streaming)           │
├─────────────────────────────────────────────────────────┤
│  FastAPI Backend  │  Celery Workers  │  LangGraph Agents│
├──────────┬────────┴──────────────────┴──────────────────┤
│ PostgreSQL│  pgvector  │  Redis  │  Ollama (Local LLMs) │
└──────────┴────────────────────────────────────────────────┘
```

---

## Features

| Category | Features |
|----------|----------|
| **Auth** | Email/password, Google OAuth, GitHub OAuth, JWT + refresh token rotation, MFA-ready, account lockout |
| **RBAC** | 8 roles (Super Admin → Guest), per-document permissions, enforced before vector search |
| **Multi-tenant** | Full organization isolation, no cross-tenant data leakage |
| **Documents** | PDF, DOCX, XLSX, PPTX, TXT, MD, CSV, images, ZIP — with OCR fallback |
| **Retrieval** | Hybrid BM25 + vector search, reranking (BGE), query rewriting, multi-query |
| **AI Agents** | LangGraph pipeline: Planner → Retriever → Writer → FactChecker → Reviewer |
| **Citations** | Every answer cites document name, page number, section, confidence score |
| **Chat** | Streaming SSE + WebSocket, conversation history, pin/rename/delete |
| **Analytics** | Query volume, latency, top documents, hallucination tracking |
| **Admin** | User management, audit logs, worker status, document reindex |
| **Security** | Argon2 passwords, signed URLs, CSP headers, CSRF, rate limiting, audit trail |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Frontend | Next.js 15, TypeScript, Tailwind CSS, TanStack Query, Zustand |
| Backend | FastAPI, Python 3.12, SQLAlchemy 2.0 async, Pydantic v2 |
| AI | LangGraph, LlamaIndex, Ollama (Llama 3, Mistral, Qwen, Gemma) |
| Embeddings | nomic-embed-text, BAAI/bge-small-en-v1.5 |
| Reranker | BAAI/bge-reranker-base |
| OCR | Tesseract, EasyOCR |
| Database | PostgreSQL 16 + pgvector |
| Cache | Redis 7 |
| Workers | Celery + RedBeat |
| Proxy | Nginx |
| Deploy | Docker Compose |

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- 16 GB RAM (for local LLMs)
- NVIDIA GPU (optional, for faster inference)

### 1. Clone and Configure

```bash
git clone https://github.com/your-org/enterprise-rag.git
cd enterprise-rag

# Copy and fill in environment variables
cp .env.example .env
```

**Required values to set in `.env`:**
```bash
SECRET_KEY=<generate: openssl rand -hex 32>
JWT_SECRET=<generate: openssl rand -hex 32>
ENCRYPTION_KEY=<generate: openssl rand -hex 32>
POSTGRES_PASSWORD=<strong password>
REDIS_PASSWORD=<strong password>
FLOWER_PASSWORD=<strong password>
```

### 2. Start the Stack

```bash
docker compose up -d
```

### 3. Pull an LLM Model

```bash
# Wait for Ollama to start, then pull models
docker exec eka_ollama ollama pull llama3:8b
docker exec eka_ollama ollama pull nomic-embed-text
```

### 4. Run Database Migrations

```bash
docker exec eka_backend alembic upgrade head
```

### 5. Open the App

- **Frontend:** http://localhost:3000
- **API Docs:** http://localhost:8000/docs (dev mode)
- **Flower (workers):** http://localhost:5555

---

## Architecture

```
enterprise-rag/
├── frontend/              # Next.js 15 App Router
│   └── src/
│       ├── app/           # Pages (chat, documents, dashboard, auth)
│       ├── components/    # UI components
│       ├── lib/api/       # Axios API client
│       └── store/         # Zustand state management
│
├── backend/               # FastAPI application
│   └── app/
│       ├── api/v1/        # REST API routers (auth, chat, docs, admin)
│       ├── core/          # Config, security, logging
│       ├── domain/        # SQLAlchemy models, schemas
│       ├── infrastructure/# DB, cache, storage, LLM clients
│       ├── services/      # Business logic (ingestion, retrieval)
│       ├── agents/        # LangGraph multi-agent pipeline
│       └── workers/       # Celery background tasks
│
├── docker/
│   ├── nginx/nginx.conf   # Reverse proxy + SSL
│   └── postgres/init.sql  # DB initialization
│
├── docker-compose.yml
└── .env.example
```

---

## Retrieval Pipeline

```
User Query
    │
    ▼
Query Rewriting (LLM)          ← Uses conversation history
    │
    ▼
Multi-Query Generation          ← 3 alternative phrasings
    │
    ▼
Permission Filter               ← RBAC enforced at SQL level
    │
    ├── Vector Search (pgvector cosine)
    └── BM25 Full-Text Search
    │
    ▼
RRF Merge (Reciprocal Rank Fusion)
    │
    ▼
Cross-Encoder Reranking (BGE)
    │
    ▼
Context Compression
    │
    ▼
LLM Generation (Ollama)
    │
    ▼
Grounded Response + Citations
```

---

## Security

- **Passwords:** Argon2id hashing (64MB memory, 3 iterations)
- **JWT:** Short-lived access tokens (30min) + rotating refresh tokens
- **Refresh tokens:** Stored as SHA-256 hash only; rotation detects replay attacks
- **File uploads:** MIME type detection (not extension), size limits, executable rejection
- **Downloads:** Time-limited signed URLs (1hr expiry)
- **RBAC:** Permission check runs **before** vector search — unauthorized chunks never enter the context
- **Rate limiting:** Per-IP via SlowAPI + Nginx (auth: 10/min, API: 60/min, upload: 10/hr)
- **Headers:** CSP, HSTS, X-Frame-Options, X-Content-Type-Options
- **Audit log:** Immutable record of all auth events and sensitive operations
- **Multi-tenancy:** Organization ID on all queries; Nginx TrustedHost validation

---

## Development

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Start with hot reload
uvicorn app.main:app --reload --port 8000

# Run tests
pytest tests/ -v --cov=app
```

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:3000
npm run type-check   # TypeScript check
```

### Adding a New Document Type

1. Add MIME type to `StorageSettings.allowed_mime_types`
2. Add a `_parse_*` method to `DocumentParser` in `services/document/ingestion.py`
3. Update the dispatch dict in `DocumentParser.parse()`

### Adding a New LLM Model

Pull it in Ollama and set `DEFAULT_LLM_MODEL` in `.env`:
```bash
docker exec eka_ollama ollama pull qwen2.5:7b
# Update .env: DEFAULT_LLM_MODEL=qwen2.5:7b
docker compose restart backend worker
```

---

## API Reference

Full OpenAPI docs available at `/docs` in development mode.

Key endpoints:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/auth/register` | Register + create organization |
| POST | `/api/v1/auth/login` | Get access + refresh tokens |
| POST | `/api/v1/auth/refresh` | Rotate refresh token |
| POST | `/api/v1/documents/upload` | Upload documents (multipart) |
| GET | `/api/v1/documents` | List accessible documents |
| POST | `/api/v1/chat` | Create conversation |
| POST | `/api/v1/chat/{id}/messages` | Send message (SSE streaming) |
| WS | `/api/v1/chat/{id}/stream` | WebSocket streaming |
| POST | `/api/v1/search` | Direct hybrid search |
| POST | `/api/v1/search/query` | RAG query (non-streaming) |
| GET | `/api/v1/analytics/summary` | Usage analytics |

---

## Deployment

### Production Checklist

- [ ] Set all secrets in `.env` (never use defaults)
- [ ] Obtain SSL certificate and place in `docker/nginx/ssl/`
- [ ] Set `ENVIRONMENT=production` and `DEBUG=false`
- [ ] Set `CORS_ORIGINS` to your actual domain
- [ ] Run `alembic upgrade head` before starting
- [ ] Pull required Ollama models
- [ ] Configure backup for PostgreSQL data volume
- [ ] Set up log shipping (Loki, CloudWatch, etc.)
- [ ] Configure monitoring (Prometheus + Grafana)

### Generate SSL Certificate (self-signed for testing)

```bash
mkdir -p docker/nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout docker/nginx/ssl/key.pem \
  -out docker/nginx/ssl/cert.pem \
  -subj "/CN=localhost"
```

---

## License

MIT License — see LICENSE file.
