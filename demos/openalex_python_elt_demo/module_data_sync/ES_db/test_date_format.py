"""Tests for date formatting used by ES indexer."""
import os, sys
from datetime import datetime

# Ensure repo root is on sys.path when running tests directly
import importlib.util

# Load indexer module directly to avoid package import issues in test runner
indexer_path = os.path.abspath(os.path.join(os.path.dirname(__file__), 'indexer.py'))
spec = importlib.util.spec_from_file_location('es_indexer', indexer_path)
es_indexer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(es_indexer)
_format_datetime = es_indexer._format_datetime


def test_format_datetime_space():
    dt = datetime(2025, 12, 11, 16, 28, 11)
    assert _format_datetime(dt) == '2025-12-11 16:28:11'


def test_format_datetime_non_datetime():
    assert _format_datetime(None) is None
    assert _format_datetime('2025-12-11T16:28:11') == '2025-12-11T16:28:11'
