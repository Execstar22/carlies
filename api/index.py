# =============================================================================
# Vercel Serverless Entry Point — Flask Dashboard
# =============================================================================

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["VERCEL"] = "1"

from app_vercel import app

