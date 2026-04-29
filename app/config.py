import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

    #Database 
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = os.getenv("DB_PORT", "5432")
    DB_NAME: str = os.getenv("DB_NAME", "postgres")
    DB_USER = os.getenv("DB_USER", 'postgres')
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "postgres")

    #Active 
    
