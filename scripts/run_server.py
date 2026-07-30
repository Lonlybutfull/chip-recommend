#!/usr/bin/env python3
"""Launch the AISHPerf FastAPI server.

Usage:
    python scripts/run_server.py
    python scripts/run_server.py --port 8080
    python scripts/run_server.py --db-path /path/to/data.db
"""
import sys
import os

# Ensure project root is on sys.path so chip_model is importable
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

if __name__ == "__main__":
    import uvicorn
    from chip_model.server import app

    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
