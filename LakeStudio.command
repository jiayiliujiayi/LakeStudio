#!/bin/bash
# Automatically navigate to the project directory
cd "$(dirname "$0")"

# First-time setup check: Create venv and install dependencies if missing
if [ ! -d ".venv" ]; then
    echo "⚡ Initializing LakeStudio environment for first-time setup..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
else
    source .venv/bin/activate
fi

# Launch Streamlit in the default browser
streamlit run app.py
