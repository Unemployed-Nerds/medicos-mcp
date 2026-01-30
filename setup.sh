#!/bin/bash
# Setup script for Medicos MCP Backend
# Creates a virtual environment and installs dependencies

set -e

echo "🚀 Setting up Medicos MCP Backend..."

# Check Python version
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.10 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
REQUIRED_VERSION="3.10"

if [ "$(printf '%s\n' "$REQUIRED_VERSION" "$PYTHON_VERSION" | sort -V | head -n1)" != "$REQUIRED_VERSION" ]; then
    echo "❌ Python 3.10+ required. Found: $PYTHON_VERSION"
    exit 1
fi

echo "✅ Python version: $(python3 --version)"

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
else
    echo "✅ Virtual environment already exists"
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source .venv/bin/activate

# Upgrade pip
echo "⬆️  Upgrading pip..."
pip install --upgrade pip setuptools wheel

# Install dependencies
echo "📥 Installing dependencies..."
pip install -r requirements.txt

echo ""
echo "✅ Setup complete!"
echo ""
echo "To activate the virtual environment, run:"
echo "  source .venv/bin/activate"
echo ""
echo "To run the MCP server:"
echo "  python -m mcp_server.main"
echo ""
echo "Don't forget to set your environment variables or create a .env file!"
