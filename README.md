# AI Document Vault Agent

A Streamlit app where users sign up, upload personal documents, and ask an AI
agent to retrieve them by name ("give me my passport"). Each user can only see
their own files.

## Run locally

```bash
cd document_agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # Windows: copy .env.example .env
# optional: put your OPENAI_API_KEY in .env
streamlit run app.py
```

Open http://localhost:8501. The database (`data/app.db`) is created on first run.
Without an API key the agent still works using filename matching.

## Structure

| File | Responsibility |
|---|---|
| `app.py` | Streamlit UI only |
| `auth.py` | Sign up / login, bcrypt hashing |
| `database.py` | SQLite connection + schema |
| `document_manager.py` | Upload validation, metadata, `StorageBackend` interface |
| `agent.py` | Intent cleanup, filename matching, optional LLM re-ranking |

## How the agent works

1. If the message contains an exact filename (`open resume.pdf`) -> return it.
2. Strip filler words ("give me my ... document"), score the user's filenames
   (token match, typos, plurals, small synonym list). A single clear winner is returned.
3. Several close matches -> shown with "Choose" buttons.
4. Weak/no match -> if `OPENAI_API_KEY` is set, the LLM ranks the user's
   `{id, filename}` list. IDs are validated against the user's real documents.
5. Otherwise: "Sorry, I couldn't find a document matching '...'".

The LLM never receives file contents, paths, or other users' data.

## Security notes

- bcrypt password hashes; generic "invalid username/email or password" message.
- Every document query includes `user_id` from the server-side session.
- Files stored as `uploads/user_<id>/<random-hex>.<ext>`; original names live only
  in the DB and are sanitised. Storage keys are regex-validated and resolved paths
  are checked, so `../../x` cannot escape the user's folder.
- Extension allow-list, size limit, and magic-byte content check.
- Paths are never shown in the UI.

## Known MVP limitations

- Login lasts for the browser tab session (Streamlit session state); refreshing logs out.
- No login rate limiting or email verification yet.
- Download buttons load file bytes into memory (fine for <=10 MB files).
- Run behind HTTPS in production.

## Extending

- Cloud storage: implement `StorageBackend` (save/read/delete) for S3/Supabase/Firebase and select it in `get_storage()`.
- Vector search / OCR: add a module that indexes extracted text and call it from `agent.handle_request`.
