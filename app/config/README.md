# app/config/

Holds only the environment variable template (`.env.example`). It does not contain configuration logic.

The actual config loader lives in `app/infrastructure/config.py` — it reads `.env` (via `python-dotenv`) and exposes typed accessors. See `.env.example` for the documented keys.
