#!/usr/bin/env python3
"""
Bank Statement PDF Parser (2007 Format Version) - Enhanced

This script extracts transaction data from UFCU bank statements in the 2007 format
and outputs the data in CSV or TSV format.

Features:
- Handles the narrative-style format with equals sign notation
- Processes the DDMMM date format (e.g., 07MAR)
- Extracts transaction trace numbers
- Properly handles account sections with "suffix" notation
- Handles asterisks next to dates in transactions
- Processes XML-like ACH transaction data
- Supports eChecking account type
- Maintains source tracking for transactions
- Extracts all transaction details including dates, descriptions, amounts, balances
- Improved account context detection
- Enhanced transaction deduplication
- Better merchant description extraction
"""

import os
import re
import argparse
import glob
from datetime import datetime
import hashlib

import pandas as pd
import pdfplumber


def determine_account_section(full_text, match_position):
    """
    Determine the correct account section based on context with improved detection.
    
    Args:
        full_text (str): The full text of the PDF
        match_position (int): The position of the transaction match
        
    Returns:
        tuple: (account_type, account_number)
    """
    # Look at more text before the match for better context detection
    context_before = full_text[max(0, match_position - 1000):match_position]
    
    # First look for specific section headers that would decisively identify the account
    if "eChecking No. 900165187" in context_before[-500:]:
        return "eChecking", "8"
    elif "Regular Savings Suffix 0" in context_before[-500:] or "Regular Your balance" in context_before[-500:]:
        return "Regular Savings", "0"
    elif "LOC Loan 8" in context_before[-500:] or "LOC Your balance" in context_before[-500:]:
        return "LOC", "8"
    
    # Check for account headers more thoroughly
    account_headers = {
        "eChecking": ["eChecking No.", "eChecking Suffix 8", "Suffix 8"],
        "Regular Savings": ["Regular Savings", "Regular Your balance", "Suffix 0"],
        "LOC": ["LOC Loan", "LOC Your balance"]
    }
    
    last_found_account = None
    last_found_position = -1
    
    for account_type, header_patterns in account_headers.items():
        for pattern in header_patterns:
            matches = [m for m in re.finditer(pattern, context_before)]
            if matches:
                # Get the last (most recent) occurrence
                last_match = matches[-1]
                if last_match.start() > last_found_position:
                    last_found_position = last_match.start()
                    if account_type == "eChecking":
                        last_found_account = ("eChecking", "8")
                    elif account_type == "Regular Savings":
                        last_found_account = ("Regular Savings", "0")
                    elif account_type == "LOC":
                        last_found_account = ("LOC", "8")
    
    if last_found_account:
        return last_found_account
    
    # Look for patterns in nearby transactions to infer the account
    nearby_context = full_text[max(0, match_position - 2000):min(len(full_text), match_position + 200)]
    
    # Check for characteristic transaction patterns
    if "Transfer 'STD'" in nearby_context and "share 8" in nearby_context:
        return "eChecking", "8"
    elif "Transfer 'STD'" in nearby_context and "share 0" in nearby_context:
        return "Regular Savings", "0"
    elif "Payment" in nearby_context and "Late charge" in nearby_context:
        return "LOC", "8"
    elif "WENDY" in nearby_context or "JAVA CITY" in nearby_context or "ARAMARK" in nearby_context:
        # Most merchant transactions are from checking
        return "eChecking", "8"
    
    # If all else fails, use checking as default (most common for 2007 statements)
    return "eChecking", "8"


def categorize_transaction(description, amount, transaction_type=None):
    """
    Categorize a transaction based on its description and amount.
    
    Args:
        description (str): Transaction description
        amount (float): Transaction amount
        transaction_type (str, optional): Original transaction type if available
        
    Returns:
        str: Transaction category
    """
    description = description.lower() if description else ""
    
    if transaction_type and "dividend" in transaction_type.lower():
        return "Dividend"
    elif "dividend" in description:
        return "Dividend"
    elif "overdraft transfer" in description:
        return "Overdraft Transfer"
    elif "transfer" in description:
        return "Transfer"
    elif "check #" in description or ("check" in description and "#" in description):
        return "Check"
    elif re.search(r'#\d+', description) and amount < 0:
        return "Check"
    elif "fee" in description:
        return "Fee"
    elif "payment" in description:
        return "Payment"
    elif "principal advance" in description:
        return "Principal Advance"
    elif amount > 0:
        if "payroll" in description:
            return "Deposit-Payroll"
        return "Deposit"
    else:
        return "Withdrawal"


def deduplicate_transactions(transactions):
    """
    Remove duplicate transactions with enhanced logic.
    
    Args:
        transactions (list): List of transaction dictionaries
        
    Returns:
        list: Deduplicated transactions
    """
    # Group by date, amount and balance (key indicators of the same transaction)
    transaction_groups = {}
    
    for transaction in transactions:
        # Create unique key based on important transaction attributes
        date = transaction.get('Transaction Date', '')
        amount = str(transaction.get('Amount', ''))
        balance = str(transaction.get('Balance', ''))
        
        # Base key for grouping
        key = f"{date}_{amount}_{balance}"
        
        if key not in transaction_groups:
            transaction_groups[key] = []
        
        transaction_groups[key].append(transaction)
    
    # Process each group to select the best representative transaction
    deduplicated = []
    
    for key, group in transaction_groups.items():
        if len(group) == 1:
            # No duplicates, add the single transaction
            deduplicated.append(group[0])
        else:
            # Score transactions based on completeness and prefer non-Unknown accounts
            best_transaction = None
            best_score = -1
            
            for t in group:
                score = 0
                
                # Prioritize transactions with known accounts
                account = t.get('Account', '')
                if "Unknown" not in account:
                    score += 50  # High priority
                
                # Prioritize transactions with descriptions
                description = t.get('Description', '')
                if description:
                    score += len(description)  # Longer descriptions are better
                
                # Bonus for transactions with trace numbers
                if "Trace" in description:
                    score += 10
                
                # Bonus for transactions with check numbers
                if t.get('Check Number'):
                    score += 15
                
                # Prefer transactions with merchant names
                for merchant in ["WENDY", "ARAMARK", "HEB", "FREEBIRDS"]:
                    if merchant in description:
                        score += 5
                
                # Bonus for transactions with special fields
                if t.get('Interest') is not None:
                    score += 8
                if t.get('Late Charge') is not None:
                    score += 8
                if t.get('Has_Asterisk'):
                    score += 5
                
                if score > best_score:
                    best_score = score
                    best_transaction = t
            
            if best_transaction:
                deduplicated.append(best_transaction)
    
    # Further deduplicate by trace number
    trace_groups = {}
    for transaction in deduplicated:
        description = transaction.get('Description', '')
        trace_match = re.search(r'Trace\s+#(\w+)', description)
        if trace_match:
            trace_num = trace_match.group(1)
            
            # Skip very common trace numbers that might appear in different transactions
            if trace_num in ['00', '01', '1', '2', '3']:
                continue
                
            if trace_num not in trace_groups:
                trace_groups[trace_num] = []
            trace_groups[trace_num].append(transaction)
    
    # Process trace groups to eliminate duplicates with the same trace
    final_deduplicated = []
    processed_by_trace = set()
    
    for trace_num, trace_transactions in trace_groups.items():
        if len(trace_transactions) > 1:
            # Find the transaction with highest quality description and account info
            best = None
            best_score = -1
            
            for t in trace_transactions:
                score = 0
                
                # Prefer transactions with known accounts
                if "Unknown" not in t.get('Account', ''):
                    score += 50
                
                # Prefer longer descriptions
                score += len(t.get('Description', ''))
                
                if score > best_score:
                    best_score = score
                    best = t
            
            if best:
                final_deduplicated.append(best)
                for t in trace_transactions:
                    # Mark all transactions in this trace group as processed
                    processed_by_trace.add(id(t))
        else:
            # Only one transaction with this trace, add it
            final_deduplicated.append(trace_transactions[0])
            processed_by_trace.add(id(trace_transactions[0]))
    
    # Add any transactions not processed through trace grouping
    for t in deduplicated:
        if id(t) not in processed_by_trace:
            final_deduplicated.append(t)
    
    return final_deduplicated


def standardize_date(day, month_abbr, year, month_map):
    """
    Standardize date format to YYYY-MM-DD.
    
    Args:
        day (str): Day part of the date
        month_abbr (str): Month abbreviation
        year (str): Year
        month_map (dict): Month abbreviation to number mapping
        
    Returns:
        str: Standardized date
    """
    day = day.strip()
    month_abbr = month_abbr.strip().upper()
    month = month_map.get(month_abbr, '01')
    
    # Zero-pad day if needed
    day = day.zfill(2)
    
    complete_date = f"{year}-{month}-{day}"
    
    # Validate the date format
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', complete_date):
        # Fix any formatting issues
        parts = complete_date.split('-')
        if len(parts) == 3:
            year, month, day = parts
            # Ensure each part has the right length
            year = year.strip()
            month = month.strip().zfill(2)
            day = day.strip().zfill(2)
            complete_date = f"{year}-{month}-{day}"
    
    return complete_date


def extract_transactions_from_pdf(pdf_path):
    """
    Extract transaction data from a 2007 format bank statement PDF with improved account detection.
    
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
            
            # Extract statement period - 2007 format uses "01MAR07 31MAR07" format
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
            
            # Define account section patterns more precisely for 2007 format
            account_section_patterns = [
                # Regular Savings pattern
                (r'Regular\s+Your balance at the beginning of the period.*?Savings\s+Suffix\s+(\d+)', 'Regular Savings'),
                
                # LOC/Loan pattern
                (r'LOC\s+Your balance at the beginning of the period.*?Loan\s+(\d+)', 'LOC'),
                
                # eChecking pattern 
                (r'eChecking\s+No\.\s+(\d+)\.\s+Balance at the beginning of the period', 'eChecking'),
                
                # Simplified eChecking pattern
                (r'eChecking\s+Suffix\s+(\d+)', 'eChecking'),
                
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
            
            # 1. Enhanced Pattern for transactions with asterisks beside dates (typically eChecking)
            asterisk_pattern = r'(\d{2}[A-Z]{3})\*\s+Withdrawal\s+([A-Z0-9\s\-\'&.#]+?)\s+([-+]?[\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(asterisk_pattern, full_text):
                trans_date = match.group(1)
                desc_text = match.group(2).strip()
                amount_str = match.group(3)
                balance_str = match.group(4)
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
                # For asterisk transactions, we're confident these are eChecking
                account_type = "eChecking"
                account_number = "8"  # Default for eChecking
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    # Make withdrawal amounts negative
                    amount = -abs(amount)
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                # Determine transaction type
                transaction_type = categorize_transaction(desc_text, amount, "Withdrawal")
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': transaction_type,
                    'Description': desc_text,
                    'Amount': amount,
                    'Balance': balance,
                    'Has_Asterisk': True,  # Flag for asterisk transactions
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
            
            # 2. Enhanced Pattern for Regular Savings transactions
            savings_pattern = r'(\d{2}[A-Z]{3})\s+(Deposit-Payroll-\d+|Withdrawal|Withdrawal\s+Overdraft\s+transfer\s+to|Dividend through \d{2}[A-Z]{3}\d{4})\s+([-+]?[\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(savings_pattern, full_text):
                # Get surrounding context to properly identify account
                context_start = max(0, match.start() - 1000)
                context_before = full_text[context_start:match.start()]
                
                # Determine account more reliably
                if "Regular Your balance" in context_before[-300:] or "Regular Savings" in context_before[-300:]:
                    account_type = "Regular Savings"
                    account_number = "0"
                else:
                    # Use improved account detection function
                    account_type, account_number = determine_account_section(full_text, match.start())
                
                trans_date = match.group(1)
                trans_type = match.group(2)
                amount_str = match.group(3)
                balance_str = match.group(4)
                
                # Look for description in surrounding text
                context_after = full_text[match.end():match.end() + 300]
                description = ""
                
                # For Payroll deposits, look for University of Texas Payroll
                if "Payroll" in trans_type:
                    payroll_desc = re.search(r'University of Texas Payroll', context_after)
                    if payroll_desc:
                        description = "University of Texas Payroll"
                
                # For withdrawals, look for On Demand Internet Transaction or ATM location
                elif "Withdrawal" in trans_type:
                    # Check for overdraft transfer
                    if "Overdraft" in trans_type:
                        overdraft_match = re.search(r'Withdrawal\s+Overdraft\s+transfer\s+to\s+([A-Z\-\d]+)', trans_type)
                        if overdraft_match:
                            dest_account = overdraft_match.group(1)
                            description = f"Overdraft transfer to {dest_account}"
                            trans_type = "Overdraft Transfer"
                    else:
                        # Check for ATM withdrawal with location
                        atm_pattern = r'(\d+\s+\&\s+[A-Za-z]+)\s+([A-Za-z]+)\s+TX\s+US\s+Trace\s+#(\d+)'
                        atm_match = re.search(atm_pattern, context_after)
                        
                        if atm_match:
                            location = atm_match.group(1)
                            city = atm_match.group(2)
                            trace_number = atm_match.group(3)
                            description = f"ATM Withdrawal - {location}, {city} (Trace: {trace_number})"
                        else:
                            # Check for internet transaction
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
                                    # Look for transaction location
                                    location_pattern = r'(\d+\s*&\s*\w+|\w+\s*&\s*\w+)\s+([A-Za-z]+)\s+TX\s+US\s+Trace\s+#(\w+)'
                                    location_match = re.search(location_pattern, context_after)
                                    
                                    if location_match:
                                        loc = location_match.group(1)
                                        city = location_match.group(2)
                                        description = f"{loc}, {city} (Trace: {trace_number})"
                                    else:
                                        description = f"On Demand Internet Transaction (Trace: {trace_number})"
                
                # For dividends, extract Annual Percentage Yield information
                elif "Dividend" in trans_type:
                    apy_pattern = r'ANNUAL PERCENTAGE YIELD EARNED:\s+([\d.]+)\%\s+FOR\s+A\s+(\d+)\s+DAY\s+PERIOD'
                    avg_balance_pattern = r'Average Daily Balance:\s+([\d.]+)'
                    
                    apy_match = re.search(apy_pattern, context_after)
                    avg_balance_match = re.search(avg_balance_pattern, context_after)
                    
                    if apy_match and avg_balance_match:
                        apy = apy_match.group(1)
                        period_days = apy_match.group(2)
                        avg_balance = avg_balance_match.group(1)
                        description = f"Dividend at {apy}% APY for {period_days} days (Avg Balance: ${avg_balance})"
                    else:
                        description = "Dividend Payment"
                
                # Convert date format from DDMMM to YYYY-MM-DD
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    if "Withdrawal" in trans_type and amount > 0:
                        amount = -amount  # Make withdrawal amounts negative
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                # Determine transaction type
                transaction_type = categorize_transaction(description, amount, trans_type)
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': transaction_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
            
            # 3. Enhanced Pattern for LOC transactions
            loc_pattern = r'(\d{2}[A-Z]{3})\s+(Payment|Principal advance)\s+\(([\d.]+)\)\s+([\d.]+)\s+([\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(loc_pattern, full_text):
                # Check context for LOC section
                context_before = full_text[max(0, match.start() - 1000):match.start()]
                if "LOC Your balance" in context_before[-300:] or "LOC Loan 8" in context_before[-300:]:
                    account_type = "LOC"
                    account_number = "8"
                else:
                    account_type, account_number = determine_account_section(full_text, match.start())
                    if account_type != "LOC":
                        account_type = "LOC"  # Force LOC for this pattern
                
                trans_date = match.group(1)
                trans_type = match.group(2)
                payment_amount = float(match.group(3))
                interest = float(match.group(4))
                principal = float(match.group(5))
                balance = float(match.group(6))
                
                # Look for description
                context_after = full_text[match.end():match.end() + 300]
                description = ""
                
                # Check for late charge
                late_charge_pattern = r'\((\d+\.\d+)\s+Late\s+charge\)'
                late_charge_match = re.search(late_charge_pattern, context_after)
                late_charge = 0.0
                
                if late_charge_match:
                    late_charge = float(late_charge_match.group(1))
                    description += f"Late charge: ${late_charge} "
                
                # Check for transfer info
                transfer_pattern = r"Transfer\s+'([^']+)'\s+([\d.]+)\s+from\s+acct:\s+([^'\s]+)"
                transfer_match = re.search(transfer_pattern, context_after)
                
                if transfer_match:
                    transfer_type = transfer_match.group(1)
                    transfer_amount = transfer_match.group(2)
                    transfer_account = transfer_match.group(3)
                    description += f"Transfer {transfer_type} {transfer_amount} from account {transfer_account}"
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
                # Set amount based on transaction type
                amount = principal if trans_type == "Principal advance" else -principal
                
                # Determine transaction type
                transaction_type = categorize_transaction(description, amount, trans_type)
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': transaction_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Interest': interest,
                    'Late Charge': late_charge if late_charge > 0 else None,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
            
            # 4. Enhanced Pattern for regular eChecking withdrawals with better account detection
            echecking_pattern = r'(\d{2}[A-Z]{3})\s+Withdrawal\s+([-+]?[\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(echecking_pattern, full_text):
                # Skip if the previous character is an asterisk (already captured)
                prev_char_pos = max(0, match.start() - 1)
                prev_char = full_text[prev_char_pos:match.start()]
                if '*' in prev_char:
                    continue  # Skip this match as it's already captured by the pattern with asterisk
                
                # Determine account with improved context detection
                context_before = full_text[max(0, match.start() - 1000):match.start()]
                
                # Check for eChecking section markers
                if "eChecking No. 900165187" in context_before[-500:] or "eChecking Suffix 8" in context_before[-500:]:
                    account_type = "eChecking"
                    account_number = "8"
                else:
                    account_type, account_number = determine_account_section(full_text, match.start())
                
                trans_date = match.group(1)
                amount_str = match.group(2)
                balance_str = match.group(3)
                
                # Look for description in the text after this match
                context_after = full_text[match.end():match.end() + 500]
                
                # Default description
                description = ""
                
                # Look for various patterns in the following context
                
                # Enhanced merchant pattern with more details
                merchant_pattern = r'([A-Z0-9\s\-\'&.#]+?)\s+([A-Z0-9\s\-\'&.#]+?)\s+(?:(?:[A-Z]{2}\s+US)|(?:[A-Za-z]+\s+TX\s+US))\s+Trace\s+#(\w+)'
                merchant_match = re.search(merchant_pattern, context_after)
                
                if merchant_match:
                    merchant = merchant_match.group(1).strip()
                    location = merchant_match.group(2).strip()
                    trace_number = merchant_match.group(3)
                    # Combine to form a more descriptive string
                    description = f"{merchant} - {location} (Trace: {trace_number})"
                else:
                    # Simpler pattern if the enhanced one doesn't match
                    simple_merchant_pattern = r'([A-Z0-9\s\-\'&.#]+?)\s+Trace\s+#(\w+)'
                    simple_match = re.search(simple_merchant_pattern, context_after)
                    
                    if simple_match:
                        merchant = simple_match.group(1).strip()
                        trace_number = simple_match.group(2)
                        description = f"{merchant} (Trace: {trace_number})"
                
                # Check for ACH pattern
                if not description:
                    ach_pattern = r'Withdrawal-ACH-A-([^\s]+)\s+(.*?)(?:\n|$)'
                    ach_match = re.search(ach_pattern, context_after)
                    
                    if ach_match:
                        ach_code = ach_match.group(1)
                        ach_desc = ach_match.group(2).strip()
                        description = f"ACH {ach_code}: {ach_desc}"
                
                # Check for XML-like ACH data (unique to 2007 format)
                if not description:
                    xml_ach_pattern = r'A-UT\s+\[P\]\.\.\.UNIV\s+TX\s+AUSTIN(.*?)(?:\n\d{2}[A-Z]{3}|\Z)'
                    xml_match = re.search(xml_ach_pattern, context_after, re.DOTALL)
                    
                    if xml_match:
                        xml_data = xml_match.group(1).strip()
                        # Extract relevant payment information
                        payment_info_pattern = r'\*C\*(\d+\.\d+)\*C\*ACH\*CTX'
                        payment_match = re.search(payment_info_pattern, xml_data)
                        
                        if payment_match:
                            payment_amount = payment_match.group(1)
                            description = f"ACH Payment from University of Texas Austin: ${payment_amount}"
                        else:
                            description = "ACH Payment from University of Texas Austin"
                
                # Check for check pattern
                if not description:
                    check_pattern = r'Withdrawal\s+#(\d+)'
                    check_match = re.search(check_pattern, context_after)
                    
                    if check_match:
                        check_number = check_match.group(1)
                        description = f"Check #{check_number}"
                
                # Check for transfer pattern
                if not description:
                    transfer_pattern = r"Transfer\s+'([^']+)'\s+([\d.]+)\s+to\s+acct:\s+([^'\s]+)"
                    transfer_match = re.search(transfer_pattern, context_after)
                    
                    if transfer_match:
                        transfer_type = transfer_match.group(1)
                        transfer_amount = transfer_match.group(2)
                        transfer_account = transfer_match.group(3)
                        description = f"Transfer {transfer_type} {transfer_amount} to account {transfer_account}"
                
                # Check for ATM or merchant location
                if not description:
                    location_pattern = r'(\w+\s+\&\s+\w+|\d+\s+\&\s+\w+)\s+([A-Za-z]+)\s+TX\s+US\s+Trace\s+#(\w+)'
                    location_match = re.search(location_pattern, context_after)
                    
                    if location_match:
                        location = location_match.group(1)
                        city = location_match.group(2)
                        trace = location_match.group(3)
                        description = f"{location}, {city} (Trace: {trace})"
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    # Make withdrawal amounts negative if not explicitly marked
                    if not amount_str.startswith('+'):
                        amount = -abs(amount)
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                # Determine transaction type
                transaction_type = categorize_transaction(description, amount, "Withdrawal")
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': transaction_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Has_Asterisk': False,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
            
            # 5. Enhanced Pattern for deposits in eChecking
            deposit_pattern = r'(\d{2}[A-Z]{3})\s+Deposit(?:-Payroll)?\s+([\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(deposit_pattern, full_text):
                # Determine account with improved context detection
                context_before = full_text[max(0, match.start() - 1000):match.start()]
                
                if "eChecking No. 900165187" in context_before[-500:] or "eChecking Suffix 8" in context_before[-500:]:
                    account_type = "eChecking"
                    account_number = "8"
                elif "Regular Your balance" in context_before[-500:] or "Regular Savings" in context_before[-500:]:
                    account_type = "Regular Savings"
                    account_number = "0"
                else:
                    # Use the more sophisticated account detection
                    account_type, account_number = determine_account_section(full_text, match.start())
                
                trans_date = match.group(1)
                amount_str = match.group(2)
                balance_str = match.group(3)
                
                # Look for description
                context_after = full_text[match.end():match.end() + 500]
                description = ""
                
                # Check if this is a payroll deposit
                payroll_match = re.search(r'University of Texas Payroll', context_after)
                if payroll_match:
                    description = "University of Texas Payroll"
                    trans_type = "Deposit-Payroll"
                else:
                    trans_type = "Deposit"
                    
                    # Look for ACH deposit description - 2007 format has specific XML-like data
                    ach_pattern = r'Deposit-ACH-A-([^\s]+)\s+(.*?)(?:\n|$)'
                    ach_match = re.search(ach_pattern, context_after)
                    
                    if ach_match:
                        ach_code = ach_match.group(1)
                        ach_desc = ach_match.group(2).strip()
                        description = f"ACH {ach_code}: {ach_desc}"
                    else:
                        # Check for transfer pattern
                        transfer_pattern = r'On Demand Internet Transaction\s+Trace\s+#(\w+)'
                        transfer_detail_pattern = r'Transfer\s+"([^"]+)"\s+([\d.]+)\s+from\s+share\s+(\w+)'
                        
                        transfer_match = re.search(transfer_pattern, context_after)
                        if transfer_match:
                            trace_number = transfer_match.group(1)
                            transfer_detail_match = re.search(transfer_detail_pattern, context_after)
                            
                            if transfer_detail_match:
                                transfer_type = transfer_detail_match.group(1)
                                transfer_amount = transfer_detail_match.group(2)
                                transfer_source = transfer_detail_match.group(3)
                                description = f"Transfer {transfer_type} {transfer_amount} from share {transfer_source} (Trace: {trace_number})"
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                # Determine transaction type
                transaction_type = categorize_transaction(description, amount, trans_type)
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': transaction_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
            
            # 6. Enhanced Pattern for check clearing (unique to 2007 format)
            check_patterns = [
                r'(\d{2}[A-Z]{3})\s+Withdrawal\s+#(\d+)\s+([-+]?[\d.]+)\s+=\s+([\d.]+)',
                r'(\d{2}[A-Z]{3})\s+Withdrawal\s+#(\d+)'
            ]
            
            for pattern in check_patterns:
                for match in re.finditer(pattern, full_text):
                    trans_date = match.group(1)
                    check_number = match.group(2)
                    
                    # Determine account based on context
                    context_before = full_text[max(0, match.start() - 1000):match.start()]
                    
                    if "eChecking No. 900165187" in context_before[-500:] or "eChecking Suffix 8" in context_before[-500:]:
                        account_type = "eChecking"
                        account_number = "8"
                    else:
                        account_type, account_number = determine_account_section(full_text, match.start())
                    
                    # Get amount and balance if available in this pattern
                    amount = None
                    balance = None
                    
                    if len(match.groups()) >= 4:
                        amount_str = match.group(3)
                        balance_str = match.group(4)
                        try:
                            amount = float(amount_str)
                            # Make withdrawal amounts negative
                            amount = -abs(amount)
                            balance = float(balance_str)
                        except ValueError:
                            continue
                    else:
                        # Look for amount in the text after the match
                        context_after = full_text[match.end():match.end() + 100]
                        amount_pattern = r'([-+]?[\d.]+)\s+=\s+([\d.]+)'
                        amount_match = re.search(amount_pattern, context_after)
                        
                        if amount_match:
                            amount_str = amount_match.group(1)
                            balance_str = amount_match.group(2)
                            try:
                                amount = float(amount_str)
                                # Make withdrawal amounts negative
                                amount = -abs(amount)
                                balance = float(balance_str)
                            except ValueError:
                                continue
                    
                    # Skip if amount couldn't be determined
                    if amount is None or balance is None:
                        continue
                        
                    # Convert date format
                    day = trans_date[:2]
                    month_abbr = trans_date[2:5]
                    complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                    
                    description = f"Check #{check_number}"
                    
                    transaction = {
                        'Account': f"{account_type} ({account_number})",
                        'Transaction Date': complete_date,
                        'Effective Date': complete_date,
                        'Processing Date': complete_date,
                        'Type': 'Check',
                        'Description': description,
                        'Check Number': check_number,
                        'Amount': amount,
                        'Balance': balance,
                        'Statement Period': statement_period,
                        'Source_PDF': pdf_source_id,
                    }
                    all_transactions.append(transaction)
                
            # 7. Enhanced Pattern for overdraft transfers and fees (specific to 2007 format)
            overdraft_pattern = r'(\d{2}[A-Z]{3})\s+Withdrawal\s+Overdraft\s+transfer\s+to\s+([A-Z\-\d]+)\s+([-+]?[\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(overdraft_pattern, full_text):
                # This pattern is almost certainly for Regular Savings
                account_type = "Regular Savings"
                account_number = "0"
                
                trans_date = match.group(1)
                dest_account = match.group(2)
                amount_str = match.group(3)
                balance_str = match.group(4)
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    # Make withdrawal amounts negative
                    amount = -abs(amount)
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                description = f"Overdraft transfer to {dest_account}"
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': 'Overdraft Transfer',
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
                
            # 8. Enhanced Pattern for ATM and Non-UFCU ATM fees (specific to 2007 format)
            atm_fee_pattern = r'(\d{2}[A-Z]{3})\s+Withdrawal-Fee\s+([-+]?[\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(atm_fee_pattern, full_text):
                # Determine account based on context - these are usually eChecking
                account_type = "eChecking"
                account_number = "8"
                
                trans_date = match.group(1)
                amount_str = match.group(2)
                balance_str = match.group(3)
                
                # Look for description in the text after this match
                context_after = full_text[match.end():match.end() + 200]
                
                # Default description
                description = "ATM Fee"
                
                # Look for the specific fee description
                fee_desc_pattern = r'(\d{2}[A-Z]{3})\s+EFT\s+SERVICE\s+FEE\s+([^\n]+)'
                fee_desc_match = re.search(fee_desc_pattern, context_after)
                
                if fee_desc_match:
                    fee_date = fee_desc_match.group(1)
                    fee_desc = fee_desc_match.group(2).strip()
                    description = f"EFT Service Fee: {fee_desc}"
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    # Make fee amounts negative
                    amount = -abs(amount)
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': 'Fee',
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
                
            # 9. Enhanced Pattern for dividend payments specific to 2007 format
            dividend_pattern = r'(\d{2}[A-Z]{3})\s+Dividend\s+through\s+\d{2}[A-Z]{3}\d{4}\s+([\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(dividend_pattern, full_text):
                # Determine account based on context
                context_before = full_text[max(0, match.start() - 1000):match.start()]
                
                if "Regular Your balance" in context_before[-300:] or "Regular Savings" in context_before[-300:]:
                    account_type = "Regular Savings" 
                    account_number = "0"
                elif "eChecking No. 900165187" in context_before[-300:]:
                    account_type = "eChecking"
                    account_number = "8"
                else:
                    account_type, account_number = determine_account_section(full_text, match.start())
                
                trans_date = match.group(1)
                amount_str = match.group(2)
                balance_str = match.group(3)
                
                # Look for description in the text after this match
                context_after = full_text[match.end():match.end() + 200]
                
                description = "Dividend Payment"
                
                # Look for APY information
                apy_pattern = r'ANNUAL\s+PERCENTAGE\s+YIELD\s+EARNED:\s+([\d.]+)\%\s+FOR\s+A\s+(\d+)\s+DAY\s+PERIOD'
                avg_balance_pattern = r'Average\s+Daily\s+Balance:\s+([\d.]+)'
                
                apy_match = re.search(apy_pattern, context_after)
                avg_match = re.search(avg_balance_pattern, context_after)
                
                if apy_match:
                    apy = apy_match.group(1)
                    period = apy_match.group(2)
                    description = f"Dividend at {apy}% APY for {period} day period"
                    
                    if avg_match:
                        avg_balance = avg_match.group(1)
                        description += f" (Avg Balance: ${avg_balance})"
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
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
                    'Type': 'Dividend',
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
                
            # 10. Process ACH transactions with XML-like data (unique to 2007 format)
            ach_pattern = r'(\d{2}[A-Z]{3})\s+(?:Deposit|Withdrawal)-ACH-A-([^\s]+)\s+([-+]?[\d.]+)\s+=\s+([\d.]+)'
            for match in re.finditer(ach_pattern, full_text):
                # Determine account based on context
                context_before = full_text[max(0, match.start() - 1000):match.start()]
                
                if "eChecking No. 900165187" in context_before[-300:]:
                    account_type = "eChecking"
                    account_number = "8"
                else:
                    account_type, account_number = determine_account_section(full_text, match.start())
                
                trans_date = match.group(1)
                ach_code = match.group(2)
                amount_str = match.group(3)
                balance_str = match.group(4)
                
                # Look for XML-like data after this transaction
                context_after = full_text[match.end():match.end() + 1000]
                
                trans_type = "Deposit" if "Deposit-ACH" in full_text[match.start()-20:match.start()+20] else "Withdrawal"
                description = f"ACH {ach_code}"
                
                # Look for additional structured data
                xml_pattern = r'A-[A-Z]+\s+\[P\]\.\.\.([^\n]+)'
                xml_matches = re.finditer(xml_pattern, context_after, re.MULTILINE)
                
                xml_data = []
                for xml_match in list(xml_matches)[:5]:  # Limit to first 5 matches
                    xml_data.append(xml_match.group(1).strip())
                
                if xml_data:
                    # Extract useful info from XML-like data
                    company_name_pattern = r'\*([^*]+)\*'
                    amount_pattern = r'\*C\*(\d+\.\d+)\*C\*'
                    
                    company_match = None
                    amount_match = None
                    
                    for line in xml_data:
                        if not company_match:
                            company_match = re.search(company_name_pattern, line)
                        if not amount_match:
                            amount_match = re.search(amount_pattern, line)
                    
                    if company_match:
                        company = company_match.group(1).strip()
                        description += f": {company}"
                    
                    if amount_match:
                        ach_amount = amount_match.group(1)
                        if not company_match:  # Only add amount if we didn't find a company
                            description += f": ${ach_amount}"
                
                # Convert date format
                day = trans_date[:2]
                month_abbr = trans_date[2:5]
                complete_date = standardize_date(day, month_abbr, statement_year, month_map)
                
                # Parse amount and balance
                try:
                    amount = float(amount_str)
                    if trans_type == "Withdrawal" and amount > 0:
                        amount = -amount  # Make withdrawal amounts negative
                    balance = float(balance_str)
                except ValueError:
                    continue
                
                transaction = {
                    'Account': f"{account_type} ({account_number})",
                    'Transaction Date': complete_date,
                    'Effective Date': complete_date,
                    'Processing Date': complete_date,
                    'Type': trans_type if not description.startswith("ACH") else "ACH " + trans_type,
                    'Description': description,
                    'Amount': amount,
                    'Balance': balance,
                    'Statement Period': statement_period,
                    'Source_PDF': pdf_source_id,
                }
                all_transactions.append(transaction)
        
        if all_transactions:
            # Deduplicate transactions with improved logic
            all_transactions = deduplicate_transactions(all_transactions)
            
            # Sort transactions by date and account
            df = pd.DataFrame(all_transactions)
            df = df.sort_values(by=['Transaction Date', 'Account'])
            
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
    parser = argparse.ArgumentParser(description='Extract transactions from 2007 format bank statement PDFs')
    parser.add_argument('--input-dir', '-i', default='/data/input', 
                        help='Directory containing PDF files (default: /data/input)')
    parser.add_argument('--output-dir', '-o', default='/data/output',
                        help='Directory to save output files (default: /data/output)')
    parser.add_argument('--format', '-f', choices=['csv', 'tsv'], default='csv',
                        help='Output format (default: csv)')
    parser.add_argument('--file', help='Process a single PDF file instead of a directory')
    parser.add_argument('--debug', action='store_true', help='Enable detailed debug logging')
    
    args = parser.parse_args()
    
    print(f"Bank Statement Parser (2007 Format) starting...")
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