import os
import pandas as pd
import streamlit as st
from LakeWidgets.auth_utils import get_snowflake_connection


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
        """Retrieves or creates the universal cached Snowflake connection."""
        self.conn = get_snowflake_connection(
            user_id=self.user_id,
            database=self.database,
            schema=self.schema,
        )
        return self.conn

    def _ensure_connection(self):
        """Internal helper to ensure active connection before execution."""
        if self.conn is None or self.conn.is_closed():
            self.connect()

    @st.cache_data(ttl=1800, show_spinner="Fetching table metadata from Snowflake...")
    def fetch_available_tables(_self) -> list:
        """Fetches active base tables from INFORMATION_SCHEMA."""
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
        """Fetches table preview with server-side row limit."""
        self._ensure_connection()

        limit_clause = f"LIMIT {limit}" if limit is not None else "LIMIT 500"
        query = f"SELECT * FROM {self.database}.{self.schema}.{table_name} {limit_clause};"

        cur = self.conn.cursor()
        try:
            cur.execute(query)
            cols = [desc[0].upper() for desc in cur.description]
            df = pd.DataFrame(cur.fetchall(), columns=cols)
            return df
        finally:
            cur.close()
