# brawlcommon/__init__.py
"""Shared utilities for TLGBS cogs. Not a cog by itself."""

__all__ = []

# If someone tries to load this as a Red extension, do nothing (no-op).
async def setup(bot):
    return
