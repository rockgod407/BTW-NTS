"""Minimal setup.py shim for compatibility with old pip/setuptools.

All metadata lives in setup.cfg and pyproject.toml. This file exists
solely so that pip versions older than 21.3 (which don't fully support
PEP 621 / pyproject.toml [project] tables) can still build and install
the package correctly.
"""
from setuptools import setup

setup()
