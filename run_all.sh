#!/bin/bash
# Start both HBlink4 server and dashboard
# Usage: ./run_all.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}Virtual environment not found. Please run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt${NC}"
    exit 1
fi

# Activate virtual environment
echo -e "${BLUE}Activating virtual environment...${NC}"
source venv/bin/activate

# Check if dashboard requirements are installed
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}Dashboard dependencies not found. Installing...${NC}"
    pip install -r requirements-dashboard.txt
fi

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Stopping services...${NC}"
    kill $(jobs -p) 2>/dev/null || true
    wait 2>/dev/null || true
    echo -e "${GREEN}All services stopped${NC}"
}

trap cleanup EXIT INT TERM

echo -e "${GREEN}Starting HBlink4 services...${NC}"
echo

# Start dashboard in background
echo -e "${BLUE}Starting Dashboard on http://0.0.0.0:8080${NC}"
python3 run_dashboard.py 0.0.0.0 8080 &
DASHBOARD_PID=$!

# Give dashboard time to start
sleep 2

# Start HBlink4 server
echo -e "${BLUE}Starting HBlink4 server...${NC}"
python3 run.py &
HBLINK_PID=$!

echo
echo -e "${GREEN}Services started:${NC}"
echo -e "  Dashboard: http://localhost:8080 (PID: $DASHBOARD_PID)"
echo -e "  HBlink4:   UDP port 54000 (PID: $HBLINK_PID)"
echo
echo -e "${YELLOW}Press CTRL+C to stop all services${NC}"
echo

# Wait for processes
wait $HBLINK_PID $DASHBOARD_PID
