# config/database.py

from sqlalchemy import URL, create_engine, text

from .settings import settings


class DatabaseManager:
    def __init__(self):
        database_url = URL.create(
            drivername=f"{settings.DB_DIALECT}+{settings.DB_DRIVER}",
            username=settings.DB_USER,
            password=settings.DB_PASSWORD,
            host=settings.DB_HOST,
            port=settings.DB_PORT,
            database=settings.DB_NAME,
        )

        self.engine = create_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
        )

    def execute_query(self, query_str: str, params: dict | None = None):
        """Executes a SQL query and returns all rows."""
        with self.engine.connect() as conn:
            result = conn.execute(text(query_str), params or {})
            return result.fetchall()


db = DatabaseManager()