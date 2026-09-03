"""FastAPI app creation, logger configuration and main API routes."""

from production_rag.di import global_injector
from production_rag.launcher import create_app

app = create_app(global_injector)

