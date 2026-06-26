-- =============================================================================
-- Enterprise Knowledge Assistant - PostgreSQL Initialization
-- Runs on first container startup to set up extensions and baseline config
-- =============================================================================

-- pgvector: vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm: trigram-based fuzzy text search
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- uuid-ossp: UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- pgcrypto: cryptographic functions
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Set default text search config
SET default_text_search_config = 'english';

-- =============================================================================
-- tsvector update trigger for document_chunks
-- Automatically keeps content_tsv in sync with content column
-- =============================================================================
CREATE OR REPLACE FUNCTION update_chunk_tsvector()
RETURNS TRIGGER AS $$
BEGIN
    NEW.content_tsv := to_tsvector('english', COALESCE(NEW.content, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Note: The trigger itself is applied after Alembic creates the table
-- This function is defined here so it's ready when needed

-- =============================================================================
-- Partitioning hints (for very large deployments)
-- query_analytics can be partitioned by created_at for performance
-- =============================================================================
-- Example (enable when table grows beyond 50M rows):
-- ALTER TABLE query_analytics PARTITION BY RANGE (created_at);

-- =============================================================================
-- Row-level security policies (additional defense-in-depth)
-- Enforces organization isolation at the database level
-- =============================================================================
-- These are applied after Alembic migrations create the tables.
-- Enable with: ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
