#!/usr/bin/env python3
"""
Bank Statement PDF Parser (Fixed Version)

This script extracts transaction data from bank statement PDFs (UFCU format)
and outputs the data in CSV or TSV format.

Fixed: Properly separates transactions by source PDF file when creating individual output files.
"""

import os
import re
import argparse
import glob
from datetime import datetime
import hashlib

import pandas as pd
import pdfplumber


def extract_transactions_from_pdf(pdf_path):
    """
    Extract transaction data from a bank statement PDF.
    
    Args:
        pdf_path (str): Path to the PDF file
        
    Returns:
        DataFrame: Pandas DataFrame with all transactions
    """
    print(f"Processing: {pdf_path}")
    all_transactions = []
    
    try:
        # Extract text from all pages of the PDF
        with pdfplumber.open(pdf_path) as pdf:
            # Extract statement period from first page
            statement_period = None
            statement_year = None
            pdf_source_id = os.path.basename(pdf_path)
            
            if len(pdf.pages) > 0:
                first_page_text = pdf.pages[0].extract_text()
                
                # Try different patterns for statement period with year extraction
                period_patterns = [
                    r'(\d{2}/\d{2}/\d{2})\s*-\s*(\d{2}/\d{2}/\d{2})',  # MM/DD/YY - MM/DD/YY
                    r'(\d{2}/\d{2}/\d{2})-(\d{2}/\d{2}/\d{2})',         # MM/DD/YY-MM/DD/YY
                    r'(\d{2}/\d{2}/\d{2})\s+(?:to|through)\s+(\d{2}/\d{2}/\d{2})', # Other formats
                ]
                
                for pattern in period_patterns:
                    period_match = re.search(pattern, first_page_text)
                    if period_match:
                        start_date = period_match.group(1)
                        end_date = period_match.group(2)
                        statement_period = f"{start_date} - {end_date}"
                        
                        # Extract year from statement period for date completion
                        year_match = re.search(r'/(\d{2})$', end_date)
                        if year_match:
                            short_year = year_match.group(1)
                            statement_year = f"20{short_year}" if int(short_year) < 50 else f"19{short_year}"
                            print(f"Detected statement year: {statement_year}")
                        break
            
            # Initialize tables for spatial extraction
            tables_by_page = []
            
            # Extract tables using pdfplumber's table extraction capabilities
            for page_num, page in enumerate(pdf.pages):
                # Try to extract tables based on layout
                try:
                    # Extract tables with explicit settings for better accuracy
                    tables = page.extract_tables(
                        table_settings={
                            "vertical_strategy": "text",
                            "horizontal_strategy": "text",
                            "snap_tolerance": 3,
                            "join_tolerance": 3,
                            "edge_min_length": 3,
                            "min_words_vertical": 3,
                            "min_words_horizontal": 1,
                        }
                    )
                    
                    if tables:
                        for table in tables:
                            if table and len(table) > 1:  # Ensure table has header and data
                                tables_by_page.append(table)
                except Exception as table_err:
                    print(f"Table extraction failed on page {page_num+1}: {str(table_err)}")
            
            # Extract all text from the PDF with improved handling
            full_text = ""
            for page in pdf.pages:
                # Extract text with custom settings for better accuracy
                page_text = page.extract_text(
                    x_tolerance=3,  # Adjust tolerance for horizontal text grouping
                    y_tolerance=3,  # Adjust tolerance for vertical text grouping
                )
                full_text += page_text + "\n"
            
            # Identify account sections with improved pattern
            account_pattern = r'([A-Z][A-Z\s\-]+)\s+\((\d{4})\)'
            account_matches = list(re.finditer(account_pattern, full_text))
            
            # Process each account section
            for i, match in enumerate(account_matches):
                account_name = match.group(1).strip()
                account_number = match.group(2)
                start_pos = match.end()
                
                # Find the end of this account section (start of next account or end of text)
                end_pos = len(full_text)
                if i < len(account_matches) - 1:
                    end_pos = account_matches[i+1].start()
                
                section_text = full_text[start_pos:end_pos]
                
                # Find the transaction table with improved pattern
                header_pattern = r'TRANS\s+EFFECTIVE\s+TYPE\s+DESCRIPTION\s+AMOUNT\s+BALANCE'
                header_match = re.search(header_pattern, section_text)
                if not header_match:
                    # Try alternative header pattern
                    header_pattern = r'DATE\s+DATE\s+.*DESCRIPTION.*AMOUNT.*BALANCE'
                    header_match = re.search(header_pattern, section_text)
                    if not header_match:
                        continue
                
                # Find "DATE DATE" line that follows the header (if present)
                date_line_pattern = r'DATE\s+DATE'
                date_line_match = re.search(date_line_pattern, section_text[header_match.end():])
                if date_line_match:
                    # Start processing after the DATE DATE line
                    table_start = header_match.end() + date_line_match.end()
                else:
                    # If no DATE DATE line, start right after header
                    table_start = header_match.end()
                
                # Find end of transaction table with improved pattern
                table_end_pattern = r'(Ending Balance|Dividends Paid|Available Balance|Total Deposits|Total Withdrawals)'
                table_end_match = re.search(table_end_pattern, section_text[table_start:])
                
                # Extract transaction table text
                if table_end_match:
                    table_text = section_text[table_start:table_start + table_end_match.start()]
                else:
                    table_text = section_text[table_start:]
                
                # Split into lines and process each transaction
                lines = table_text.split('\n')
                
                # Initialize transaction tracking for multi-line descriptions
                current_transaction = None
                prev_balance = None
                
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    
                    # Improved transaction pattern with better formatting handling
                    # FORMAT: MM/DD MM/DD Type Description Amount Balance
                    trans_pattern = r'(\d{2}/\d{2})\s+(\d{2}/\d{2})?\s+([\w\s\-]+?)\s+(.*?)\s+([-+]?\$?\d{1,3}(?:,\d{3})*\.\d{2})\s+(\d{1,3}(?:,\d{3})*\.\d{2})$'
                    alt_pattern = r'(\d{2}/\d{2})(?:\s+(\d{2}/\d{2}))?\s+([\w\s\-]+?)\s+(.*?)\s+([-+]?\$?[\d.,]+)\s+([\d.,]+)$'
                    
                    trans_match = re.search(trans_pattern, line)
                    if not trans_match:
                        trans_match = re.search(alt_pattern, line)
                        
                    if trans_match:
                        # If we have a previous transaction, finalize and add it
                        if current_transaction:
                            all_transactions.append(current_transaction)
                        
                        # Extract transaction components
                        trans_date = trans_match.group(1)
                        effect_date = trans_match.group(2) or trans_date
                        trans_type = trans_match.group(3).strip()
                        description = trans_match.group(4).strip()
                        amount_str = trans_match.group(5).replace('$', '')
                        balance_str = trans_match.group(6)
                        
                        # Parse numeric values
                        try:
                            amount = float(amount_str.replace(',', ''))
                            balance = float(balance_str.replace(',', ''))
                            
                            # Validate balance if previous transaction exists
                            if prev_balance is not None:
                                expected_balance = round(prev_balance + amount, 2)
                                if abs(expected_balance - balance) > 0.01:  # Allow for small rounding errors
                                    print(f"⚠️ Balance validation failed: Previous: {prev_balance}, Amount: {amount}, Expected: {expected_balance}, Actual: {balance}")
                            
                            # Store current balance for next validation
                            prev_balance = balance
                            
                            # Complete dates with year
                            complete_trans_date = trans_date
                            complete_effect_date = effect_date
                            
                            if statement_year:
                                # Convert MM/DD to YYYY-MM-DD format
                                trans_month, trans_day = trans_date.split('/')
                                effect_parts = effect_date.split('/')
                                if len(effect_parts) == 2:
                                    effect_month, effect_day = effect_parts
                                    complete_effect_date = f"{statement_year}-{effect_month}-{effect_day}"
                                
                                complete_trans_date = f"{statement_year}-{trans_month}-{trans_day}"
                            
                            # Create transaction record
                            current_transaction = {
                                'Account': f"{account_name} ({account_number})",
                                'Transaction Date': complete_trans_date,
                                'Effective Date': complete_effect_date,
                                'Type': trans_type,
                                'Description': description,
                                'Amount': amount,
                                'Balance': balance,
                                'Statement Period': statement_period,
                                'Source_PDF': pdf_source_id,  # Add source PDF filename for tracking
                            }
                        except ValueError as e:
                            print(f"Error parsing transaction values: {str(e)}")
                            current_transaction = None
                    elif current_transaction:
                        # Enhanced description handling for continuation lines
                        # Check if this is a description continuation line 
                        # (no date at start and doesn't match transaction pattern)
                        if not re.match(r'^\d{2}/\d{2}', line):
                            # This is likely a continuation of the previous description
                            # Clean up line to remove extra spaces
                            line = re.sub(r'\s+', ' ', line).strip()
                            
                            # If this line adds meaningful information to the description
                            if len(line) > 2:  # Avoid adding very short or empty lines
                                if len(line) > 3 and line.upper() == line:
                                    # This might be a new part of the description (like merchant info)
                                    current_transaction['Description'] += f" | {line}"
                                else:
                                    # Regular continuation
                                    current_transaction['Description'] += f" {line}"
                
                # Don't forget to add the last transaction
                if current_transaction:
                    all_transactions.append(current_transaction)
            
            # If we have table extraction data but no text-based extractions, try to use the tables
            if not all_transactions and tables_by_page:
                # Process extracted tables
                for table in tables_by_page:
                    if len(table) < 2:  # Skip tables without enough rows
                        continue
                    
                    # Try to identify if this is a transaction table
                    header_row = table[0]
                    header_text = " ".join(str(cell) for cell in header_row if cell)
                    
                    # Check if header matches transaction table pattern
                    if not (re.search(r'DATE|TRANS|EFFECTIVE', header_text) and 
                            re.search(r'AMOUNT|BALANCE', header_text)):
                        continue
                    
                    # Identify column positions
                    date_col = None
                    eff_date_col = None
                    type_col = None
                    desc_col = None
                    amount_col = None
                    balance_col = None
                    
                    for i, cell in enumerate(header_row):
                        if not cell:
                            continue
                        cell = str(cell).strip().upper()
                        if 'TRANS' in cell and 'DATE' in cell:
                            date_col = i
                        elif 'EFFECTIVE' in cell and 'DATE' in cell:
                            eff_date_col = i
                        elif cell == 'TYPE':
                            type_col = i
                        elif 'DESCRIPTION' in cell:
                            desc_col = i
                        elif cell == 'AMOUNT':
                            amount_col = i
                        elif cell == 'BALANCE':
                            balance_col = i
                    
                    # Skip if we couldn't identify essential columns
                    if date_col is None or amount_col is None or balance_col is None:
                        continue
                    
                    # Extract account info from nearby text
                    account_name = "Unknown"
                    account_number = "0000"
                    
                    # Process transaction rows
                    prev_balance = None
                    for row_idx in range(1, len(table)):
                        row = table[row_idx]
                        
                        # Skip empty rows
                        if not any(row):
                            continue
                            
                        # Skip rows that don't have a date
                        if date_col >= len(row) or not row[date_col]:
                            continue
                            
                        trans_date = str(row[date_col]).strip()
                        if not re.match(r'\d{2}/\d{2}', trans_date):
                            continue
                        
                        # Extract transaction data
                        effect_date = row[eff_date_col] if eff_date_col is not None and eff_date_col < len(row) else trans_date
                        effect_date = str(effect_date).strip() if effect_date else trans_date
                        
                        trans_type = row[type_col] if type_col is not None and type_col < len(row) else ""
                        trans_type = str(trans_type).strip() if trans_type else ""
                        
                        description = row[desc_col] if desc_col is not None and desc_col < len(row) else ""
                        description = str(description).strip() if description else ""
                        
                        # Handle amount and balance
                        if amount_col < len(row) and balance_col < len(row):
                            amount_str = str(row[amount_col]).strip()
                            balance_str = str(row[balance_col]).strip()
                            
                            # Clean and convert to float
                            try:
                                amount = float(amount_str.replace('$', '').replace(',', ''))
                                balance = float(balance_str.replace('$', '').replace(',', ''))
                                
                                # Validate balance if previous transaction exists
                                if prev_balance is not None:
                                    expected_balance = round(prev_balance + amount, 2)
                                    if abs(expected_balance - balance) > 0.01:  # Allow for small rounding errors
                                        print(f"⚠️ Balance validation failed: Previous: {prev_balance}, Amount: {amount}, Expected: {expected_balance}, Actual: {balance}")
                                
                                # Store current balance for next validation
                                prev_balance = balance
                                
                                # Complete dates with year
                                complete_trans_date = trans_date
                                complete_effect_date = effect_date
                                
                                if statement_year:
                                    # Convert MM/DD to YYYY-MM-DD format
                                    trans_month, trans_day = trans_date.split('/')
                                    
                                    # Handle effect date which might be in different formats
                                    effect_parts = effect_date.split('/')
                                    if len(effect_parts) == 2:
                                        effect_month, effect_day = effect_parts
                                        complete_effect_date = f"{statement_year}-{effect_month}-{effect_day}"
                                    
                                    complete_trans_date = f"{statement_year}-{trans_month}-{trans_day}"
                                
                                # Create transaction record
                                transaction = {
                                    'Account': f"{account_name} ({account_number})",
                                    'Transaction Date': complete_trans_date,
                                    'Effective Date': complete_effect_date,
                                    'Type': trans_type,
                                    'Description': description,
                                    'Amount': amount,
                                    'Balance': balance,
                                    'Statement Period': statement_period,
                                    'Source_PDF': pdf_source_id,  # Add source PDF filename for tracking
                                }
                                all_transactions.append(transaction)
                            except ValueError:
                                print(f"Failed to convert amount or balance: {amount_str}, {balance_str}")
        
        if all_transactions:
            return pd.DataFrame(all_transactions)
        else:
            print(f"No transactions found in {pdf_path}")
            return None
            
    except Exception as e:
        print(f"Error processing {pdf_path}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def process_directory(input_dir, output_dir, output_format='csv'):
    """
    Process all PDF files in a directory and save results.
    
    Args:
        input_dir (str): Directory containing PDF files
        output_dir (str): Directory to save output files
        output_format (str): Output format ('csv' or 'tsv')
    """
    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    
    # Get all PDF files in the input directory
    pdf_files = glob.glob(os.path.join(input_dir, "*.pdf"))
    
    if not pdf_files:
        print(f"No PDF files found in {input_dir}")
        return
    
    print(f"Found {len(pdf_files)} PDF files to process")
    
    # Process each PDF file and store dataframes with their source file info
    all_dataframes = []
    pdf_dataframes = {}  # Dictionary to store dataframes by source PDF
    
    for pdf_file in pdf_files:
        df = extract_transactions_from_pdf(pdf_file)
        if df is not None and not df.empty:
            all_dataframes.append(df)
            
            # Store this dataframe with its source PDF
            base_name = os.path.splitext(os.path.basename(pdf_file))[0]
            pdf_dataframes[base_name] = df
    
    # Combine all transactions into a single DataFrame for the combined output
    if all_dataframes:
        combined_df = pd.concat(all_dataframes, ignore_index=True)
        
        # Add a timestamp column
        combined_df['Extracted_On'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Save combined results
        if output_format.lower() == 'tsv':
            output_path = os.path.join(output_dir, "transactions.tsv")
            combined_df.to_csv(output_path, sep='\t', index=False)
            print(f"Saved combined transactions to {output_path}")
        else:
            output_path = os.path.join(output_dir, "transactions.csv")
            combined_df.to_csv(output_path, index=False)
            print(f"Saved combined transactions to {output_path}")
            
        # Now save individual files properly, one per source PDF
        for pdf_file in pdf_files:
            base_name = os.path.splitext(os.path.basename(pdf_file))[0]
            
            if base_name in pdf_dataframes:
                # Get only the transactions from this specific PDF
                pdf_df = pdf_dataframes[base_name]
                
                # Add timestamp
                pdf_df['Extracted_On'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # Remove the Source_PDF column as it's redundant in individual files
                if 'Source_PDF' in pdf_df.columns:
                    pdf_df = pdf_df.drop(columns=['Source_PDF'])
                
                # Save individual file
                if output_format.lower() == 'tsv':
                    output_path = os.path.join(output_dir, f"{base_name}.tsv")
                    pdf_df.to_csv(output_path, sep='\t', index=False)
                else:
                    output_path = os.path.join(output_dir, f"{base_name}.csv")
                    pdf_df.to_csv(output_path, index=False)
                
                print(f"Saved transactions for {base_name} to {output_path}")
    else:
        print("No transactions extracted from any PDF files")


def main():
    parser = argparse.ArgumentParser(description='Extract transactions from bank statement PDFs')
    parser.add_argument('--input-dir', '-i', default='/data/input', 
                        help='Directory containing PDF files (default: /data/input)')
    parser.add_argument('--output-dir', '-o', default='/data/output',
                        help='Directory to save output files (default: /data/output)')
    parser.add_argument('--format', '-f', choices=['csv', 'tsv'], default='csv',
                        help='Output format (default: csv)')
    parser.add_argument('--file', help='Process a single PDF file instead of a directory')
    
    args = parser.parse_args()
    
    print(f"Bank Statement Parser starting...")
    print(f"Input directory: {args.input_dir}")
    print(f"Output directory: {args.output_dir}")
    print(f"Output format: {args.format}")
    
    # Process a single file or an entire directory
    if args.file:
        if os.path.isfile(args.file):
            df = extract_transactions_from_pdf(args.file)
            if df is not None and not df.empty:
                # Save to the specified output directory
                base_name = os.path.splitext(os.path.basename(args.file))[0]
                os.makedirs(args.output_dir, exist_ok=True)
                
                # Add timestamp
                df['Extracted_On'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                
                # Remove the Source_PDF column as it's redundant in individual files
                if 'Source_PDF' in df.columns:
                    df = df.drop(columns=['Source_PDF'])
                
                if args.format.lower() == 'tsv':
                    output_path = os.path.join(args.output_dir, f"{base_name}.tsv")
                    df.to_csv(output_path, sep='\t', index=False)
                else:
                    output_path = os.path.join(args.output_dir, f"{base_name}.csv")
                    df.to_csv(output_path, index=False)
                    
                print(f"Saved transactions to {output_path}")
            else:
                print(f"No transactions extracted from {args.file}")
        else:
            print(f"File not found: {args.file}")
    else:
        # Process all PDFs in the input directory
        process_directory(args.input_dir, args.output_dir, args.format)
    
    print("Processing complete!")


if __name__ == "__main__":
    main()
    