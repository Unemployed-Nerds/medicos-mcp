FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY mcp_server/ ./mcp_server/

# Create non-root user for security
RUN useradd -m -u 1000 mcpuser && \
    chown -R mcpuser:mcpuser /app

USER mcpuser

# Expose port for HTTP transport
EXPOSE 8000

# Default command: run MCP server over stdio
CMD ["python", "-m", "mcp_server.main"]
