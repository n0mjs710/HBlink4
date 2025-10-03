# HBlink4 Scripts

## filter_user_csv.py

Filters the DMR user database to only include US and Canada entries, reducing file size and memory usage.

### Usage

#### Download latest user database:
```bash
# Download from radioid.net or other DMR database source
wget -O user_full.csv https://example.com/user.csv
```

#### Filter for US/Canada only:
```bash
# Process and save to user.csv
python3 scripts/filter_user_csv.py user_full.csv user.csv

# Or overwrite the original file:
python3 scripts/filter_user_csv.py user.csv
```

#### Output:
```
Processing user_full.csv...

✅ Filtering complete!
   Kept: 132,999 entries (45.6%)
     - Canada: 7,047
     - United States: 125,952
   Skipped: 158,969 entries (54.4%)

📊 File sizes:
   Input:  14.79 MB
   Output: 7.21 MB
   Saved:  7.57 MB (51.2%)

✨ Filtered CSV written to: user.csv
```

### Why Filter?

- **Memory efficiency**: 54% fewer entries to load
- **Disk space**: 51% smaller file size
- **Target audience**: Primary users are US/Canada
- **Performance**: Faster startup time for dashboard

### Adding More Countries

To include additional countries, edit `filter_user_csv.py` line 52:

```python
if country in ('United States', 'Canada', 'United Kingdom'):  # Add more here
```

### Automation

To automatically update and filter the user database:

```bash
#!/bin/bash
# update_user_database.sh

echo "Downloading latest user database..."
wget -O user_full.csv https://example.com/user.csv

echo "Filtering for US/Canada..."
python3 scripts/filter_user_csv.py user_full.csv user.csv

echo "Cleaning up..."
rm user_full.csv

echo "Done! Restart dashboard to load new database."
```
