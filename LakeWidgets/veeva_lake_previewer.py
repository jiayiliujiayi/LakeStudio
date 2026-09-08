import os
import pandas as pd
import snowflake.connector


class VeevaLakePreviewer:
    """Widget class to dynamically inspect available tables in PROD_GTED_HUB.CUR_CLIN_VEEVA_CTMS schema."""

    def __init__(self, user_id=None):
        self.user_id = user_id or os.environ.get("SNOWFLAKE_USER")
        if not self.user_id:
            raise ValueError(
                "❌ Initialization Failed: A valid corporate user_id must be provided."
            )
        self.database = "PROD_GTED_HUB"
        self.schema = "CUR_CLIN_VEEVA_CTMS"
        self.conn = None

    def connect(self):
        """Initializes Snowflake session via SSO using corporate account locator."""
        if self.conn is None or self.conn.is_closed():
            print(f"Initializing Snowflake connection for {self.user_id}...")
            self.conn = snowflake.connector.connect(
                user=self.user_id,
                account="colgatepalmoliveprod.us-central1.gcp",
                authenticator="externalbrowser",
            )
            cursor = self.conn.cursor()
            try:
                cursor.execute(f"USE DATABASE {self.database}")
                cursor.execute(f"USE SCHEMA {self.schema}")
            finally:
                cursor.close()
        return self.conn

    def _ensure_connection(self):
        """Internal helper to ensure active connection before query execution."""
        if self.conn is None or self.conn.is_closed():
            self.connect()

    def fetch_available_tables(self) -> list:
        """
        Dynamically iterates through Snowflake INFORMATION_SCHEMA to fetch 
        all active table names in real-time.
        """
        self._ensure_connection()

        query = f"""
        SELECT TABLE_NAME 
        FROM {self.database}.INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_SCHEMA = '{self.schema}' 
          AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME ASC;
        """
        cur = self.conn.cursor()
        try:
            cur.execute(query)
            tables = [row[0] for row in cur.fetchall()]
            return tables
        finally:
            cur.close()

    def fetch_table_preview(self, table_name: str, limit: int = None) -> pd.DataFrame:
        """Fetches rows dynamically for the specified target table."""
        self._ensure_connection()

        limit_clause = f"LIMIT {limit}" if limit is not None else ""
        query = f"SELECT * FROM {self.database}.{self.schema}.{table_name} {limit_clause};"

        cur = self.conn.cursor()
        try:
            cur.execute(query)
            cols = [desc[0].upper() for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
            return df
        finally:
            cur.close()
