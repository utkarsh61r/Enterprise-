# Enterprise RAG (Retrieval-Augmented Generation)

A comprehensive, production-ready Retrieval-Augmented Generation system designed for enterprises. This platform combines document management, intelligent search, and conversational AI to provide context-aware responses powered by your knowledge base.

## 🎯 Overview

Enterprise RAG is a full-stack application that enables organizations to build intelligent document retrieval and AI-powered question-answering systems. It integrates cutting-edge LLM technology with enterprise-grade document management, vector search, and caching infrastructure.

**Key Capabilities:**
- Document ingestion and processing
- Vector embeddings and semantic search
- RAG-based chat interface
- Multi-user authentication and authorization
- Admin analytics and monitoring
- Scalable microservices architecture

## 🚀 Features

### Core Features
- **Smart Document Management** - Upload, process, and organize documents at scale
- **Vector Search** - Semantic search using embeddings for precise retrieval
- **Conversational AI** - RAG-powered chat that references source documents
- **Multi-tenant Support** - Workspace/organization-based data isolation
- **Authentication & Authorization** - JWT-based security with role management

### Enterprise Features
- **Analytics Dashboard** - Track usage, queries, and performance metrics
- **Admin Console** - Manage users, documents, and system settings
- **API-First Design** - RESTful APIs for all functionality
- **Docker Deployment** - Containerized architecture for easy scaling
- **Extensible LLM Integration** - Support for multiple LLM providers

## 📋 Project Structure

```
enterprise-rag/
├── backend/              # FastAPI backend application
│   ├── app/
│   │   ├── agents/       # AI orchestration logic
│   │   ├── api/          # REST API endpoints
│   │   ├── core/         # Configuration & security
│   │   ├── domain/       # Business logic & models
│   │   ├── infrastructure/  # Database, cache, LLM, embeddings
│   │   ├── services/     # Business services
│   │   └── workers/      # Background job processing
│   ├── alembic/          # Database migrations
│   └── tests/            # Test suites
├── frontend/             # Next.js React application
│   ├── src/
│   │   ├── app/          # Page routes
│   │   ├── components/   # Reusable UI components
│   │   ├── lib/          # Utilities and API client
│   │   ├── store/        # State management (Zustand)
│   │   └── types/        # TypeScript types
├── worker/               # Celery worker for async tasks
├── shared/               # Shared types and constants
├── docker/               # Docker configuration
│   ├── nginx/            # Reverse proxy config
│   ├── postgres/         # Database initialization
│   └── redis/            # Cache configuration
├── docs/                 # Documentation
└── docker-compose.yml    # Multi-container orchestration
```

## 🏗️ Architecture

### Technology Stack

**Backend:**
- **Framework:** FastAPI (Python)
- **Database:** PostgreSQL with SQLAlchemy ORM
- **Cache:** Redis
- **Task Queue:** Celery
- **LLM:** Ollama (local) / OpenAI (cloud)
- **Embeddings:** Sentence Transformers
- **Vector DB:** Milvus/Weaviate compatible

**Frontend:**
- **Framework:** Next.js 13+ (React)
- **Styling:** Tailwind CSS
- **State Management:** Zustand
- **API Client:** Axios
- **UI Components:** Custom + Shadcn

**Infrastructure:**
- **Containerization:** Docker & Docker Compose
- **Web Server:** Nginx
- **Message Queue:** Redis/Celery
- **Monitoring:** Structured logging

### System Architecture

```
┌─────────────────────────────────────────────────┐
│            Next.js Frontend (React)             │
├─────────────────────────────────────────────────┤
│                   Nginx Reverse Proxy            │
├─────────────────────────────────────────────────┤
│  FastAPI Backend   │  Celery Workers  │         │
│  - REST API        │  - Doc Processing│         │
│  - Authentication  │  - Embedding Gen │  Redis  │
│  - Chat Logic      │  - Async Tasks   │  Cache  │
├─────────────────────────────────────────────────┤
│                PostgreSQL Database              │
├─────────────────────────────────────────────────┤
│  External Services                              │
│  - LLM (Ollama/OpenAI)                         │
│  - Embedding Service                            │
│  - Vector Store                                 │
└─────────────────────────────────────────────────┘
```

## 🛠️ Installation & Setup

### Prerequisites

- **Docker & Docker Compose** (recommended for full stack)
- **Python 3.10+** (for local backend development)
- **Node.js 18+** (for local frontend development)
- **Git**

### Quick Start with Docker Compose

```bash
# Clone the repository
git clone https://github.com/utkarsh61r/Enterprise-.git
cd enterprise-rag

# Create environment file
cp .env.example .env

# Build and start all services
docker-compose up -d

# Wait for services to initialize (2-3 minutes)
docker-compose logs -f

# Access the application
# Frontend: http://localhost:3000
# API Docs: http://localhost:8000/docs
# Admin: http://localhost:3000/admin
```

### Local Development Setup

#### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env

# Run database migrations
alembic upgrade head

# Start development server
uvicorn app.main:app --reload
```

#### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Create environment file
cp .env.example .env.local

# Start development server
npm run dev
```

## 🔐 Environment Configuration

Create a `.env` file in the project root:

```env
# Database
DATABASE_URL=postgresql://user:password@postgres:5432/enterprise_rag
REDIS_URL=redis://redis:6379/0

# JWT & Security
SECRET_KEY=your-super-secret-key-change-this
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# LLM Configuration
OLLAMA_BASE_URL=http://ollama:11434
OPENAI_API_KEY=sk-...

# Embedding Service
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2

# Frontend
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000

# Email (optional)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

## 📚 API Documentation

Once running, interactive API documentation is available at:

- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc

### Main API Endpoints

**Authentication:**
- `POST /api/v1/auth/login` - User login
- `POST /api/v1/auth/register` - User registration
- `POST /api/v1/auth/refresh` - Refresh token

**Chat:**
- `POST /api/v1/chat/create` - Create conversation
- `GET /api/v1/chat/{conversation_id}` - Get conversation
- `POST /api/v1/chat/{conversation_id}/messages` - Send message
- `WebSocket /api/v1/chat/stream` - Real-time streaming

**Documents:**
- `POST /api/v1/documents/upload` - Upload document
- `GET /api/v1/documents` - List documents
- `DELETE /api/v1/documents/{doc_id}` - Delete document

**Search:**
- `POST /api/v1/search/semantic` - Semantic search
- `POST /api/v1/search/hybrid` - Hybrid search

**Analytics:**
- `GET /api/v1/analytics/dashboard` - Dashboard metrics
- `GET /api/v1/analytics/usage` - Usage statistics

## 🧪 Testing

```bash
cd backend

# Run all tests
pytest

# Run with coverage
pytest --cov=app

# Run specific test file
pytest tests/unit/test_auth.py

# Integration tests
pytest tests/integration/
```

## 📦 Deployment

### Docker Compose (Production)

```bash
# Build images
docker-compose build

# Start services
docker-compose up -d

# View logs
docker-compose logs -f app

# Scale workers
docker-compose up -d --scale worker=3
```

### Kubernetes Deployment

Helm charts and Kubernetes manifests are available in `docs/deployment/k8s/`.

```bash
# Deploy to Kubernetes
kubectl apply -f docs/deployment/k8s/
```

## 🔄 Data Flow

### Document Ingestion Pipeline

```
User Upload
    ↓
File Storage (S3/Local)
    ↓
Content Extraction (PDF, DOCX, TXT)
    ↓
Text Chunking & Preprocessing
    ↓
Embedding Generation
    ↓
Vector Storage
    ↓
Metadata Indexing
```

### RAG Chat Pipeline

```
User Query
    ↓
Query Embedding
    ↓
Semantic Search (Vector DB)
    ↓
Retrieve Top-K Documents
    ↓
Prompt Construction
    ↓
LLM Generation
    ↓
Response Streaming
```

## 🔒 Security

- **JWT Authentication** with token refresh
- **Role-Based Access Control (RBAC)**
- **Input Validation** and sanitization
- **SQL Injection Prevention** via ORM
- **Rate Limiting** on API endpoints
- **HTTPS/TLS** support
- **Environment Variable Secrets** management

## 📊 Monitoring & Logging

- **Structured Logging** with JSON output
- **Request Tracing** with correlation IDs
- **Performance Metrics** via Prometheus
- **Health Checks** on all services
- **Error Alerting** (configurable)

Access logs and metrics:
```bash
docker-compose logs -f app
docker-compose logs -f worker
docker stats
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

### Development Guidelines

- Follow PEP 8 for Python code
- Use TypeScript for frontend code
- Write tests for new features
- Update documentation
- Use conventional commit messages

## 🐛 Troubleshooting

### Services Won't Start

```bash
# Check logs
docker-compose logs

# Rebuild images
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

### Database Connection Issues

```bash
# Check PostgreSQL status
docker-compose ps postgres

# Reset database
docker-compose down -v
docker-compose up -d postgres
docker-compose exec postgres psql -U user -d enterprise_rag -f /docker-entrypoint-initdb.d/init.sql
```

### LLM Not Responding

```bash
# Check Ollama service
curl http://localhost:11434/api/tags

# Verify model is pulled
docker-compose exec ollama ollama list

# Pull required model
docker-compose exec ollama ollama pull llama2
```

## 📖 Documentation

Full documentation available in `docs/`:
- [Architecture Overview](docs/architecture/)
- [API Reference](docs/api/)
- [Deployment Guide](docs/deployment/)
- [Contributing Guide](CONTRIBUTING.md)

## 📄 License

This project is licensed under the MIT License - see [LICENSE](LICENSE) file for details.

## 👥 Support

- **Issues:** [GitHub Issues](https://github.com/utkarsh61r/Enterprise-/issues)
- **Discussions:** [GitHub Discussions](https://github.com/utkarsh61r/Enterprise-/discussions)
- **Email:** support@example.com

## 🙏 Acknowledgments

Built with:
- FastAPI & Starlette
- Next.js & React
- LangChain
- Ollama
- PostgreSQL
- Redis

## 📈 Roadmap

- [ ] Multi-language support
- [ ] Fine-tuning capabilities
- [ ] Advanced RAG techniques (HyDE, Self-RAG)
- [ ] Real-time collaboration features
- [ ] Mobile application
- [ ] Enterprise SSO integration
- [ ] Advanced analytics & insights
- [ ] Custom embedding models

---

**Made with ❤️ for the Enterprise**
