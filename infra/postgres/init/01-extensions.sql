-- Runs once when the pgdata volume is first created.
CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector: embeddings for EvidenceChunk
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- trigram similarity for alias / fuzzy lookup
CREATE EXTENSION IF NOT EXISTS unaccent;    -- diacritics-insensitive matching helper
