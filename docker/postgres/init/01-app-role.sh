#!/bin/sh
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_DB_USER}') THEN
        EXECUTE format(
            'CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
            '${APP_DB_USER}',
            '${APP_DB_PASSWORD}'
        );
    ELSE
        EXECUTE format(
            'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
            '${APP_DB_USER}',
            '${APP_DB_PASSWORD}'
        );
    END IF;
END
\$\$;

SELECT format('CREATE DATABASE %I OWNER %I', '${APP_DB_NAME}', '${APP_DB_USER}')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${APP_DB_NAME}')
\gexec
SQL
