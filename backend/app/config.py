from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env.local", extra="ignore"
    )
    database_url: str = ""
    app_password: str = ""
    app_origin: str = "http://localhost:3000"
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "gemma3:4b-it-q4_K_M"
    gemini_api_key: str = ""
    gemini_model: str = ""
    enable_cloud: bool = False
    context_window: int = 8192
    max_output_tokens: int = 1024
    total_timeout: float = 300
    first_token_timeout: float = 60
    idle_timeout: float = 30
    memory_timeout: float = 120
    owner_id: str = "00000000-0000-4000-8000-000000000001"

    def configured(self) -> bool:
        return bool(self.database_url and len(self.app_password) >= 16)
