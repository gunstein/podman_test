-- Historical M11 transition. Normal M12 startup creates this bootstrap-owned
-- schema before migrations; the conditional branch keeps older test flows valid.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_namespace WHERE nspname = 'keycloak'
    ) THEN
        CREATE SCHEMA keycloak;
    END IF;
END
$$;
