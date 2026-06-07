#!/usr/bin/env python3
"""
Catechesis Localization Toolkit
"""

import os
import sys
import json
import re
import zipfile
from pathlib import Path
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openai import OpenAI

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def load_and_validate_env(mode='parse'):
    """Load .env file and validate all required parameters exist."""
    # Load .env file
    if not os.path.exists('.env'):
        print("Error: .env file not found. Please create one based on .env.example")
        sys.exit(1)
    
    load_dotenv()
    
    # Required parameters for all modes
    required_params = {
        'GOOGLE_CREDS': 'Path to Google credentials JSON file',
        'GOOGLE_SPREADSHEET_ID': 'Google Spreadsheet ID',
        'SOURCE_LANG': 'Source language code',
        'TARGET_LANG': 'Target language code',
    }
    
    # Add mode-specific required parameters
    if mode == 'parse':
        required_params['SOURCE_DIR'] = 'Source directory for properties files'
    elif mode == 'translate' or mode == 'validate':
        required_params['OPENAI_API_ENDPOINT'] = 'OpenAI API endpoint'
        required_params['OPENAI_API_KEY'] = 'OpenAI API key'
        required_params['OPENAI_MODEL'] = 'OpenAI model name'
    elif mode == 'build':
        required_params['SOURCE_DIR'] = 'Source directory for properties files'
        required_params['TARGET_DIR'] = 'Target directory for localized files'
    
    missing_params = []
    config = {}
    
    for param, description in required_params.items():
        value = os.getenv(param)
        if not value:
            missing_params.append(f"  - {param}: {description}")
        else:
            config[param] = value
    
    if missing_params:
        print("Error: Missing required environment variables in .env file:")
        print("\n".join(missing_params))
        sys.exit(1)
    
    # Validate that files/directories exist
    if not os.path.exists(config['GOOGLE_CREDS']):
        print(f"Error: Google credentials file not found: {config['GOOGLE_CREDS']}")
        sys.exit(1)
    
    if mode == 'parse' and not os.path.exists(config['SOURCE_DIR']):
        print(f"Error: Source directory not found: {config['SOURCE_DIR']}")
        sys.exit(1)
    
    if mode == 'build' and not os.path.exists(config['SOURCE_DIR']):
        print(f"Error: Source directory not found: {config['SOURCE_DIR']}")
        sys.exit(1)
    
    # Optional parameters
    config['SOURCE_DIR'] = os.getenv('SOURCE_DIR', 'source')
    config['TARGET_DIR'] = os.getenv('TARGET_DIR', 'target')
    config['TRANSLATION_CONTEXT'] = os.getenv('TRANSLATION_CONTEXT', '')
    config['OPENAI_TEMPERATURE'] = float(os.getenv('OPENAI_TEMPERATURE', '0.2'))
    config['PACK_LOCALIZATION'] = os.getenv('PACK_LOCALIZATION', 'false').lower() == 'true'
    
    return config


def get_google_sheets_service(creds_file):
    """Authenticate and return Google Sheets service using service account."""
    try:
        creds = service_account.Credentials.from_service_account_file(
            creds_file, scopes=SCOPES)
        service = build("sheets", "v4", credentials=creds)
        return service
    except Exception as err:
        print(f"Error creating Google Sheets service: {err}")
        print("Make sure you're using a service account credentials file.")
        sys.exit(1)


def parse_properties_file(filepath):
    """Parse a .properties file and return a dict of key-value pairs."""
    properties = {}
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue
            
            # Split on first '=' only
            if '=' in line:
                key, value = line.split('=', 1)
                properties[key.strip()] = value.strip()
    
    return properties


def find_properties_files(source_dir, source_lang):
    """Find all properties files matching the pattern _${SOURCE_LANG}.properties."""
    pattern = f"_{source_lang}.properties"
    properties_files = []
    
    source_path = Path(source_dir)
    for filepath in source_path.glob(f"*{pattern}"):
        if filepath.is_file():
            # Extract sheet name by removing the pattern
            sheet_name = filepath.stem.replace(f"_{source_lang}", "")
            properties_files.append({
                'filepath': str(filepath),
                'sheet_name': sheet_name
            })
    
    return properties_files


def get_existing_sheets(service, spreadsheet_id):
    """Get list of existing sheet names in the spreadsheet."""
    try:
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = spreadsheet.get('sheets', [])
        return {sheet['properties']['title']: sheet['properties']['sheetId'] for sheet in sheets}
    except HttpError as err:
        print(f"Error accessing spreadsheet: {err}")
        sys.exit(1)


def create_sheet(service, spreadsheet_id, sheet_name, source_lang, target_lang):
    """Create a new sheet with headers and format as plain text."""
    try:
        # Create the sheet
        add_sheet_response = service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                'requests': [{
                    'addSheet': {
                        'properties': {
                            'title': sheet_name
                        }
                    }
                }]
            }
        ).execute()
        
        # Get the new sheet ID
        sheet_id = add_sheet_response['replies'][0]['addSheet']['properties']['sheetId']
        
        # Format all columns as plain text to prevent auto-linking
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                'requests': [{
                    'repeatCell': {
                        'range': {
                            'sheetId': sheet_id,
                            'startColumnIndex': 0,
                            'endColumnIndex': 3
                        },
                        'cell': {
                            'userEnteredFormat': {
                                'numberFormat': {
                                    'type': 'TEXT'
                                }
                            }
                        },
                        'fields': 'userEnteredFormat.numberFormat'
                    }
                }]
            }
        ).execute()
        
        # Add headers
        headers = [['key', source_lang, target_lang]]
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1:C1",
            valueInputOption='RAW',
            body={'values': headers}
        ).execute()
        
        return True
    except HttpError as err:
        print(f"Error creating sheet {sheet_name}: {err}")
        return False


def get_sheet_data(service, spreadsheet_id, sheet_name):
    """Get all data from a sheet."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A:C"
        ).execute()
        return result.get('values', [])
    except HttpError as err:
        print(f"Error reading sheet {sheet_name}: {err}")
        return []


def update_existing_sheet(service, spreadsheet_id, sheet_name, source_lang, new_data):
    """Update existing sheet with new/changed data."""
    # Get existing data
    existing_data = get_sheet_data(service, spreadsheet_id, sheet_name)
    
    if not existing_data or len(existing_data) < 1:
        return 0, 0
    
    # Build a map of existing keys to their row index and values
    existing_keys = {}
    for idx, row in enumerate(existing_data[1:], start=2):  # Start from row 2 (skip header)
        if row:  # Skip empty rows
            key = row[0] if len(row) > 0 else ''
            source_value = row[1] if len(row) > 1 else ''
            target_value = row[2] if len(row) > 2 else ''
            existing_keys[key] = {
                'row': idx,
                'source': source_value,
                'target': target_value
            }
    
    updates = []
    updated_keys = []
    added_count = 0
    
    # Check for updates to existing keys
    for key, value in new_data.items():
        if key in existing_keys:
            # Check if source value changed
            if existing_keys[key]['source'] != value:
                row_num = existing_keys[key]['row']
                updates.append({
                    'range': f"{sheet_name}!B{row_num}",
                    'values': [[value]]
                })
                updated_keys.append(key)
        else:
            # New key - will be added at the end
            added_count += 1
    
    # Apply updates for changed values
    if updates:
        try:
            batch_update_body = {
                'valueInputOption': 'RAW',
                'data': updates
            }
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body=batch_update_body
            ).execute()
        except HttpError as err:
            print(f"Error updating sheet {sheet_name}: {err}")
    
    # Add new keys at the end
    if added_count > 0:
        new_rows = []
        for key, value in new_data.items():
            if key not in existing_keys:
                new_rows.append([key, value, ''])
        
        if new_rows:
            try:
                next_row = len(existing_data) + 1
                service.spreadsheets().values().append(
                    spreadsheetId=spreadsheet_id,
                    range=f"{sheet_name}!A{next_row}",
                    valueInputOption='RAW',
                    body={'values': new_rows}
                ).execute()
            except HttpError as err:
                print(f"Error adding new keys to sheet {sheet_name}: {err}")
    
    return updated_keys, added_count


def populate_new_sheet(service, spreadsheet_id, sheet_name, data):
    """Populate a newly created sheet with data."""
    rows = [[key, value, ''] for key, value in data.items()]
    
    try:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A2",
            valueInputOption='RAW',
            body={'values': rows}
        ).execute()
        return len(rows)
    except HttpError as err:
        print(f"Error populating sheet {sheet_name}: {err}")
        return 0


def parse_and_upload(config):
    """Main logic for parsing properties files and uploading to Google Sheets."""
    print("Starting parse and upload process...")
    print(f"Source directory: {config['SOURCE_DIR']}")
    print(f"Source language: {config['SOURCE_LANG']}")
    print(f"Target language: {config['TARGET_LANG']}")
    print()
    
    # Find all properties files
    properties_files = find_properties_files(config['SOURCE_DIR'], config['SOURCE_LANG'])
    
    if not properties_files:
        print(f"No properties files found matching pattern *_{config['SOURCE_LANG']}.properties")
        return
    
    print(f"Found {len(properties_files)} properties file(s):")
    for pf in properties_files:
        print(f"  - {pf['sheet_name']} ({pf['filepath']})")
    print()
    
    # Get Google Sheets service
    print("Authenticating with Google Sheets...")
    service = get_google_sheets_service(config['GOOGLE_CREDS'])
    print("Authentication successful!")
    print()
    
    # Get existing sheets
    existing_sheets = get_existing_sheets(service, config['GOOGLE_SPREADSHEET_ID'])
    print(f"Found {len(existing_sheets)} existing sheet(s) in spreadsheet")
    print()
    
    # Statistics
    stats = {
        'sheets_created': 0,
        'sheets_updated': 0,
        'keys_added': 0,
        'keys_updated': 0
    }
    
    # Process each properties file
    for pf in properties_files:
        sheet_name = pf['sheet_name']
        print(f"Processing sheet: {sheet_name}")
        
        # Parse the properties file
        properties = parse_properties_file(pf['filepath'])
        print(f"  Parsed {len(properties)} key-value pairs")
        
        if sheet_name in existing_sheets:
            # Update existing sheet
            print(f"  Sheet exists, updating...")
            updated_keys, added = update_existing_sheet(
                service,
                config['GOOGLE_SPREADSHEET_ID'],
                sheet_name,
                config['SOURCE_LANG'],
                properties
            )
            stats['sheets_updated'] += 1
            stats['keys_updated'] += len(updated_keys)
            stats['keys_added'] += added
            print(f"  Updated {len(updated_keys)} existing key(s), added {added} new key(s)")
            for key in updated_keys:
                print(f"    ~ {key}")
        else:
            # Create new sheet
            print(f"  Creating new sheet...")
            if create_sheet(service, config['GOOGLE_SPREADSHEET_ID'], sheet_name, 
                          config['SOURCE_LANG'], config['TARGET_LANG']):
                added = populate_new_sheet(service, config['GOOGLE_SPREADSHEET_ID'], 
                                         sheet_name, properties)
                stats['sheets_created'] += 1
                stats['keys_added'] += added
                print(f"  Created sheet with {added} key(s)")
        
        print()
    
    # Display final statistics
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Sheets created:           {stats['sheets_created']}")
    print(f"Sheets updated:           {stats['sheets_updated']}")
    print(f"Total keys added:         {stats['keys_added']}")
    print(f"Total keys updated:       {stats['keys_updated']}")
    print("=" * 60)


def translate_with_llm(client, model, temperature, source_lang, target_lang, translation_context, batch):
    """Translate a batch of key-value pairs using LLM."""
    # Build system prompt
    system_prompt = f"""Please translate the following set of strings from source language code `{source_lang}` to target language code `{target_lang}`.

For example if you get
[
  {{"some.key": "some source string"}},
  {{"some.other.key": "some other source string"}}
]

You should return
[
  {{"some.key": "translated string"}},
  {{"some.other.key": "other translated string"}}
]

Return only JSON, try to maintain original style of writing. The string to translate could be parts of the dialogue, object names or some technical info. It's important to keep all special formatting intact, for example color tags!"""

    # Add translation context if provided
    if translation_context:
        system_prompt += f"\n\nUse the following transaction context to improve the translation.\n\n{translation_context}"
    
    # Format batch as JSON for user message
    user_message = json.dumps(batch, ensure_ascii=False, indent=2)
    
    try:
        # Call OpenAI API
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=temperature
        )
        
        # Extract response content
        content = response.choices[0].message.content.strip()
        
        # Parse JSON from response (handle backticks and json label)
        # Remove markdown code blocks if present
        content = re.sub(r'^```json\s*\n', '', content)
        content = re.sub(r'^```\s*\n', '', content)
        content = re.sub(r'\n```\s*$', '', content)
        content = content.strip()
        
        # Parse JSON
        translated_batch = json.loads(content)
        
        return translated_batch
    except json.JSONDecodeError as e:
        print(f"  Error: Failed to parse LLM response as JSON: {e}")
        print(f"  Response was: {content[:200]}...")
        return None
    except Exception as e:
        print(f"  Error calling LLM API: {e}")
        return None


def translate_sheet(config, sheet_name):
    """Translate strings in a Google Sheet using LLM."""
    print(f"Starting translation for sheet: {sheet_name}")
    print(f"Source language: {config['SOURCE_LANG']}")
    print(f"Target language: {config['TARGET_LANG']}")
    print(f"Model: {config['OPENAI_MODEL']}")
    print()
    
    # Get Google Sheets service
    print("Authenticating with Google Sheets...")
    service = get_google_sheets_service(config['GOOGLE_CREDS'])
    print("Authentication successful!")
    print()
    
    # Check if sheet exists
    existing_sheets = get_existing_sheets(service, config['GOOGLE_SPREADSHEET_ID'])
    if sheet_name not in existing_sheets:
        print(f"Error: Sheet '{sheet_name}' does not exist in the spreadsheet")
        print(f"Available sheets: {', '.join(existing_sheets.keys())}")
        sys.exit(1)
    
    # Get sheet data
    print(f"Reading sheet data...")
    sheet_data = get_sheet_data(service, config['GOOGLE_SPREADSHEET_ID'], sheet_name)
    
    if not sheet_data or len(sheet_data) < 2:
        print("Error: Sheet is empty or has no data rows")
        sys.exit(1)
    
    print(f"Found {len(sheet_data) - 1} rows (excluding header)")
    print()
    
    # Load translation context if provided
    translation_context = ""
    if config['TRANSLATION_CONTEXT'] and os.path.exists(config['TRANSLATION_CONTEXT']):
        with open(config['TRANSLATION_CONTEXT'], 'r', encoding='utf-8') as f:
            translation_context = f.read().strip()
        print(f"Loaded translation context from {config['TRANSLATION_CONTEXT']}")
        print()
    
    # Initialize OpenAI client
    client = OpenAI(
        api_key=config['OPENAI_API_KEY'],
        base_url=config['OPENAI_API_ENDPOINT']
    )
    
    # Collect rows that need translation
    rows_to_translate = []
    rows_already_translated = 0
    
    for idx, row in enumerate(sheet_data[1:], start=2):  # Skip header, start from row 2
        if len(row) < 2:
            continue  # Skip rows without key and source value
        
        key = row[0] if len(row) > 0 else ''
        source_value = row[1] if len(row) > 1 else ''
        target_value = row[2] if len(row) > 2 else ''
        
        # Skip if translation already exists
        if target_value.strip():
            rows_already_translated += 1
            continue
        
        rows_to_translate.append({
            'row_num': idx,
            'key': key,
            'value': source_value
        })
    
    if not rows_to_translate:
        print("No rows need translation (all rows already have translations)")
        return
    
    print(f"Found {len(rows_to_translate)} rows to translate")
    if rows_already_translated > 0:
        print(f"Skipping {rows_already_translated} rows that already have translations")
    print()
    
    # Process in batches of 100
    batch_size = 100
    total_translated = 0
    total_failed = 0
    
    for batch_start in range(0, len(rows_to_translate), batch_size):
        batch_end = min(batch_start + batch_size, len(rows_to_translate))
        batch_rows = rows_to_translate[batch_start:batch_end]
        
        print(f"Processing batch {batch_start // batch_size + 1} ({len(batch_rows)} keys)...")
        
        # Prepare batch for translation
        batch_for_llm = [{row['key']: row['value']} for row in batch_rows]
        
        # Translate batch
        translated_batch = translate_with_llm(
            client,
            config['OPENAI_MODEL'],
            config['OPENAI_TEMPERATURE'],
            config['SOURCE_LANG'],
            config['TARGET_LANG'],
            translation_context,
            batch_for_llm
        )
        
        if translated_batch is None:
            print(f"  Skipping batch due to error")
            total_failed += len(batch_rows)
            continue
        
        # Update sheet with translations
        updates = []
        for row_info in batch_rows:
            key = row_info['key']
            row_num = row_info['row_num']
            
            # Find translation in response
            translation = None
            for item in translated_batch:
                if key in item:
                    translation = item[key]
                    break
            
            if translation:
                updates.append({
                    'range': f"{sheet_name}!C{row_num}",
                    'values': [[translation]]
                })
                total_translated += 1
            else:
                print(f"  Warning: No translation found for key '{key}'")
                total_failed += 1
        
        # Apply updates to sheet
        if updates:
            try:
                batch_update_body = {
                    'valueInputOption': 'RAW',
                    'data': updates
                }
                service.spreadsheets().values().batchUpdate(
                    spreadsheetId=config['GOOGLE_SPREADSHEET_ID'],
                    body=batch_update_body
                ).execute()
                print(f"  Successfully updated {len(updates)} translations")
            except HttpError as err:
                print(f"  Error updating sheet: {err}")
                total_failed += len(updates)
                total_translated -= len(updates)
        
        print()
    
    # Display statistics
    print("=" * 60)
    print("TRANSLATION SUMMARY")
    print("=" * 60)
    print(f"Sheet processed:          {sheet_name}")
    print(f"Keys translated:          {total_translated}")
    print(f"Keys skipped:             {rows_already_translated} (already had translations)")
    print(f"Keys failed:              {total_failed}")
    print(f"Total keys in sheet:      {len(sheet_data) - 1}")
    print("=" * 60)


def validate_with_llm(client, model, temperature, source_lang, target_lang, translation_context, entries):
    """Validate translations using LLM."""
    # Build the validation prompt
    system_prompt = f"""Please validate the following translation of {source_lang} to {target_lang}. Each translation entry has a key, original string and translated string. Please check for consistency, styling, typos and other suspicious things. Feel free to propose better translations as well, but this is secondary, the main goal is validation. Return the list of suspicious keys and explanation why you think it's wrong for each. For your report you can use {target_lang} language.

For example, if you get this:

#### example.key1
Original: `example original string`
Translated: `example translated string`

#### example.key2
Original: `another example original string`
Translated: `another exmple translated string!`

Return this:

#### example.key2
Translated string contains a typo in the word "example" and also exclamation mark that doesn't exist in the original string."""

    # Add translation context if provided
    if translation_context:
        system_prompt += f"\n\nUse the following transaction context to improve the translation.\n\n{translation_context}"
    
    # Format entries for validation
    user_message = ""
    for entry in entries:
        user_message += f"\n#### {entry['key']}\n"
        user_message += f"Original: `{entry['original']}`\n"
        user_message += f"Translated: `{entry['translated']}`\n"
    
    try:
        # Call OpenAI API
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            temperature=temperature
        )
        
        # Extract response content
        content = response.choices[0].message.content.strip()
        return content
    except Exception as e:
        print(f"  Error calling LLM API: {e}")
        return None


def validate_sheet(config, sheet_name):
    """Validate translations in a Google Sheet using LLM."""
    print(f"Starting validation for sheet: {sheet_name}")
    print(f"Source language: {config['SOURCE_LANG']}")
    print(f"Target language: {config['TARGET_LANG']}")
    print(f"Model: {config['OPENAI_MODEL']}")
    print()
    
    # Get Google Sheets service
    print("Authenticating with Google Sheets...")
    service = get_google_sheets_service(config['GOOGLE_CREDS'])
    print("Authentication successful!")
    print()
    
    # Check if sheet exists
    existing_sheets = get_existing_sheets(service, config['GOOGLE_SPREADSHEET_ID'])
    if sheet_name not in existing_sheets:
        print(f"Error: Sheet '{sheet_name}' does not exist in the spreadsheet")
        print(f"Available sheets: {', '.join(existing_sheets.keys())}")
        sys.exit(1)
    
    # Get sheet data
    print(f"Reading sheet data...")
    sheet_data = get_sheet_data(service, config['GOOGLE_SPREADSHEET_ID'], sheet_name)
    
    if not sheet_data or len(sheet_data) < 2:
        print("Error: Sheet is empty or has no data rows")
        sys.exit(1)
    
    print(f"Found {len(sheet_data) - 1} rows (excluding header)")
    print()
    
    # Load translation context if provided
    translation_context = ""
    if config['TRANSLATION_CONTEXT'] and os.path.exists(config['TRANSLATION_CONTEXT']):
        with open(config['TRANSLATION_CONTEXT'], 'r', encoding='utf-8') as f:
            translation_context = f.read().strip()
        print(f"Loaded translation context from {config['TRANSLATION_CONTEXT']}")
        print()
    
    # Initialize OpenAI client
    client = OpenAI(
        api_key=config['OPENAI_API_KEY'],
        base_url=config['OPENAI_API_ENDPOINT']
    )
    
    # Collect entries with translations
    entries_to_validate = []
    rows_without_translation = 0
    
    for idx, row in enumerate(sheet_data[1:], start=2):  # Skip header, start from row 2
        if len(row) < 3:
            continue  # Skip rows without all three columns
        
        key = row[0] if len(row) > 0 else ''
        source_value = row[1] if len(row) > 1 else ''
        target_value = row[2] if len(row) > 2 else ''
        
        # Only validate rows that have translations
        if not target_value.strip():
            rows_without_translation += 1
            continue
        
        entries_to_validate.append({
            'key': key,
            'original': source_value,
            'translated': target_value
        })
    
    if not entries_to_validate:
        print("No entries to validate (no rows have translations)")
        return
    
    print(f"Found {len(entries_to_validate)} entries to validate")
    if rows_without_translation > 0:
        print(f"Skipping {rows_without_translation} rows without translations")
    print()
    
    # Validate all entries at once
    print("Sending validation request to LLM...")
    validation_result = validate_with_llm(
        client,
        config['OPENAI_MODEL'],
        config['OPENAI_TEMPERATURE'],
        config['SOURCE_LANG'],
        config['TARGET_LANG'],
        translation_context,
        entries_to_validate
    )
    
    if validation_result is None:
        print("Error: Validation failed")
        sys.exit(1)
    
    # Save result to file
    output_filename = f"validate-{sheet_name}.md"
    with open(output_filename, 'w', encoding='utf-8') as f:
        f.write(f"# Validation Report: {sheet_name}\n\n")
        f.write(f"**Source Language:** {config['SOURCE_LANG']}\n")
        f.write(f"**Target Language:** {config['TARGET_LANG']}\n")
        f.write(f"**Total Entries Validated:** {len(entries_to_validate)}\n\n")
        f.write("---\n\n")
        f.write(validation_result)
        f.write("\n")
    
    print(f"Validation complete! Report saved to: {output_filename}")
    print()
    
    # Display statistics
    print("=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Sheet validated:          {sheet_name}")
    print(f"Entries validated:        {len(entries_to_validate)}")
    print(f"Entries skipped:          {rows_without_translation} (no translation)")
    print(f"Report saved to:          {output_filename}")
    print("=" * 60)


def build_localization(config):
    """Build localized properties files from Google Sheets translations."""
    print("Starting build process...")
    print(f"Source directory: {config['SOURCE_DIR']}")
    print(f"Target directory: {config['TARGET_DIR']}")
    print(f"Target language: {config['TARGET_LANG']}")
    print()
    
    # Find all properties files to determine sheet names
    properties_files = find_properties_files(config['SOURCE_DIR'], config['SOURCE_LANG'])
    
    if not properties_files:
        print(f"No properties files found matching pattern *_{config['SOURCE_LANG']}.properties")
        return
    
    print(f"Found {len(properties_files)} sheet(s) to process:")
    for pf in properties_files:
        print(f"  - {pf['sheet_name']}")
    print()
    
    # Get Google Sheets service
    print("Authenticating with Google Sheets...")
    service = get_google_sheets_service(config['GOOGLE_CREDS'])
    print("Authentication successful!")
    print()
    
    # Check which sheets exist
    existing_sheets = get_existing_sheets(service, config['GOOGLE_SPREADSHEET_ID'])
    
    # Create target directory if it doesn't exist
    target_path = Path(config['TARGET_DIR'])
    target_path.mkdir(parents=True, exist_ok=True)
    
    # Statistics
    stats = {
        'sheets_processed': 0,
        'total_strings': 0,
        'substituted_strings': 0
    }
    
    # Process each sheet
    for pf in properties_files:
        sheet_name = pf['sheet_name']
        
        # Check if sheet exists
        if sheet_name not in existing_sheets:
            print(f"Warning: Sheet '{sheet_name}' not found in spreadsheet, skipping...")
            continue
        
        print(f"Processing sheet: {sheet_name}")
        
        # Get sheet data
        sheet_data = get_sheet_data(service, config['GOOGLE_SPREADSHEET_ID'], sheet_name)
        
        if not sheet_data or len(sheet_data) < 2:
            print(f"  Warning: Sheet is empty or has no data rows, skipping...")
            continue
        
        # Build key=value pairs
        properties_lines = []
        sheet_strings = 0
        sheet_substituted = 0
        
        for row in sheet_data[1:]:  # Skip header
            if len(row) < 2:
                continue  # Skip rows without key and source value
            
            key = row[0] if len(row) > 0 else ''
            source_value = row[1] if len(row) > 1 else ''
            target_value = row[2] if len(row) > 2 else ''
            
            if not key:
                continue  # Skip rows without key
            
            # Use translation if available, otherwise use source
            if target_value.strip():
                value = target_value
            else:
                value = source_value
                sheet_substituted += 1
            
            properties_lines.append(f"{key}={value}")
            sheet_strings += 1
        
        # Write to file
        output_filename = f"{sheet_name}_{config['TARGET_LANG']}.properties"
        output_path = target_path / output_filename
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(properties_lines))
            if properties_lines:  # Add final newline if file is not empty
                f.write('\n')
        
        print(f"  Generated: {output_filename}")
        print(f"  Strings: {sheet_strings} (substituted: {sheet_substituted})")
        
        stats['sheets_processed'] += 1
        stats['total_strings'] += sheet_strings
        stats['substituted_strings'] += sheet_substituted
        
        print()
    
    # Pack into zip if requested
    if config['PACK_LOCALIZATION']:
        print("Packing localization files...")
        zip_filename = f"catechesis-{config['TARGET_LANG']}.zip"
        
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in target_path.glob(f"*_{config['TARGET_LANG']}.properties"):
                zipf.write(file_path, file_path.name)
                print(f"  Added to archive: {file_path.name}")
        
        print(f"Created archive: {zip_filename}")
        print()
    
    # Display final statistics
    print("=" * 60)
    print("BUILD SUMMARY")
    print("=" * 60)
    print(f"Sheets processed:         {stats['sheets_processed']}")
    print(f"Total strings:            {stats['total_strings']}")
    print(f"Substituted strings:      {stats['substituted_strings']}")
    if config['PACK_LOCALIZATION']:
        print(f"Archive created:          catechesis-{config['TARGET_LANG']}.zip")
    print("=" * 60)


def show_usage():
    """Display usage information."""
    print("Catechesis Localization Toolkit")
    print()
    print("Usage:")
    print("  python run.py parse              - Parse source files and upload to Google Sheets")
    print("  python run.py translate <sheet>  - Translate strings in specified sheet using LLM")
    print("  python run.py validate <sheet>   - Validate translations in specified sheet using LLM")
    print("  python run.py build              - Build localized files from Google Sheets")
    print("  python run.py help               - Show this help message")
    print()
    print("Make sure you have a .env file configured (see .env.example)")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        show_usage()
        sys.exit(0)
    
    command = sys.argv[1].lower()
    
    if command == 'help':
        show_usage()
    elif command == 'parse':
        config = load_and_validate_env(mode='parse')
        parse_and_upload(config)
    elif command == 'translate':
        # Check if sheet name is provided
        if len(sys.argv) < 3:
            print("Error: Sheet name is required for translate command")
            print()
            print("Usage: python run.py translate <sheet_name>")
            sys.exit(1)
        
        sheet_name = sys.argv[2]
        config = load_and_validate_env(mode='translate')
        translate_sheet(config, sheet_name)
    elif command == 'validate':
        # Check if sheet name is provided
        if len(sys.argv) < 3:
            print("Error: Sheet name is required for validate command")
            print()
            print("Usage: python run.py validate <sheet_name>")
            sys.exit(1)
        
        sheet_name = sys.argv[2]
        config = load_and_validate_env(mode='validate')
        validate_sheet(config, sheet_name)
    elif command == 'build':
        config = load_and_validate_env(mode='build')
        build_localization(config)
    else:
        print(f"Unknown command: {command}")
        print()
        show_usage()
        sys.exit(1)


if __name__ == "__main__":
    main()
