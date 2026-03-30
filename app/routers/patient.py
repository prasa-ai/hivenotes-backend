"""
patient.py — DEPRECATED.

Patient session upload functionality has moved to app/routers/sessions.py.
  Upload : POST /api/v1/sessions/upload

This module is kept only to avoid import errors during the transition.
"""
from fastapi import APIRouter

router = APIRouter()

