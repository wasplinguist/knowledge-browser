CREATE TABLE bulk_import_runs (
  id uuid PRIMARY KEY,
  manifest_digest text NOT NULL UNIQUE,
  dataset_version text NOT NULL,
  embedding_model text NOT NULL,
  embedding_dimensions integer NOT NULL CHECK (embedding_dimensions = 1536),
  status text NOT NULL CHECK (status IN ('loading','indexing','complete','failed')),
  safe_error text,
  cache_hits bigint NOT NULL DEFAULT 0,
  cache_misses bigint NOT NULL DEFAULT 0,
  provider_requests bigint NOT NULL DEFAULT 0,
  request_concurrency integer NOT NULL DEFAULT 0,
  retries bigint NOT NULL DEFAULT 0,
  sentences_per_second double precision NOT NULL DEFAULT 0,
  estimated_remaining_seconds double precision NOT NULL DEFAULT 0,
  stall_after_seconds double precision NOT NULL DEFAULT 150,
  started_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz
);
CREATE TABLE bulk_import_progress (
  run_id uuid NOT NULL REFERENCES bulk_import_runs(id) ON DELETE CASCADE,
  source text NOT NULL CHECK (source IN ('jira','confluence','slack','github')),
  next_line bigint NOT NULL DEFAULT 1,
  next_offset bigint NOT NULL DEFAULT 0,
  documents bigint NOT NULL DEFAULT 0,
  chunks bigint NOT NULL DEFAULT 0,
  sentences bigint NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, source)
);
CREATE TABLE bulk_embedding_cache (
  run_id uuid NOT NULL REFERENCES bulk_import_runs(id) ON DELETE CASCADE,
  content_hash text NOT NULL,
  sentence text NOT NULL,
  embedding halfvec(1536) NOT NULL,
  PRIMARY KEY (run_id, content_hash)
);
