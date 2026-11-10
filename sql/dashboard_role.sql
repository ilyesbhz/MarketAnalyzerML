-- Run ONCE as the PostgreSQL admin (psql or pgAdmin), after init_db. Replace CHANGE_ME first.
-- Gives the dashboard a read-only login that can see the analytics views and nothing else
-- (no access to core.* tables, no write rights).
DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'dashboard_reader') THEN
        CREATE ROLE dashboard_reader LOGIN PASSWORD '00000000';
    END IF;
END $$;
GRANT CONNECT ON DATABASE jobmarket TO dashboard_reader;
GRANT USAGE ON SCHEMA analytics TO dashboard_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO dashboard_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics GRANT SELECT ON TABLES TO dashboard_reader;
