-- Keycloak data is bootstrap-managed. Only remove a schema created and still
-- owned by the migration user; never delete a schema transferred to Keycloak.
DO $$
DECLARE
    schema_owner name;
BEGIN
    SELECT pg_get_userbyid(nspowner)
    INTO schema_owner
    FROM pg_namespace
    WHERE nspname = 'keycloak';

    IF schema_owner = current_user THEN
        DROP SCHEMA keycloak CASCADE;
    END IF;
END
$$;
