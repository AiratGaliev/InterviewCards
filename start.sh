#!/bin/bash

if [ ! -d ".venv" ]; then
    echo "Creating .venv directory..."
    python3 -m venv .venv
    source .venv/bin/activate
    echo "Installing dependencies..."
    pip install --upgrade pip setuptools wheel
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo "Error happens. Deleting .venv directory..."
        rm -rf .venv
        exit 1
    fi
else
    source .venv/bin/activate
fi
streamlit run start.py
deactivate