from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    plex_url: str = Field(default="http://localhost:32400", env="PLEX_URL")
    plex_token: str = Field(default="", env="PLEX_TOKEN")
    plex_music_section: str = Field(default="Music", env="PLEX_MUSIC_SECTION")
    music_library_path: str = Field(default="", env="MUSIC_LIBRARY_PATH")
    port: int = Field(default=8766, env="PORT")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
