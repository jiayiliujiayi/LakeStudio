import os
import pandas as pd
import snowflake.connector
import streamlit as st


@st.cache_resource(ttl=3600)
def _get_cached_snowflake_connection(user_id: str, database: str, schema: str):
    """
    Caches the Snowflake connection object in Streamlit memory for 1 hour.
    Triggers Okta/SSO browser prompt ONLY ONCE per session.
    """
    print(f"⚡ Establishing single cached Snowflake connection for {user_id}...")
    conn = snowflake.connector.connect(
        user=user_id,
        account="colgatepalmoliveprod.us-central1.gcp",
        authenticator="externalbrowser",
    )
    cursor = conn.cursor()
    try:
        cursor.execute(f"USE DATABASE {database}")
        cursor.execute(f"USE SCHEMA {schema}")
    finally:
        cursor.close()
    return conn


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
        """Retrieves or reuses the cached Snowflake session."""
        self.conn = _get_cached_snowflake_connection(
            user_id=self.user_id,
            database=self.database,
            schema=self.schema
        )
        return self.conn

    def _ensure_connection(self):
        """Internal helper to guarantee connection state."""
        if self.conn is None or self.conn.is_closed():
            self.connect()

    @st.cache_data(ttl=1800, show_spinner="Fetching table list from Snowflake...")
    def fetch_available_tables(_self) -> list:
        """
        Fetches and caches active table names from INFORMATION_SCHEMA for 30 mins.
        """
        _self._ensure_connection()

        query = f"""
        SELECT TABLE_NAME 
        FROM {_self.database}.INFORMATION_SCHEMA.TABLES 
        WHERE TABLE_SCHEMA = '{_self.schema}' 
          AND TABLE_TYPE = 'BASE TABLE'
        ORDER BY TABLE_NAME ASC;
        """
        cur = _self.conn.cursor()
        try:
            cur.execute(query)
            tables = [row[0] for row in cur.fetchall()]
            return tables
        finally:
            cur.close()

    def fetch_table_preview(self, table_name: str, limit: int = 100) -> pd.DataFrame:
        """
        Fetches a fast preview of the target table. 
        Defaults to 100 rows to ensure rapid local response times.
        """
        self._ensure_connection()

        # Enforce server-side limit for speed
        limit_clause = f"LIMIT {limit}" if limit else "LIMIT 500"
        query = f"SELECT * FROM {self.database}.{self.schema}.{table_name} {limit_clause};"

        cur = self.conn.cursor()
        try:
            cur.execute(query)
            cols = [desc[0].upper() for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
            return df
        finally:
            cur.close()
