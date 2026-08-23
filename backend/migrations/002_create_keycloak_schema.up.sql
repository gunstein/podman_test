DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_namespace WHERE nspname = 'keycloak'
    ) THEN
        CREATE SCHEMA keycloak;
    END IF;
END
$$;
