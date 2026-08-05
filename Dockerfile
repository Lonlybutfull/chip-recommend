FROM python:3.13-slim

WORKDIR /app

# Install uv + Python deps via domestic mirrors (for servers in China)
COPY requirements.txt .
RUN pip install --no-cache-dir uv -i https://pypi.tuna.tsinghua.edu.cn/simple && \
    uv pip install --system --no-cache -r requirements.txt \
      --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# Copy application package
COPY chip_model/ ./chip_model/

# Copy schema and static assets
COPY schema.sql .
COPY static/ ./static/

# Copy skills so the chat agent can enumerate project capabilities
COPY .claude/skills/ .claude/skills/

# Copy data files
COPY data/ ./data/

# Copy entry scripts
COPY scripts/ ./scripts/

# Point to the bundled SQLite database
ENV DATA_DB_PATH=/app/data/data.db

EXPOSE 8000

# Default: serve API. Use 'python scripts/run_cli.py --help' for CLI mode.
CMD ["python", "scripts/run_server.py"]
