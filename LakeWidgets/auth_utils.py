import os
import re
import snowflake.connector
import streamlit as st

DEFAULT_DATABASE = "PROD_GTED_HUB"
DEFAULT_SCHEMA = "CUR_CLIN_VEEVA_CTMS"
SNOWFLAKE_ACCOUNT = "colgatepalmoliveprod.us-central1.gcp"


def is_streamlit_cloud() -> bool:
    """Detects if running in Streamlit Cloud / headless environment."""
    return os.getenv("STREAMLIT_SERVER_HEADLESS", "false").lower() == "true" or not os.getenv("DISPLAY", "")


@st.cache_resource(ttl=3600)
def _connect_snowflake_sso_direct(user_id: str, database: str, schema: str):
    """Cached connection for local desktop where localhost redirect works automatically."""
    return snowflake.connector.connect(
        user=user_id,
        account=SNOWFLAKE_ACCOUNT,
        authenticator="externalbrowser",
        database=database,
        schema=schema,
    )


@st.cache_resource(ttl=3600)
def _connect_snowflake_via_token(user_id: str, token: str, database: str, schema: str):
    """Cached connection using extracted SAML/SSO authorization token."""
    return snowflake.connector.connect(
        user=user_id,
        account=SNOWFLAKE_ACCOUNT,
        authenticator="oauth",
        token=token,
        warehouse="GTED_WH",
        database=database,
        schema=schema,
    )


def get_snowflake_connection(
    user_id: str,
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
):
    """
    Main authentication router with manual localhost URL parsing for Streamlit Cloud.
    """
    # 1. Local Desktop Execution: Try direct automatic browser SSO
    if not is_streamlit_cloud():
        try:
            return _connect_snowflake_sso_direct(user_id, database, schema)
        except Exception as e:
            st.warning(f"Automatic browser SSO failed: {e}")

    # 2. Streamlit Cloud / Headless Execution
    st.info("🔐 **Snowflake SSO Authorization Required**")
    st.markdown(
        """
        **Instructions to complete authentication:**
        1. Open your browser and complete your Okta / Single Sign-On authentication if prompted.
        2. When Okta finishes, your browser will try to navigate to a URL starting with `http://localhost:43477/...` (or a similar port) and fail to load.
        3. **Copy the full `http://localhost:...` address** from your browser's address bar and paste it below.
        """
    )

    redirect_url = st.text_input(
        "Paste the full redirected 'http://localhost:...' URL here:",
        type="default",
        key="sso_redirect_url_input",
        placeholder="http://localhost:43477/?token=..."
    )

    if redirect_url:
        # Extract token parameter from pasted URL
        token_match = re.search(r"[?&]token=([^&]+)", redirect_url)
        if token_match:
            extracted_token = token_match.group(1)
            try:
                conn = _connect_snowflake_via_token(
                    user_id, extracted_token, database, schema
                )
                st.success("✅ Successfully authenticated!")
                return conn
            except Exception as err:
                st.error(f"❌ Connection failed with provided token: {err}")
                st.stop()
        else:
            st.error("❌ Invalid URL format. Could not locate `token=` parameter in the pasted address.")
            st.stop()
    else:
        st.stop()
