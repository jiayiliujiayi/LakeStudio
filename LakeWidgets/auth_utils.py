import io
import os
import sys
import webbrowser
import snowflake.connector
import streamlit as st

# Central database and schema definitions
DEFAULT_DATABASE = "PROD_GTED_HUB"
DEFAULT_SCHEMA = "CUR_CLIN_VEEVA_CTMS"
SNOWFLAKE_ACCOUNT = "colgatepalmoliveprod.us-central1.gcp"


@st.cache_resource(ttl=3600)
def get_snowflake_connection(
    user_id: str,
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
):
    """
    Universal cached Snowflake connection helper for all LakeStudio modules.
    - Reuses an active connection for 1 hour across all widgets.
    - Automatically launches a browser locally.
    - Renders a clickable SSO link in Streamlit UI if browser popup fails (cloud/headless).
    """
    print(f"⚡ [LakeStudio Auth] Initializing Snowflake session for {user_id}...")

    # Detect if local environment supports opening a desktop browser
    can_open_browser = True
    try:
        browser = webbrowser.get()
        if hasattr(browser, "name") and browser.name in ("www-browser", "lynx", "links"):
            can_open_browser = False
    except Exception:
        can_open_browser = False

    # Redirect stdout to capture the login URL emitted by snowflake.connector
    captured_output = io.StringIO()
    original_stdout = sys.stdout

    try:
        if not can_open_browser:
            sys.stdout = captured_output

        conn = snowflake.connector.connect(
            user=user_id,
            account=SNOWFLAKE_ACCOUNT,
            authenticator="externalbrowser",
        )
    except Exception as err:
        output_text = captured_output.getvalue()

        # Extract URL if generated during authentication prompt
        if "https://" in output_text:
            url_start = output_text.find("https://")
            url = output_text[url_start:].split()[0]
            st.error("🔒 **SSO Authentication Required**")
            st.markdown(
                f"Automatic browser pop-up is unavailable in this environment.\n\n"
                f"👉 **[Click here to authenticate with Colgate SSO]({url})**"
            )
        raise ConnectionError(f"Snowflake SSO Authentication failed: {str(err)}")
    finally:
        sys.stdout = original_stdout

    cursor = conn.cursor()
    try:
        cursor.execute(f"USE DATABASE {database}")
        cursor.execute(f"USE SCHEMA {schema}")
    finally:
        cursor.close()

    return conn
