#!/usr/bin/env python3
"""
Dashboard launcher for HBlink4
Starts the FastAPI web dashboard server
"""

import sys
import uvicorn

if __name__ == '__main__':
    # Default host and port
    host = '0.0.0.0'  # Listen on all interfaces
    port = 8080
    
    # Allow override from command line
    if len(sys.argv) > 1:
        host = sys.argv[1]
    if len(sys.argv) > 2:
        port = int(sys.argv[2])
    
    print(f"Starting HBlink4 Dashboard on http://{host}:{port}")
    print("Press CTRL+C to stop")
    print()
    
    uvicorn.run(
        "dashboard.server:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False  # Disable access logging to reduce log clutter
    )
