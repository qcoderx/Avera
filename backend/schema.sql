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

-- Transactional Kafka publication intent. Rows are inserted with the canonical event.
CREATE TABLE IF NOT EXISTS avera_event_outbox (
    sequence BIGINT PRIMARY KEY REFERENCES avera_stream_records (sequence),
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_until TIMESTAMPTZ,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    delivered_at TIMESTAMPTZ,
    dead_lettered_at TIMESTAMPTZ,
    last_error TEXT,
    inserted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT avera_event_outbox_single_terminal_state
        CHECK (delivered_at IS NULL OR dead_lettered_at IS NULL)
);

ALTER TABLE avera_event_outbox
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS avera_event_outbox_pending_idx
    ON avera_event_outbox (sequence)
    WHERE delivered_at IS NULL AND dead_lettered_at IS NULL;

-- Queue legacy log rows once when upgrading from the pre-outbox schema.
INSERT INTO avera_event_outbox (sequence)
SELECT sequence FROM avera_stream_records
ON CONFLICT (sequence) DO NOTHING;
