#!/usr/bin/env python3
"""
Bank Statement PDF Parser (2009 Format Version)

This script extracts transaction data from UFCU bank statements in the 2009 format
and outputs the data in CSV or TSV format.

Features:
- Handles the narrative-style format with equals sign notation
- Processes the DDMMMYY date format (e.g., 01APR09)
- Extracts transaction trace numbers
- Properly handles account sections with "suffix" notation
- Maintains source tracking for transactions
- Extracts all transaction details including dates, descriptions, amounts, balances
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
    Extract transaction data from a 2009 format bank statement PDF.
    
    Args:
        pdf_path (str): Path to the PDF file
        
    Returns:
        DataFrame: Pandas DataFrame with all transactions
    """
    print(f"Processing: {pdf_path}")
    all_transactions = []
    
    # Define month map at the beginning of the function
    month_map = {
        'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 
        'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08', 
        'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
    }
    
    try:
        # Extract text from all pages of the PDF
        with pdfplumber.open(pdf_path) as pdf:
            # Initialize variables for statement metadata
            statement_period = None
            statement_year = None
            pdf_source_id = os.path.basename(pdf_path)
            
            # Extract all text from the PDF with improved handling
            full_text = ""
            for page in pdf.pages:
                page_text = page.extract_text(
                    x_tolerance=3,
                    y_tolerance=3,
                )
                full_text += page_text + "\n"
            
            # Skip past any header encoding information
            # This will ignore the "KBKJOLGPMFENANKP" type data at the top
            clean_text = re.sub(r'^[A-Z0-9\s]+?\d+\s+\d+-\d+\n', '', full_text)
            if clean_text:
                full_text = clean_text
            
            # Extract statement period - 2009 format uses "01APR09 30APR09" format
            period_pattern = r'(\d{2}[A-Z]{3}\d{2})\s+(\d{2}[A-Z]{3}\d{2})'
            period_match = re.search(period_pattern, full_text)
            
            if period_match:
                start_date = period_match.group(1)
                end_date = period_match.group(2)
                statement_period = f"{start_date} - {end_date}"
                
                # Extract year from statement period
                year_match = re.search(r'(\d{2})$', end_date)
                if year_match:
                    short_year = year_match.group(1)
                    statement_year = f"20{short_year}" if int(short_year) < 50 else f"19{short_year}"
                    print(f"Detected statement year: {statement_year}")
            
            # Define account section patterns more precisely
            account_section_patterns = [
                # Regular Savings pattern
                (r'Regular\s+Your balance at the beginning of the period.*?Savings\s+Suffix\s+(\d+)', 'Regular Savings'),
                
                # LOC/Loan pattern
                (r'LOC\s+Your balance at the beginning of the period.*?Loan\s+(\d+)', 'LOC'),
                
                # Free Checking pattern - specific for statement number 900165187
                (r'Free\s+No\.\s+(\d+)\.\s+Balance at the beginning of the period', 'Free Checking'),
                
                # Simplified Free Checking pattern
                (r'Free\s+Checking\s+Suffix\s+(\d+)', 'Free Checking'),
                
                # More direct pattern for suffix
                (r'Suffix\s+(\d+)', 'Account')  # Generic fallback
            ]
            
            # Find all account sections
            account_sections = []
            
            for pattern, account_type in account_section_patterns:
                for match in re.finditer(pattern, full_text):
                    # Extract account number
                    if '(' in pattern:
                        # If there's a capturing group for the account number
                        account_number = match.group(1)
                    else:
                        # Default account number if not found
                        account_number = "0000"
                    
                    # Set account type based on the pattern or the match
                    if account_type is None:
                        # Extract account type from match
                        account_type = match.group(1)
                    
                    # Record the section position
                    account_sections.append({
                        "type": account_type,
                        "number": account_number,
                        "start": match.start(),
                        "match_text": full_text[match.start():match.start() + 100]  # For debugging
                    })
            
            # Sort account sections by their position in the text
            account_sections.sort(key=lambda x: x["start"])
            
            # Filter out duplicates and sections with invalid types
            filtered_sections = []
            known_sections = set()
            
            for section in account_sections:
                # Create a unique key for this section
                section_key = f"{section['type']}_{section['number']}"
                
                # Skip if we've already seen this section or if type is invalid
                if section_key in known_sections or section['type'] in ["On", "University"]:
                    continue
                
                # Add to filtered list and mark as seen
                filtered_sections.append(section)
                known_sections.add(section_key)
            
            account_sections = filtered_sections
            
            # Print found account sections for debugging
            print(f"Found {len(account_sections)} account sections:")
            for section in account_sections:
                print(f"  - {section['type']} ({section['number']}) at position {section['start']}")
            
            # Direct transaction pattern matching for the whole document
            # This will capture transactions regardless of account section
            
            # 1. Pattern for Regular Savings transactions
            savings_pattern = r'(\d{2}[A-Z]{3})\s+(Deposit-Payroll-\d+|Withdrawal)\s+([\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(savings_pattern, full_text):
                trans_date = match.group(1)
                trans_type = match.group(2)
                amount_str = match.group(3)
                balance_str = match.group(4)
                
                # Look for description in surrounding text
                context_after = full_text[match.end():match.end() + 200]
                description = ""
                
                # For Payroll deposits, look for University of Texas Payroll
                if "Payroll" in trans_type:
                    payroll_desc = re.search(r'University of Texas Payroll', context_after)
                    if payroll_desc:
                        description = "University of Texas Payroll"
                
                # For withdrawals, look for On Demand Internet Transaction
                elif trans_type == "Withdrawal":
                    trace_pattern = r'On Demand Internet Transaction\s+Trace\s+#(\w+)'
                    transfer_pattern = r'Transfer\s+"([^"]+)"\s+([\d.]+)\s+(?:to|from)\s+share\s+(\w+)'
                    
                    trace_match = re.search(trace_pattern, context_after)
                    if trace_match:
                        trace_number = trace_match.group(1)
                        transfer_match = re.search(transfer_pattern, context_after)
                        
                        if transfer_match:
                            transfer_type = transfer_match.group(1)
                            transfer_amount = transfer_match.group(2)
                            transfer_account = transfer_match.group(3)
                            description = f"Transfer {transfer_type} {transfer_amount} to share {transfer_account} (Trace: {trace_number})"
                        else:
                            description = f"On Demand Internet Transaction (Trace: {trace_number})"
                
                # Convert date format from DDMMM to YYYY-MM-DD
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                month = month_map.get(month_abbr.upper(), '01')
                complete_date = f"{statement_year}-{month}-{day}"
                
                # Determine account type - most likely Regular Savings
                account_type = "Regular Savings"
                account_number = "0"  # Default for Regular Savings
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    if trans_type == "Withdrawal":
                        amount = -amount  # Make withdrawal amounts negative
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': trans_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
            
            # 2. Pattern for LOC transactions
            loc_pattern = r'(\d{2}[A-Z]{3})\s+(Payment|Principal advance)\s+\(([\d.]+)\)\s+([\d.]+)\s+([\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(loc_pattern, full_text):
                trans_date = match.group(1)
                trans_type = match.group(2)
                payment_amount = float(match.group(3))
                interest = float(match.group(4))
                principal = float(match.group(5))
                balance = float(match.group(6))
                
                # Look for description
                context_after = full_text[match.end():match.end() + 200]
                description = ""
                
                trace_pattern = r'On Demand Internet Transaction\s+Trace\s+#(\w+)'
                transfer_pattern = r'Transfer\s+"([^"]+)"\s+([\d.]+)\s+(?:from|to)\s+share\s+(\w+)'
                
                trace_match = re.search(trace_pattern, context_after)
                if trace_match:
                    trace_number = trace_match.group(1)
                    transfer_match = re.search(transfer_pattern, context_after)
                    
                    if transfer_match:
                        transfer_type = transfer_match.group(1)
                        transfer_amount = transfer_match.group(2)
                        transfer_account = transfer_match.group(3)
                        description = f"Transfer {transfer_type} {transfer_amount} from share {transfer_account} (Trace: {trace_number})"
                    else:
                        description = f"On Demand Internet Transaction (Trace: {trace_number})"
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                month = month_map.get(month_abbr.upper(), '01')
                complete_date = f"{statement_year}-{month}-{day}"
                
                # For LOC accounts
                account_type = "LOC"
                account_number = "8"  # Default for LOC in this statement
                
                # Set amount based on transaction type
                amount = principal if trans_type == "Principal advance" else -principal
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': trans_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Interest': interest,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
            
            # 3. Pattern for Free Checking transactions - handles the majority of transactions
            checking_pattern = r'(\d{2}[A-Z]{3})\s+Withdrawal\s+([-+]?[\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(checking_pattern, full_text):
                trans_date = match.group(1)
                amount_str = match.group(2)
                balance_str = match.group(3)
                
                # Look for description in the text after this match
                context_before = full_text[max(0, match.start() - 100):match.start()]
                context_after = full_text[match.end():match.end() + 200]
                
                # Default description and type
                description = ""
                trans_type = "Withdrawal"
                
                # Look for description text and trace number
                desc_line_end = context_after.find('\n')
                if desc_line_end > 0:
                    desc_line = context_after[:desc_line_end].strip()
                else:
                    desc_line = context_after[:100].strip()
                
                trace_pattern = r'Trace\s+#(\w+)'
                trace_match = re.search(trace_pattern, desc_line)
                
                if trace_match:
                    trace_number = trace_match.group(1)
                    # Remove the trace part from description
                    description = re.sub(r'Trace\s+#\w+', '', desc_line).strip()
                    
                    if description:
                        description += f" (Trace: {trace_number})"
                    else:
                        description = f"Trace: {trace_number}"
                else:
                    description = desc_line
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                month = month_map.get(month_abbr.upper(), '01')
                complete_date = f"{statement_year}-{month}-{day}"
                
                # For Free Checking accounts
                account_type = "Free Checking"
                account_number = "8"  # Default for Free Checking
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    if not amount_str.startswith('+'):  # If it's not explicitly positive
                        amount = -amount  # Make withdrawal amounts negative
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': trans_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
            
            # 4. Pattern for deposits in Free Checking
            deposit_pattern = r'(\d{2}[A-Z]{3})\s+Deposit(?:-Payroll)?\s+([\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(deposit_pattern, full_text):
                trans_date = match.group(1)
                amount_str = match.group(2)
                balance_str = match.group(3)
                
                # Look for description
                context_after = full_text[match.end():match.end() + 200]
                
                # Default description and type
                description = ""
                trans_type = "Deposit"
                
                # Check if this is a payroll deposit
                payroll_match = re.search(r'University of Texas Payroll', context_after)
                if payroll_match:
                    description = "University of Texas Payroll"
                    trans_type = "Deposit-Payroll"
                else:
                    # Look for description text and trace number
                    desc_line_end = context_after.find('\n')
                    if desc_line_end > 0:
                        desc_line = context_after[:desc_line_end].strip()
                    else:
                        desc_line = context_after[:100].strip()
                    
                    trace_pattern = r'Trace\s+#(\w+)'
                    trace_match = re.search(trace_pattern, desc_line)
                    
                    if trace_match:
                        trace_number = trace_match.group(1)
                        # Remove the trace part from description
                        description = re.sub(r'Trace\s+#\w+', '', desc_line).strip()
                        
                        if description:
                            description += f" (Trace: {trace_number})"
                        else:
                            description = f"Trace: {trace_number}"
                    else:
                        description = desc_line
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                month = month_map.get(month_abbr.upper(), '01')
                complete_date = f"{statement_year}-{month}-{day}"
                
                # Determine account type based on context
                account_type = "Free Checking"
                account_number = "8"
                if "Savings" in context_before[-100:]:
                    account_type = "Regular Savings"
                    account_number = "0"
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': trans_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
        
        if all_transactions:
            # Sort transactions by date and account
            df = pd.DataFrame(all_transactions)
            df = df.sort_values(by=['Transaction Date', 'Account'])
            
            # Remove duplicate transactions (same date, account, amount, balance)
            df = df.drop_duplicates(subset=['Transaction Date', 'Account', 'Amount', 'Balance'])
            
            return df
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
    parser = argparse.ArgumentParser(description='Extract transactions from 2009 format bank statement PDFs')
    parser.add_argument('--input-dir', '-i', default='/data/input', 
                        help='Directory containing PDF files (default: /data/input)')
    parser.add_argument('--output-dir', '-o', default='/data/output',
                        help='Directory to save output files (default: /data/output)')
    parser.add_argument('--format', '-f', choices=['csv', 'tsv'], default='csv',
                        help='Output format (default: csv)')
    parser.add_argument('--file', help='Process a single PDF file instead of a directory')
    parser.add_argument('--debug', action='store_true', help='Enable detailed debug logging')
    
    args = parser.parse_args()
    
    print(f"Bank Statement Parser (2009 Format) starting...")
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
                print(f"Extracted {len(df)} transactions")
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
    