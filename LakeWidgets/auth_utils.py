import os
import webbrowser
import snowflake.connector
from snowflake.connector.auth_webbrowser import AuthByWebBrowser
import streamlit as st

DEFAULT_DATABASE = "PROD_GTED_HUB"
DEFAULT_SCHEMA = "CUR_CLIN_VEEVA_CTMS"
SNOWFLAKE_ACCOUNT = "colgatepalmoliveprod.us-central1.gcp"


def _is_headless_environment() -> bool:
    """Detects if running in Streamlit Cloud, Docker, or a headless server."""
    if os.environ.get("STREAMLIT_SERVER_PORT") is not None:
        # Check if GUI web browser can be initialized
        try:
            browser = webbrowser.get()
            if hasattr(browser, "name") and browser.name in ("www-browser", "lynx", "links"):
                return True
        except Exception:
            return True
    return False


@st.cache_resource(ttl=3600)
def get_snowflake_connection(
    user_id: str,
    database: str = DEFAULT_DATABASE,
    schema: str = DEFAULT_SCHEMA,
):
    """
    Universal cached Snowflake connection helper.
    - Local Desktop: Launches browser SSO pop-up automatically.
    - Streamlit Cloud: Intercepts SSO redirect URL and displays clickable UI link + input field.
    """
    print(f"⚡ [LakeStudio Auth] Initializing Snowflake session for {user_id}...")

    # Check if Streamlit Cloud Secrets has service account credentials configured
    if "snowflake" in st.secrets:
        conn = snowflake.connector.connect(
            user=st.secrets["snowflake"].get("user", user_id),
            password=st.secrets["snowflake"]["password"],
            account=st.secrets["snowflake"].get("account", SNOWFLAKE_ACCOUNT),
            warehouse=st.secrets["snowflake"].get("warehouse", "GTED_WH"),
            database=database,
            schema=schema,
        )
        return conn

    # Desktop / Interactive SSO Browser Auth
    try:
        conn = snowflake.connector.connect(
            user=user_id,
            account=SNOWFLAKE_ACCOUNT,
            authenticator="externalbrowser",
            database=database,
            schema=schema,
        )
        return conn
    except Exception as err:
        # If external browser failed in cloud/headless mode, render manual link UI
        st.error("🔒 **Snowflake SSO Authentication Required**")
        st.info(
            "Automatic browser pop-ups are unavailable in hosted cloud environments. "
            "Please authenticate using your corporate Okta/SSO credentials below."
        )

        try:
            # Instantiate web browser authenticator manually to extract SSO URL
            authenticator = AuthByWebBrowser(
                application="LakeStudio",
                webbrowser_pkg=webbrowser,
            )
            # Generate SAML request URL
            sso_url = authenticator.get_sso_url(
                account=SNOWFLAKE_ACCOUNT,
                user=user_id,
                authenticator="externalbrowser",
            )
            
            if sso_url:
                st.markdown(f"### 👉 **[Click Here to Authenticate with Colgate SSO]({sso_url})**")
                st.caption(
                    "After authenticating in the new tab, copy the final redirected URL from your browser address bar and paste it below."
                )
                
                redirect_url = st.text_input("Paste Redirected SAML URL here:", key="sso_redirect_url_input")
                if redirect_url:
                    # Authenticate session using returned SAML response token
                    conn = authenticator.authenticate(redirect_url)
                    return conn
        except Exception as sso_err:
            st.warning(f"Could not automatically generate SSO URL: {str(sso_err)}")

        raise ConnectionError(f"Snowflake Authentication Failed: {str(err)}")
