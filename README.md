## Medicos MCP Backend

Python MCP server backend for the Hospital Medicine Reminder app.

### Overview

- **Protocol**: Model Context Protocol (MCP), Python implementation
- **Responsibilities**:
  - Expose controlled tools for Firebase, OCR/parsing, medical intelligence, scheduling, notifications, adherence, and governance.
  - Enforce ArmorIQ intent checks and auditing for all sensitive operations.
  - Persist all application data in Firebase (Firestore + Storage) and send notifications via FCM.

### High-Level Layout

- `mcp_server/`
  - `main.py` – MCP server entrypoint and tool registration.
  - `config.py` – Environment-driven configuration (Firebase, ArmorIQ, LLM, etc.).
  - `firebase_client.py` – Firebase Admin SDK initialization and helpers.
  - `armor_iq_client.py` – ArmorIQ REST client for `policy.check_intent` and `audit.log`.
  - `llm_client.py` – Wrapper for LLM provider(s) used by parsing/medical/scheduling tools.
  - `models/` – Pydantic models for tool inputs/outputs and shared context.
  - `tools/` – Implementation of MCP tools, grouped by namespace.

### Runtime Expectations

- Python 3.10+ recommended.
- MCP client (e.g. orchestrator agent) connects to this server over stdio or another supported transport.
- The server is designed to run on your VPS, separate from the Flutter app and hospital dashboard.

### Environment Variables

The server is configured via environment variables (all optional defaults documented in `config.py`):

- `MEDICOS_ENV` – `dev` or `prod` (default: `dev`).
- `MEDICOS_FIREBASE_PROJECT_ID` – Firebase project ID.
- `MEDICOS_FIREBASE_CREDENTIALS_FILE` – Path to Firebase service account JSON (if not using ADC).
- `MEDICOS_ARMORIQ_API_KEY` – API key for ArmorIQ (SDK handles endpoints internally based on `MEDICOS_ENV`).
- `MEDICOS_LLM_PROVIDER` – LLM provider identifier (e.g. `openai`, `anthropic`).
- `MEDICOS_LLM_API_KEY` – API key for the chosen LLM provider.

You can also use a local `.env` file in the repository root during development; see notes in `config.py`.

### Getting Started

#### Option 1: Virtual Environment (Development)

**Setup:**
```bash
./setup.sh          # Creates venv and installs dependencies
source .venv/bin/activate
```

**Or manually:**
```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install --upgrade pip setuptools wheel
pip install -r requirements.txt
```

**Configure Environment:**
1. Copy `env.example` to `.env` and fill in your values:
   ```bash
   cp env.example .env
   # Edit .env with your actual values
   ```

2. Place your Firebase service account JSON in `./credentials/firebase-service-account.json`

**Run the Server:**
```bash
python -m mcp_server.main
```

#### Option 2: Docker Compose (Production/VPS)

**Prerequisites:**
- Docker and Docker Compose installed
- Firebase service account JSON file

**Setup:**
1. Create credentials directory and add Firebase service account:
   ```bash
   mkdir -p credentials
   cp /path/to/your/firebase-service-account.json credentials/
   ```

2. Create `.env` file:
   ```bash
   cp env.example .env
   # Edit .env with your actual values
   ```

3. Build and run:
   ```bash
   docker-compose build
   docker-compose up -d
   docker-compose logs -f mcp-server
   ```

**Stop the container:**
```bash
docker-compose down
```

**Docker Configuration:**
- The container runs as a non-root user (`mcpuser`)
- Firebase credentials are mounted read-only from `./credentials/`
- Environment variables can be set in `.env` or `docker-compose.yml`
- The server runs over stdio (for MCP clients that support it)

### Transport Modes

The MCP server supports two transport modes:

#### 1. Stdio Transport (Default)
For direct process-to-process communication (e.g., Cursor, Claude Desktop):

```json
{
  "mcpServers": {
    "medicos": {
      "command": "python",
      "args": ["-m", "mcp_server.main"],
      "env": {
        "MEDICOS_FIREBASE_PROJECT_ID": "...",
        "MEDICOS_TRANSPORT": "stdio"
      }
    }
  }
}
```

#### 2. HTTP Transport (Reverse Proxy)
For deployment behind a reverse proxy (e.g., `mcp.p1ng.me`):

1. **Set environment variable:**
   ```bash
   MEDICOS_TRANSPORT=http
   MEDICOS_SERVER_PORT=8000
   ```

2. **Run the server:**
   ```bash
   python -m mcp_server.main
   # Server listens on http://0.0.0.0:8000
   ```

3. **Configure reverse proxy** (using Caddy, nginx, or similar):
   - Point `mcp.p1ng.me` to your VPS
   - Proxy `/mcp/stream` and `/mcp/message` to `http://localhost:8000`
   - Set up SSL/TLS certificates

4. **Connect clients to:** `https://mcp.p1ng.me/mcp/stream`

### Troubleshooting

- **Import errors**: Make sure dependencies are installed (`pip install -e .`)
- **Firebase errors**: Verify service account JSON path and permissions
- **ArmorIQ errors**: Check base URL and API key
- **LLM errors**: Verify API key and provider name
- **Docker issues**: Ensure credentials directory exists and contains valid JSON

