#!/bin/bash

# Store the script's directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Function to deactivate venv on script termination
cleanup() {
    echo "Deactivating virtual environment..."
    deactivate 2>/dev/null || true
}

# Set up trap to call cleanup on script exit
trap cleanup EXIT

# Activate virtual environment
echo "Activating virtual environment..."
source "$SCRIPT_DIR/venv/bin/activate"

# Run HBlink4
echo "Starting HBlink4..."
"$SCRIPT_DIR/run.py" "$@"
