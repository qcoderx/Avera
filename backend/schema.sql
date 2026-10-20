-- Append-only event log. PostgreSQL assigns the global stream order.
CREATE TABLE IF NOT EXISTS avera_stream_records (
    sequence BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    data JSONB NOT NULL,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT avera_stream_records_object_key UNIQUE (object_type, object_id, occurred_at)
);

CREATE INDEX IF NOT EXISTS avera_stream_records_occurred_at_idx
    ON avera_stream_records (occurred_at);
