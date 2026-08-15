"""Reading and writing an Obsidian vault as a first-class data store."""

from .note import Note, dump_frontmatter, parse_frontmatter, slugify
from .vault import Vault
from .graph import VaultGraph

__all__ = [
    "Note",
    "Vault",
    "VaultGraph",
    "parse_frontmatter",
    "dump_frontmatter",
    "slugify",
]
