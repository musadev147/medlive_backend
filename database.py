import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

# MySQL by default; override with the DATABASE_URL environment variable
# (e.g. "sqlite:///./medivila.db" for local testing).
SQLALCHEMY_DATABASE_URL = os.getenv(
    "DATABASE_URL", "mysql+pymysql://root:12345678@localhost:3306/db"
)

connect_args = {"check_same_thread": False} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def sync_columns():
    """Bring already-existing tables in line with the models.

    `create_all` only creates new tables, so this adds columns introduced later. On MySQL it also
    fixes the type of columns whose type changed, but only while the table is still empty, so no
    stored data is ever converted.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    quote = engine.dialect.identifier_preparer.quote
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing = {c["name"]: c for c in inspector.get_columns(table.name)}
            empty = None
            for column in table.columns:
                col_type = column.type.compile(dialect=engine.dialect)
                if column.name not in existing:
                    conn.execute(text(f"ALTER TABLE {quote(table.name)} ADD COLUMN {quote(column.name)} {col_type}"))
                    continue
                if engine.dialect.name != "mysql" or column.primary_key:
                    continue
                current = existing[column.name]["type"].compile(dialect=engine.dialect)
                if _base_type(current) == _base_type(col_type):
                    continue
                if empty is None:
                    empty = conn.execute(text(f"SELECT COUNT(*) FROM {quote(table.name)}")).scalar() == 0
                if empty:
                    conn.execute(text(f"ALTER TABLE {quote(table.name)} MODIFY {quote(column.name)} {col_type} NULL"))


def _base_type(type_sql: str) -> str:
    # BOOL is stored as TINYINT(1); treat such pairs as equal so they are left alone.
    base = type_sql.upper().split("(")[0].strip()
    return {"BOOL": "TINYINT", "BOOLEAN": "TINYINT", "INT": "INTEGER"}.get(base, base)


# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
