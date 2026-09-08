import os
import re
import urllib.parse
import snowflake.connector
import streamlit as st

DEFAULT_DATABASE = "PROD_GTED_HUB"
DEFAULT_SCHEMA = "CUR_CLIN_VEEVA_CTMS"
SNOWFLAKE_ACCOUNT = "colgatepalmoliveprod.us-central1.gcp"


def is_streamlit_cloud() -> bool:
    """Detects if running in Streamlit Cloud / headless environment."""
    return os.getenv("STREAMLIT_SERVER_HEADLESS", "false").lower() == "true" or not os.getenv("DISPLAY", "")


def build_snowflake_sso_url(user_id: str) -> str:
    """Constructs the Okta/SAML SSO authorization URL for Snowflake."""
    # Formats the standard Snowflake SAML initiation endpoint
    base_account = SNOWFLAKE_ACCOUNT.replace(".", "-")
    sso_url = (
        f"https://{base_account}.snowflakecomputing.com/console/login?"
        f"login_name={urllib.parse.quote(user_id)}&authenticator=externalbrowser"
    )
    return sso_url


@st.cache_resource(ttl=3600)
def _connect_snowflake_sso_direct(user_id: str, database: str, schema: str):
    """Cached connection for local desktop where browser popup works automatically."""
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
    Universal auth router:
    - If running locally: Automatically triggers browser pop-up.
    - If running on Streamlit Cloud: Displays clickable SSO login button + token listener.
    """
    # 1. Local Desktop Execution: Automatically launch browser popup
    if not is_streamlit_cloud():
        try:
            return _connect_snowflake_sso_direct(user_id, database, schema)
        except Exception as e:
            st.warning(f"Local browser popup failed, switching to cloud workflow: {e}")

    # 2. Streamlit Cloud / Headless Execution Flow
    st.info("🔐 **Snowflake SSO Authentication Required**")
    
    sso_link = build_snowflake_sso_url(user_id)

    st.markdown(
        f"""
        ### Step 1: Log in with Okta
        Click the button below to complete Single Sign-On in a new tab:
        
        [👉 **Click Here to Open Okta / Snowflake Login**]({sso_link})
        """
    )

    st.markdown(
        """
        ### Step 2: Complete Connection
        1. After authenticating in Okta, your browser will try to open a page starting with `http://localhost:43477/...` (or a similar port) and display an error page (e.g. *"This site can’t be reached"*).
        2. **Copy the full `http://localhost:...` URL** from your browser's address bar.
        3. **Paste the URL below** to sign in:
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
                st.success("✅ Successfully connected to Snowflake!")
                return conn
            except Exception as err:
                st.error(f"❌ Connection failed with provided token: {err}")
                st.stop()
        else:
            st.error("❌ Invalid URL format. Could not locate `token=` parameter in the pasted address.")
            st.stop()
    else:
        st.stop()
