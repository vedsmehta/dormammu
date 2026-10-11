"""Vercel Python entry. The Next.js rewrite sends /api/py/* here; routes keep their /api/py prefix."""

from server.app import app

__all__ = ["app"]
