# Catechesis Localization Toolkit

A Python tool for managing [Catechesis](https://store.steampowered.com/app/2593320/Catechesis/) localization using Google Sheets as a translation backend and an LLM for machine translation/validation.

The workflow is: parse source files → upload to Google Sheets → translate with LLM → refine manually → validate → build localized files.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and edit the new file, following the comments inside.

### Google Sheets setup

1. Set up [a service account](https://console.cloud.google.com/) (google "How to set up service account to access google sheets" if you're not sure how to do that, [here is one example](https://stackoverflow.com/a/76838253)), save a JSON file with credentials. Don't forget to [enable Google Sheets API](https://support.google.com/googleapi/answer/6158841?hl=en).
2. Set path to the downloaded JSON file as `GOOGLE_CREDS` in `.env`.
3. Create a Google Spreadsheet and copy its ID from the URL into `GOOGLE_SPREADSHEET_ID`.
4. Share the spreadsheet with the service account email (`client_email` in the JSON file).


## Usage

```
python run.py parse              # Parse source files and upload to Google Sheets
python run.py translate <sheet>  # Translate untranslated strings in a sheet using LLM
python run.py validate <sheet>   # Validate translations in a sheet using LLM
python run.py build              # Download translations and generate localized files
python run.py help               # Show usage
```

### parse

Scans `SOURCE_DIR` for files matching `*_<SOURCE_LANG>.properties`, parses key-value pairs, and syncs them to Google Sheets. Each file becomes a sheet with three columns: `key`, source language, target language. Existing translations are never overwritten — only new keys are added and changed source values are updated.

### translate `<sheet>`

Reads untranslated rows from the named sheet and sends them in batches of 100 to the configured LLM. Translations are written back to the target language column. Already-translated rows are skipped.

### validate `<sheet>`

Sends all translated rows from the named sheet to the LLM for review. The LLM checks for consistency, typos, formatting issues, and style. Results are saved to `validate-<sheet>.md`.

### build

Downloads all sheets from Google Sheets and writes `*_<TARGET_LANG>.properties` files to `TARGET_DIR`. Keys without a translation fall back to the source value. If `PACK_LOCALIZATION=true`, all output files are zipped into `catechesis-<TARGET_LANG>.zip`.
