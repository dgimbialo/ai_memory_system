"""Test isolation: no shared embedding cache, no daemon."""
import os

os.environ.setdefault("AI_MEMORY_EMBED_CACHE", "off")
os.environ.setdefault("AI_MEMORY_NO_DAEMON", "1")
