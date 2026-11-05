"""Create the game schema and the restricted `runtime` role.

`db/001_initial.sql` and `db/002_runtime_role.sh` only run on a fresh
PostgreSQL container. RDS has no such entrypoint, so the EC2 bootstrap runs
this script once instead. It is idempotent: re-running it keeps the schema,
rotates the runtime password to match DATABASE_URL, and re-applies grants.

Environment:
    DATABASE_URL         runtime URL; its password becomes the role password
    DB_HOST              database host
    DB_NAME              database name
    DB_MASTER_USER       owner user (mines_admin on RDS)
    DB_MASTER_PASSWORD   owner password from Secrets Manager
"""

import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

SCHEMA = Path(__file__).resolve().parent.parent / "db" / "001_initial.sql"
ROLE = "runtime"


def required(name):
    value = os.environ.get(name)
    if not value:
        sys.exit("%s is required" % name)
    return value


def master_conninfo():
    return make_conninfo(
        host=required("DB_HOST"),
        dbname=required("DB_NAME"),
        user=required("DB_MASTER_USER"),
        password=required("DB_MASTER_PASSWORD"),
        connect_timeout=10,
        application_name="find-my-mines-init",
    )


def runtime_password():
    password = conninfo_to_dict(required("DATABASE_URL")).get("password")
    if not password:
        sys.exit("DATABASE_URL must include the runtime password")
    return password


def statements():
    for chunk in SCHEMA.read_text(encoding="utf-8").split(";"):
        statement = chunk.strip()
        if statement:
            yield statement


def role(name):
    return sql.Identifier(name)


def main():
    database = required("DB_NAME")
    password = runtime_password()

    with psycopg.connect(master_conninfo()) as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.players') IS NOT NULL")
        if cur.fetchone()[0]:
            print("schema already present")
        else:
            for statement in statements():
                cur.execute(statement)
            print("schema created from db/001_initial.sql")

        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (ROLE,))
        if cur.fetchone():
            cur.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}")
                        .format(role(ROLE), sql.Literal(password)))
            print("runtime role password refreshed")
        else:
            cur.execute(sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD {}")
                        .format(role(ROLE), sql.Literal(password)))
            print("runtime role created")

        cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}")
                    .format(sql.Identifier(database), role(ROLE)))
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role(ROLE)))
        cur.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE ON players, player_ratings TO {}")
                    .format(role(ROLE)))
        cur.execute(sql.SQL("GRANT SELECT, INSERT ON matches, match_participants TO {}")
                    .format(role(ROLE)))
        cur.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}")
                    .format(role(ROLE)))
        conn.commit()

    print("database ready for the game server")


if __name__ == "__main__":
    main()
