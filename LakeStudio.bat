@echo off
:: Automatically navigate to the project directory
cd /d "%~dp0"

:: First-time setup check: Create venv and install dependencies if missing
if not exist ".venv" (
    echo ⚡ Initializing LakeStudio environment for first-time setup...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo ❌ Installation failed! Please check your internet connection or requirements.txt.
        pause
        exit /b %ERRORLEVEL%
    )
) else (
    call .venv\Scripts\activate.bat
)

:: Launch Streamlit in the default browser
streamlit run app.py
