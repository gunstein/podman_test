CREATE TABLE IF NOT EXISTS todos (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    completed BOOLEAN NOT NULL DEFAULT FALSE
);

-- The runtime role todo_app (the platform makes it, app_installer.database_roles) may
-- read and write rows here, and nothing more: the migration that creates a table grants
-- its rights, so they stay with it.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE todos TO todo_app;
GRANT USAGE, SELECT ON SEQUENCE todos_id_seq TO todo_app;
