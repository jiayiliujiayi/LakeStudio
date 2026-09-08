import os
import snowflake.connector
import streamlit as st

DEFAULT_DATABASE = "PROD_GTED_HUB"
DEFAULT_SCHEMA = "CUR_CLIN_VEEVA_CTMS"
SNOWFLAKE_ACCOUNT = "colgatepalmoliveprod.us-central1.gcp"


def is_streamlit_cloud() -> bool:
    """Detects if running in Streamlit Cloud / headless environment."""
    return os.getenv("STREAMLIT_SERVER_HEADLESS", "false").lower() == "true" or not os.getenv("DISPLAY", "")


@st.cache_resource(ttl=3600)
def _connect_snowflake_oauth(user_id: str, token: str, database: str, schema: str):
    """Cached connection helper for OAuth Tokens."""
    return snowflake.connector.connect(
        user=user_id,
        account=SNOWFLAKE_ACCOUNT,
        authenticator="oauth",
        token=token,
        warehouse="GTED_WH",
        database=database,
        schema=schema,
    )


@st.cache_resource(ttl=3600)
def _connect_snowflake_pat(user_id: str, pat_token: str, database: str, schema: str):
    """Cached connection helper for Programmatic Access Tokens (PAT) / Passwords."""
    return snowflake.connector.connect(
        user=user_id,
        password=pat_token,
        account=SNOWFLAKE_ACCOUNT,
        warehouse="GTED_WH",
        database=database,
        schema=schema,
    )


@st.cache_resource(ttl=3600)
def _connect_snowflake_sso(user_id: str, database: str, schema: str):
    """Cached connection helper for Local SSO."""
    return snowflake.connector.connect(
        user=user_id,
        account=SNOWFLAKE_ACCOUNT,
        authenticator="externalbrowser",
        database=database,
        schema=schema,
    )


def get_snowflake_connection(
    user_id: str,
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
):
    """
    Main authentication router (No @st.cache_resource on this function to allow UI widgets).
    """
    # 1. Streamlit Secrets (Cloud setup)
    if "snowflake" in st.secrets:
        sec = st.secrets["snowflake"]
        try:
            if "token" in sec:
                return _connect_snowflake_oauth(
                    sec.get("user", user_id), sec["token"], database, schema
                )
            elif "password" in sec:
                return _connect_snowflake_pat(
                    sec.get("user", user_id), sec["password"], database, schema
                )
        except Exception as e:
            st.error(f"❌ Connection failed using Streamlit secrets: {e}")

    # 2. Local Desktop SSO Execution
    if not is_streamlit_cloud():
        try:
            return _connect_snowflake_sso(user_id, database, schema)
        except Exception as e:
            st.warning(f"Browser SSO failed: {e}")

    # 3. Streamlit Cloud UI Fallback (Prompt Outside Cache)
    st.warning("🌐 **Browser SSO unavailable in Streamlit Cloud environment.**")
    
    auth_type = st.radio(
        "Select Authentication Method:",
        ["Programmatic Access Token (PAT) / Password", "OAuth Token"],
        key="cloud_auth_type"
    )

    token_input = st.text_input(
        f"Enter your Snowflake {auth_type}:",
        type="password",
        key="cloud_user_token_input"
    )

    if token_input:
        try:
            if "OAuth" in auth_type:
                conn = _connect_snowflake_oauth(user_id, token_input, database, schema)
            else:
                conn = _connect_snowflake_pat(user_id, token_input, database, schema)
            st.success("✅ Connected successfully!")
            return conn
        except Exception as err:
            st.error(f"❌ Failed to connect: {err}")
            st.stop()
    else:
        st.stop()
