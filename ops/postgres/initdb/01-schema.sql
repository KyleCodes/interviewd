-- interviewd schema. The queue IS a Postgres table (claim with SKIP LOCKED).
-- Kept intentionally minimal: the app domain is throwaway; the platform is the point.

-- The work queue. One row per interview awaiting AI scoring. Deleted on ack.
CREATE TABLE jobs_queue (
  id           BIGSERIAL PRIMARY KEY,
  interview_id TEXT        NOT NULL,          -- cache key; repeats => cache hits
  received_at  TIMESTAMPTZ NOT NULL DEFAULT now()  -- powers "oldest job age"
);
-- Depth query hits count(*); oldest-age hits min(received_at). Both trivial.
CREATE INDEX ON jobs_queue (received_at);

-- Completed scores (the "result store"). The hot copy lives in Redis; this is
-- the durable record of what was produced.
CREATE TABLE scores (
  interview_id TEXT PRIMARY KEY,
  score        INT         NOT NULL,          -- fake LLM rubric score 0-100
  compute_ms   INT         NOT NULL,          -- how long the "LLM call" took
  produced_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON scores (produced_at);
