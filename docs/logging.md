# HBlink4 Logging System

HBlink4 implements a comprehensive logging system with daily log rotation and retention control. This guide describes the logging configuration options and behavior.

## Overview

The logging system supports:
- Separate logging levels for console and file output
- Daily log rotation at midnight with date-based filenames (YYYY-MM-DD)
- Configurable log retention period
- Automatic cleanup of old log files

## Configuration

Logging is configured in the global section of the configuration file:

```json
{
    "global": {
        "logging": {
            "file": "logs/hblink.log",
            "console_level": "INFO",
            "file_level": "DEBUG",
            "retention_days": 30,
            "log_protocol": false,
            "log_dmr_data": false,
            "log_status_updates": true
        }
    }
}
```

## Configuration Options

| Option | Type | Description | Default |
|--------|------|-------------|---------|
| `file` | string | Path to log file | "logs/hblink.log" |
| `console_level` | string | Logging level for console output | "INFO" |
| `file_level` | string | Logging level for file output | "DEBUG" |
| `retention_days` | number | Days to retain log files | 30 |
| `log_protocol` | boolean | Log protocol messages | false |
| `log_dmr_data` | boolean | Log DMR data packets | false |
| `log_status_updates` | boolean | Log repeater status | true |

## Log Levels

The following log levels are available, in order of increasing severity:
- DEBUG: Detailed information for debugging
- INFO: General operational messages
- WARNING: Warning messages for potential issues
- ERROR: Error messages for serious problems

## Log Files

- Main log file: `logs/hblink.log`
- Rotated logs: `logs/hblink.log.YYYY-MM-DD`
- Log rotation occurs daily at midnight
- Old logs are automatically cleaned up based on retention_days

## Example Log Output

```
2024-01-20 10:15:23 - INFO - HBlink4 server is running on 0.0.0.0:62031 (UDP)
2024-01-20 10:15:30 - INFO - Repeater 312100 (WA0EDA-1) login request from 192.168.1.100:62031
2024-01-20 10:15:30 - DEBUG - Processing login for repeater ID 312100 from 192.168.1.100:62031
2024-01-20 10:15:31 - INFO - Repeater 312100 (WA0EDA-1) authenticated successfully
2024-01-20 10:15:31 - INFO - Repeater 312100 (WA0EDA-1) configured successfully
```

## Log Rotation

The log rotation system:
1. Creates a new log file at midnight
2. Renames the old log with the date suffix
3. Maintains logs for the configured retention period
4. Automatically cleans up logs older than retention_days on startup

## Best Practices

1. **Log Levels**
   - Use INFO for normal operation
   - Use DEBUG during troubleshooting
   - Adjust console and file levels independently

2. **Storage Management**
   - Set appropriate retention_days based on needs
   - Monitor disk space usage
   - Archive important logs before deletion

3. **Troubleshooting**
   - Enable log_protocol for protocol issues
   - Enable log_dmr_data for data issues
   - Check logs in chronological order

4. **Security**
   - Protect log files with appropriate permissions
   - Regularly review logs for security events
   - Archive security-relevant logs

## Implementation Details

The logging system uses Python's built-in `TimedRotatingFileHandler` with these settings:

```python
handler = TimedRotatingFileHandler(
    log_file,
    when='midnight',
    interval=1,
    backupCount=retention_days
)
```

This ensures:
- Reliable daily rotation
- Clean timestamp-based naming
- Automatic cleanup of old files
- Thread-safe operation
