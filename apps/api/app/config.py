from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./dispatch_intelligence.db"
    cors_origins: str = "http://localhost:3000,http://localhost:5173"
    example_data_dir: Path = Path("example data")
    vehicle_compatibility_mode: str = "EXACT_MATCH"
    default_geofence_radius_m: float = 125.0
    minimum_gps_dwell_minutes: float = 5.0
    minimum_gps_event_count: int = 2
    ml_artifact_dir: Path = Path("./ml_artifacts")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
