CREATE TABLE entries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message TEXT NOT NULL CHECK (length(btrim(message)) BETWEEN 1 AND 4000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The platform creates roles; this app grants only the operations it uses.
GRANT SELECT, INSERT, DELETE ON TABLE entries TO guestbook_app;
GRANT USAGE, SELECT ON SEQUENCE entries_id_seq TO guestbook_app;
