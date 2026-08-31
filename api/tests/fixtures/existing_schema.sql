CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL UNIQUE,
  name text NOT NULL,
  raw_payload jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE groups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  name text NOT NULL UNIQUE,
  raw_payload jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE group_memberships (
  group_id uuid NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (group_id, user_id)
);
CREATE TABLE permission_sets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  visibility text NOT NULL CHECK (visibility IN ('company', 'restricted')),
  raw_payload jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE permission_set_users (
  permission_set_id uuid NOT NULL REFERENCES permission_sets(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  PRIMARY KEY (permission_set_id, user_id)
);
CREATE TABLE permission_set_groups (
  permission_set_id uuid NOT NULL REFERENCES permission_sets(id) ON DELETE CASCADE,
  group_id uuid NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
  PRIMARY KEY (permission_set_id, group_id)
);

CREATE TABLE documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source text NOT NULL CHECK (source IN ('jira','confluence','slack','github')),
  kind text NOT NULL,
  external_id text NOT NULL,
  parent_document_id uuid REFERENCES documents(id) ON DELETE CASCADE,
  root_document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  permission_set_id uuid NOT NULL REFERENCES permission_sets(id),
  title text NOT NULL,
  body text NOT NULL DEFAULT '',
  author text,
  url text,
  container text,
  raw_payload jsonb NOT NULL DEFAULT '{}',
  source_created_at timestamptz,
  source_updated_at timestamptz,
  indexed_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (source, external_id)
);

CREATE TABLE chunks (
  source text NOT NULL,
  id text NOT NULL,
  document_id uuid NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  field text NOT NULL,
  text text NOT NULL,
  chunk_index integer NOT NULL,
  content_hash text NOT NULL,
  metadata jsonb NOT NULL DEFAULT '{}',
  fts tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
  PRIMARY KEY (source, id)
) PARTITION BY LIST (source);
CREATE TABLE jira_chunks PARTITION OF chunks FOR VALUES IN ('jira');
CREATE TABLE confluence_chunks PARTITION OF chunks FOR VALUES IN ('confluence');
CREATE TABLE slack_chunks PARTITION OF chunks FOR VALUES IN ('slack');
CREATE TABLE github_chunks PARTITION OF chunks FOR VALUES IN ('github');
CREATE INDEX chunks_fts_idx ON chunks USING gin (fts);

CREATE TABLE sentences (
  source text NOT NULL,
  id bigint GENERATED ALWAYS AS IDENTITY,
  chunk_id text NOT NULL,
  sentence_index integer NOT NULL,
  sentence text NOT NULL,
  embedding halfvec(1536),
  embedding_model text NOT NULL,
  PRIMARY KEY (source, id),
  FOREIGN KEY (source, chunk_id) REFERENCES chunks(source, id) ON DELETE CASCADE
) PARTITION BY LIST (source);
CREATE TABLE jira_sentences PARTITION OF sentences FOR VALUES IN ('jira');
CREATE TABLE confluence_sentences PARTITION OF sentences FOR VALUES IN ('confluence');
CREATE TABLE slack_sentences PARTITION OF sentences FOR VALUES IN ('slack');
CREATE TABLE github_sentences PARTITION OF sentences FOR VALUES IN ('github');
CREATE INDEX sentences_embedding_idx ON sentences USING hnsw (embedding halfvec_cosine_ops);

CREATE TABLE search_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  session_id uuid,
  query text NOT NULL,
  normalized_query text NOT NULL,
  source text CHECK (source IS NULL OR source IN ('jira', 'confluence', 'slack', 'github')),
  profile text NOT NULL,
  result_ids jsonb NOT NULL,
  result_count integer NOT NULL CHECK (result_count >= 0),
  embedding_available boolean NOT NULL,
  duration_ms integer NOT NULL CHECK (duration_ms > 0)
);

CREATE TABLE search_clicks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  search_id uuid NOT NULL REFERENCES search_events(id) ON DELETE CASCADE,
  user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source text NOT NULL CHECK (source IN ('jira', 'confluence', 'slack', 'github')),
  external_id text NOT NULL,
  rank integer NOT NULL CHECK (rank > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (search_id, source, external_id)
);
