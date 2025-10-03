#!/usr/bin/env python3
"""
Filter user.csv to only include US and Canada entries.

Usage:
    python3 scripts/filter_user_csv.py <input.csv> [output.csv]
    
Example:
    python3 scripts/filter_user_csv.py user_full.csv user.csv
    
If no output file specified, will overwrite the input file.
"""

import csv
import sys
import os
from pathlib import Path


def filter_user_csv(input_path: str, output_path: str = None) -> None:
    """
    Filter user.csv to only keep US and Canada entries.
    
    Args:
        input_path: Path to input CSV file
        output_path: Path to output CSV file (defaults to overwriting input)
    """
    if output_path is None:
        output_path = input_path
        temp_path = input_path + '.tmp'
    else:
        temp_path = output_path
    
    input_file = Path(input_path)
    if not input_file.exists():
        print(f"Error: Input file not found: {input_path}")
        sys.exit(1)
    
    # Track statistics
    kept = 0
    skipped = 0
    countries_kept = {}
    
    print(f"Processing {input_path}...")
    
    with open(input_path, 'r', encoding='utf-8') as infile, \
         open(temp_path, 'w', encoding='utf-8', newline='') as outfile:
        
        reader = csv.DictReader(infile)
        
        # Verify required columns exist
        if 'COUNTRY' not in reader.fieldnames:
            print("Error: COUNTRY column not found in CSV")
            sys.exit(1)
        
        writer = csv.DictWriter(outfile, fieldnames=reader.fieldnames)
        writer.writeheader()
        
        for row in reader:
            country = row.get('COUNTRY', '').strip()
            
            # Keep only US and Canada
            if country in ('United States', 'Canada'):
                writer.writerow(row)
                kept += 1
                countries_kept[country] = countries_kept.get(country, 0) + 1
            else:
                skipped += 1
    
    # If we wrote to a temp file, move it to the final location
    if output_path == input_path:
        os.replace(temp_path, output_path)
    
    # Show statistics
    total = kept + skipped
    print(f"\n✅ Filtering complete!")
    print(f"   Kept: {kept:,} entries ({kept/total*100:.1f}%)")
    for country, count in sorted(countries_kept.items()):
        print(f"     - {country}: {count:,}")
    print(f"   Skipped: {skipped:,} entries ({skipped/total*100:.1f}%)")
    
    # Show file sizes
    output_file = Path(output_path)
    if input_path != output_path and Path(input_path).exists():
        input_size = Path(input_path).stat().st_size / 1024 / 1024
        output_size = output_file.stat().st_size / 1024 / 1024
        print(f"\n📊 File sizes:")
        print(f"   Input:  {input_size:.2f} MB")
        print(f"   Output: {output_size:.2f} MB")
        print(f"   Saved:  {input_size - output_size:.2f} MB ({(input_size - output_size)/input_size*100:.1f}%)")
    else:
        output_size = output_file.stat().st_size / 1024 / 1024
        print(f"\n📊 Output file size: {output_size:.2f} MB")
    
    print(f"\n✨ Filtered CSV written to: {output_path}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else input_path
    
    filter_user_csv(input_path, output_path)


if __name__ == '__main__':
    main()
