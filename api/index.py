"""Vercel Python serverless 진입점 — FastAPI 앱을 가져와서 export."""
import sys
import os

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from web.app import app  # FastAPI instance
