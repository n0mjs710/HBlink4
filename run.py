#!/usr/bin/env python3
"""
HBlink4 Runner Script
"""

import os
import sys
from hblink4.hblink import main

if __name__ == '__main__':
    # If no config file specified, use config/config.json relative to this script
    if len(sys.argv) < 2:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        default_config = os.path.join(script_dir, 'config', 'config.json')
        sys.argv.append(default_config)
    main()
