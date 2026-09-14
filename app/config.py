import os

from dotenv import load_dotenv


load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://quality_agent:quality_agent@127.0.0.1:3307/quality_agent?charset=utf8mb4",
)
if not DATABASE_URL.startswith("mysql+pymysql://"):
    raise RuntimeError("DATABASE_URL must use the mysql+pymysql:// driver")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
