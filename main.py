"""Vercel FastAPI framework preset 진입점.

Vercel 플랫폼 변경(2026-08)으로 vercel.json의 catch-all rewrite가 요청 경로를
destination(/api/index)으로 바꿔버려 전체 라우트가 404 — rewrite 제거하고
프레임워크 프리셋(root main.py의 `app`)으로 전환. api/index.py는 legacy.
"""
import sys
import os

ROOT = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from web.app import app  # noqa: E402,F401  (FastAPI instance)
