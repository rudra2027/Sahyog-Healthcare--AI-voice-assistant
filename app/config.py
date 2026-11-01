"""Settings.

Every knob lives in ONE place and comes from the `.env` file (or real environment
variables). The rest of the code does `from app.config import settings`.
"""
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Copy everything in .env into os.environ. LangSmith reads its own settings from
# os.environ, so this one line is what makes tracing work without extra code.
load_dotenv()


class Settings(BaseSettings):
    """Typed settings. Pydantic checks the types for us (e.g. "30" becomes 30)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM (OpenRouter) ---
    openrouter_api_key: str = ""
    openrouter_model: str = "openai/gpt-6-sol"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    llm_mode: str = "auto"  # auto | fake | openrouter
    fake_llm_delay_ms: int = 0  # pretend the fake model is slow (useful in load tests)

    # --- LangSmith ---
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "sahyog-care"

    # --- Supabase (real sign-in). Both values are safe to expose: the publishable key is public by design ---
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    doctor_signup_code: str = ""     # staff sign-up: a doctor must type this code when creating an account (empty = doctors cannot self-register)
    frontdesk_signup_code: str = ""  # the same for front-desk staff
    allow_demo_login: bool = False  # True lets the X-User-Id demo header act as anyone: for local demos and tests ONLY

    # --- Memory and caching ---
    redis_url: str = ""  # empty = keep everything in RAM
    slot_cache_seconds: int = 30  # how long slot availability stays cached
    promote_after: int = 2  # repeats before a learned lesson goes to the doctor for approval

    # --- Voice ---
    stt_backend: str = "auto"  # auto = Gemini when GOOGLE_API_KEY is set, else whisper | gemini | whisper (on this machine) | browser
    whisper_model: str = "base.en"  # tiny.en is faster, small.en is more accurate
    google_api_key: str = ""  # for Gemini speech-to-text
    gemini_model: str = "gemini-2.5-flash"
    tts_backend: str = "edge"  # edge | phonon
    ollama_host: str = "http://localhost:11434"

    # --- Storage ---
    database_path: str = "data/sahyog.db"


@lru_cache
def get_settings() -> Settings:
    """Build the settings once and reuse them (lru_cache = remember the result)."""
    return Settings()


settings = get_settings()
