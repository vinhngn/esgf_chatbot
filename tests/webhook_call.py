import pandas as pd
import requests
import json
import re
import sys
import time
from tqdm.auto import tqdm

# --- Configuration ---
# ⬇️ 1. SET YOUR FILE PATHS HERE
INPUT_CSV_PATH = 'publicGraphEvals/movies.csv'  # Change this to your source file
OUTPUT_CSV_PATH = 'publicGraphEvals/results/exe/movies_results_triplets.csv'  # Change this to your desired output file

# API endpoint details
API_URL = "http://127.0.0.1:8000/api/text2cypher"
HEADERS = {"Content-Type": "application/json"}


# ---------------------

def clean_cypher_string(text):
    """
    Cleans a string by replacing all whitespace (newlines, tabs, multiple spaces)
    with a single space and stripping leading/trailing whitespace.
    """
    if not isinstance(text, str):
        return text
    # Replace any whitespace character (\n, \t, " ") with a single space
    cleaned_text = re.sub(r'\s+', ' ', text)
    return cleaned_text.strip()


def format_result_to_string(result_obj):
    """
    Converts the 'result' object (list of dictionaries) into a
    compact JSON string to be stored in a single CSV cell.
    """
    if result_obj is None or (isinstance(result_obj, list) and not result_obj):
        return "[]"
    try:
        # Convert the Python object to a JSON string
        return json.dumps(result_obj)
    except TypeError:
        return str(result_obj)  # Fallback


def get_api_data(question):
    """
    Calls the text2cpher webhook for a single question
    and returns the cleaned cypher and result.

    Includes detailed print statements for debugging.
    """
    payload = {"question": question}
    print(f"\n--- Processing Question ---")
    print(f"URL: {API_URL}")
    print(f"Payload: {json.dumps(payload)}")

    try:
        # Make the POST request. This line WAITS for the response.
        # Increased timeout to 20 seconds just in case.
        response = requests.post(API_URL, headers=HEADERS, json=payload, timeout=120)

        # --- DEBUGGING PRINTS ---
        print(f"Response Status Code: {response.status_code}")
        print(f"Response Text (raw): {response.text}")
        # --- END DEBUGGING PRINTS ---

        # Check for HTTP errors (like 404, 500)
        response.raise_for_status()

        # Parse the JSON response
        try:
            data = response.json()
            print(f"Response JSON (parsed): {data}")
        except json.JSONDecodeError:
            print(f"Error: Could not decode JSON from response.", file=sys.stderr)
            return pd.NA, pd.NA

        # --- Extract and Clean ---

        # 1. Get 'cypher_query'
        raw_cypher = data.get('cypher_query')
        print(f"Extracted 'cypher_query': {raw_cypher}")

        # 2. Get 'result'
        raw_result = data.get('result')
        print(f"Extracted 'result': {raw_result}")

        # 3. Clean and format
        generated_cypher = clean_cypher_string(raw_cypher)
        generated_result = format_result_to_string(raw_result)

        print(f"Cleaned 'generated_cypher': {generated_cypher}")
        print(f"Formatted 'generated_result': {generated_result}")

        return generated_cypher, generated_result

    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error for question '{question[:50]}...': {e}", file=sys.stderr)
        return pd.NA, pd.NA
    except requests.exceptions.ConnectionError as e:
        print(f"Connection Error for question '{question[:50]}...': {e}", file=sys.stderr)
        print("Check your internet connection or if a firewall is blocking the request.", file=sys.stderr)
        return pd.NA, pd.NA
    except requests.exceptions.Timeout as e:
        print(f"Timeout Error for question '{question[:50]}...': {e}", file=sys.stderr)
        return pd.NA, pd.NA
    except requests.exceptions.RequestException as e:
        print(f"API Error (RequestException) for question '{question[:50]}...': {e}", file=sys.stderr)
        return pd.NA, pd.NA
    except Exception as e:
        print(f"An unexpected error occurred for question '{question[:50]}...': {e}", file=sys.stderr)
        return pd.NA, pd.NA


def main():
    print(f"Loading dataset from {INPUT_CSV_PATH}...")
    try:
        df = pd.read_csv(INPUT_CSV_PATH)
    except FileNotFoundError:
        print(f"Error: Input file not found at {INPUT_CSV_PATH}")
        print("Please update the INPUT_CSV_PATH variable in the script.")
        return
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    if 'question' not in df.columns:
        print("Error: Input CSV must have a 'question' column.")
        return

    print(f"Found {len(df)} questions to process.")

    # --- DEBUGGING: TEST WITH FIRST ROW ONLY ---
    print("\n[DEBUG] Testing with the first question only...")
    first_question = df['question'].iloc[0]
    get_api_data(first_question)
    print("\n[DEBUG] Test finished. Proceeding with all questions in 5 seconds...")
    time.sleep(5)
    # -------------------------------------------

    # This registers pandas with tqdm to show a progress bar
    tqdm.pandas(desc="Processing questions")

    # Use .progress_apply() to show the progress bar while processing
    api_results = df['question'].progress_apply(
        lambda q: pd.Series(get_api_data(q))
    )

    # Assign the new columns back to the original DataFrame
    df['generated_cypher'] = api_results[0]
    df['generated_result'] = api_results[1]

    # Ensure the final column order matches your request
    final_columns = [
        'question',
        'type',
        'original_cypher',
        'original_result',
        'generated_cypher',
        'generated_result'
    ]

    output_df = df[[col for col in final_columns if col in df.columns]]

    # Save the new dataset
    output_df.to_csv(OUTPUT_CSV_PATH, index=False)

    print("\nDone.")
    print(f"New dataset successfully saved to {OUTPUT_CSV_PATH}")


if __name__ == "__main__":
    main()