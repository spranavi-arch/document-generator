# DocGen – Section-based Document Generator

Builds a draft from two sample documents by:

1. **Sectioning** – LLM divides both documents into logical sections (11–12 minimum recommended).
2. **Per-section prompts** – For each section, the LLM generates a prompt that describes how to write that section so it matches the sample, and identifies **required fields** to fill from external data.
3. **Field data** – You provide a CURL command; the app fetches the response (JSON) and maps required fields to values.
4. **Generation** – Each section is generated with its prompt plus the fetched field values, then all sections are assembled into the final draft.

## Setup

- Uses **backend/.env** for Azure OpenAI (or OpenAI) credentials.
- From project root:
  ```bash
  pip install -r docgen/requirements.txt
  streamlit run docgen/app.py
  ```
- Or from `docgen` folder:
  ```bash
  pip install -r requirements.txt
  # Run from project root so backend/.env is found
  cd ..
  streamlit run docgen/app.py
  ```

## UI

- **Upload** two sample documents (.txt or .docx).
- **CURL** (optional): paste a CURL command; the app will run it and use the JSON response to fill section fields.
- **Extra context**: optional case summary or text used when no API or when fields are missing.
- Click **Run pipeline** to section documents, build prompts, fetch API data, generate each section, and assemble the draft. Download the result as .docx.
