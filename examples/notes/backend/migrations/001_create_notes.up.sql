CREATE TABLE IF NOT EXISTS notes (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT ''
);

-- The runtime role notes_app (the platform makes it, app_installer.database_roles) may
-- read and write rows here, and nothing more: the migration that creates a table grants
-- its rights, so they stay with it.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE notes TO notes_app;
GRANT USAGE, SELECT ON SEQUENCE notes_id_seq TO notes_app;
