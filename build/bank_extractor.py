#!/usr/bin/env python3
"""
Bank Statement PDF Parser Controller

This script coordinates the parsing of bank statement PDFs across different formats by:
1. Detecting which format subdirectories exist under the input directory
2. Calling the appropriate parser script for each format directory
3. Collecting and combining outputs

Usage:
  python bank_extractor.py --input-dir /app/data/input --output-dir /app/data/output --format tsv
"""

import os
import sys
import argparse
import subprocess
import glob
import pandas as pd
from datetime import datetime

# Define format directories and their corresponding parser scripts
FORMAT_PARSERS = {
    "2007_format": "bank_parser_2007.py",
    "2009_format": "bank_parser_2009.py",
    "2015_format": "bank_parser_2015_plus.py"
}

def check_format_directories(input_dir):
    """
    Check which format subdirectories exist and contain PDF files.
    
    Args:
        input_dir (str): Base input directory
        
    Returns:
        dict: Mapping of format directories to lists of PDF files
    """
    format_pdfs = {}
    
    # Check if the input directory exists
    if not os.path.isdir(input_dir):
        print(f"Error: Input directory '{input_dir}' does not exist")
        return format_pdfs
    
    # Check each format directory
    for format_dir in FORMAT_PARSERS.keys():
        format_path = os.path.join(input_dir, format_dir)
        
        # Skip if the format directory doesn't exist
        if not os.path.isdir(format_path):
            continue
        
        # Find PDF files in this format directory
        pdf_files = glob.glob(os.path.join(format_path, "*.pdf"))
        
        if pdf_files:
            format_pdfs[format_dir] = pdf_files
            print(f"Found {len(pdf_files)} PDF files in {format_dir}")
    
    return format_pdfs

def process_format_directory(format_dir, parser_script, input_dir, output_dir, output_format):
    """
    Process all PDFs in a specific format directory using the appropriate parser.
    
    Args:
        format_dir (str): Format directory name (e.g. "2007_format")
        parser_script (str): Name of the parser script to use
        input_dir (str): Base input directory
        output_dir (str): Base output directory
        output_format (str): Output format (csv or tsv)
        
    Returns:
        str: Path to the output file containing the combined transactions
    """
    format_input_dir = os.path.join(input_dir, format_dir)
    format_output_dir = os.path.join(output_dir, format_dir)
    
    # Create format-specific output directory
    os.makedirs(format_output_dir, exist_ok=True)
    
    print(f"\nProcessing {format_dir} using {parser_script}...")
    
    # Build the command to run the parser script
    cmd = [
        "python", parser_script,
        "--input-dir", format_input_dir,
        "--output-dir", format_output_dir,
        "--format", output_format
    ]
    
    # Run the parser script as a subprocess
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print(result.stdout)
        
        # Return the path to the combined output file
        return os.path.join(format_output_dir, f"transactions.{output_format}")
    except subprocess.CalledProcessError as e:
        print(f"Error processing {format_dir}:")
        print(e.stderr)
        return None

def combine_outputs(output_files, output_dir, output_format):
    """
    Combine the output files from each format into a unified file.
    
    Args:
        output_files (dict): Mapping of format names to output file paths
        output_dir (str): Base output directory
        output_format (str): Output format (csv or tsv)
    """
    all_dfs = []
    
    # Load each output file
    for format_name, file_path in output_files.items():
        if file_path and os.path.exists(file_path):
            try:
                # Read the file with the appropriate separator
                sep = '\t' if output_format == 'tsv' else ','
                df = pd.read_csv(file_path, sep=sep)
                
                # Add a column to indicate the format
                df['Format_Type'] = format_name.replace('_format', '')
                
                all_dfs.append(df)
                print(f"Loaded {len(df)} transactions from {format_name}")
            except Exception as e:
                print(f"Error loading {file_path}: {str(e)}")
    
    # If we have data to combine
    if all_dfs:
        # Combine all dataframes
        combined_df = pd.concat(all_dfs, ignore_index=True)
        
        # Add timestamp for when the combined file was created
        combined_df['Combined_On'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Ensure the output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Save the combined file
        output_path = os.path.join(output_dir, f"all_transactions.{output_format}")
        if output_format == 'tsv':
            combined_df.to_csv(output_path, sep='\t', index=False)
        else:
            combined_df.to_csv(output_path, index=False)
            
        print(f"\nSaved combined transactions to {output_path}")
        print(f"Total transactions: {len(combined_df)}")
    else:
        print("\nNo transactions were extracted from any format")

def main():
    parser = argparse.ArgumentParser(description='Bank Statement PDF Parser Controller')
    parser.add_argument('--input-dir', '-i', default='/app/data/input', 
                        help='Base input directory (default: /app/data/input)')
    parser.add_argument('--output-dir', '-o', default='/app/data/output',
                        help='Base output directory (default: /app/data/output)')
    parser.add_argument('--format', '-f', choices=['csv', 'tsv'], default='tsv',
                        help='Output format (default: tsv)')
    
    args = parser.parse_args()
    
    print("Bank Statement PDF Parser Controller")
    print(f"Input directory: {args.input_dir}")
    print(f"Output directory: {args.output_dir}")
    print(f"Output format: {args.format}")
    
    # Check which format directories exist and contain PDFs
    format_pdfs = check_format_directories(args.input_dir)
    
    if not format_pdfs:
        print("No PDF files found in any format directory")
        return
    
    # Process each format directory
    output_files = {}
    for format_dir, pdf_files in format_pdfs.items():
        parser_script = FORMAT_PARSERS.get(format_dir)
        if parser_script:
            output_file = process_format_directory(
                format_dir, parser_script, args.input_dir, args.output_dir, args.format
            )
            output_files[format_dir] = output_file
    
    # Combine all outputs into a unified file
    combine_outputs(output_files, args.output_dir, args.format)
    
    print("\nProcessing complete!")

if __name__ == "__main__":
    main()
