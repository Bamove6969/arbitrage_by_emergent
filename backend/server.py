import sys
from pathlib import Path

from dotenv import load_dotenv

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env")
sys.path.insert(0, str(_here.parent))

from backend.main import app  # noqa: E402,F401
