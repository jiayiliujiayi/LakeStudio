import os
import snowflake.connector
import streamlit as st

DEFAULT_DATABASE = "PROD_GTED_HUB"
DEFAULT_SCHEMA = "CUR_CLIN_VEEVA_CTMS"
SNOWFLAKE_ACCOUNT = "colgatepalmoliveprod.us-central1.gcp"


def is_streamlit_cloud() -> bool:
    """Detects if the application is running in Streamlit Cloud / headless server."""
    return os.getenv("STREAMLIT_SERVER_HEADLESS", "false").lower() == "true" or not os.getenv("DISPLAY", "")


@st.cache_resource(ttl=3600)
def get_snowflake_connection(
    user_id: str,
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
):
    """
    Universal Snowflake Connection Helper:
    1. Uses Streamlit Secrets (PAT / OAuth / Key-Pair) if configured.
    2. Uses Local Browser SSO if running locally.
    3. Prompts for Programmatic Access Token (PAT) if running in Streamlit Cloud / Headless mode.
    """
    # Path 1: Streamlit Secrets configured in App Settings (Best for persistent cloud setup)
    if "snowflake" in st.secrets and "token" in st.secrets["snowflake"]:
        try:
            return snowflake.connector.connect(
                user=st.secrets["snowflake"].get("user", user_id),
                account=SNOWFLAKE_ACCOUNT,
                authenticator="oauth",
                token=st.secrets["snowflake"]["token"],
                warehouse=st.secrets["snowflake"].get("warehouse", "GTED_WH"),
                database=database,
                schema=schema,
            )
        except Exception as e:
            st.error(f"❌ Connection failed using secret token: {e}")

    # Path 2: Local Desktop Execution (Browser popup supported)
    if not is_streamlit_cloud():
        try:
            return snowflake.connector.connect(
                user=user_id,
                account=SNOWFLAKE_ACCOUNT,
                authenticator="externalbrowser",
                database=database,
                schema=schema,
            )
        except Exception as e:
            st.warning(f"Browser SSO failed locally. Falling back to Token Prompt: {e}")

    # Path 3: Headless / Streamlit Cloud — Prompt user for Programmatic Access Token (PAT)
    st.warning("🌐 **Browser SSO automatically unavailable in Streamlit Cloud environment.**")
    st.info(
        "To authenticate, please provide a **Snowflake Programmatic Access Token (PAT)** or OAuth token."
    )

    pat_token = st.text_input(
        "Enter your Snowflake Programmatic Access Token:",
        type="password",
        help="Generate this token from your Snowflake Account Settings or OAuth provider.",
        key="user_pat_token_input",
    )

    if pat_token:
        try:
            conn = snowflake.connector.connect(
                user=user_id,
                account=SNOWFLAKE_ACCOUNT,
                authenticator="oauth",
                token=pat_token,
                warehouse="GTED_WH",
                database=database,
                schema=schema,
            )
            st.success("✅ Successfully connected to Snowflake!")
            return conn
        except Exception as err:
            st.error(f"❌ Failed to connect with provided token: {err}")
            st.stop()
    else:
        st.stop()
