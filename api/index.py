"""Vercel's Python function entrypoint for the FastAPI application."""

from app.main import app

__all__ = ["app"]
