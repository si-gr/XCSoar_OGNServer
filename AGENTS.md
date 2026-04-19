# AGENTS.md - Development Setup Guide

## Quick Start

```bash
# One-line setup and run (for developers who want to start immediately)
curl -LsSf https://astral.sh/uv/install.sh | sh && source $HOME/.local/bin/env && uv venv .venv --python 3.12 && uv pip install -r requirements.txt && python main.py
```

---

## Prerequisites

### Python Version

- **Required**: Python 3.10 or higher
- **Recommended**: Python 3.12 (matches Docker image)

Check your Python version:
```bash
python --version  # Should show 3.10+
```

### Install uv

**Method 1: Official Installer (Recommended)**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env  # Add to ~/.bashrc or ~/.zshrc for persistence
```

**Method 2: pip (if you already have Python)**
```bash
pip install uv
```

**Method 3: Homebrew (macOS/Linux)**
```bash
brew install uv
```

Verify installation:
```bash
uv --version
```

### Git (Optional)

If cloning the repository:
```bash
git clone <repository-url>
cd XCSoar_OGNServer
```

---

## Installation Steps (Using uv)

### Step 1: Create Virtual Environment

```bash
# Create a virtual environment with Python 3.12
uv venv .venv --python 3.12
```

Alternatively, use system Python:
```bash
uv venv .venv --system
```

### Step 2: Activate Virtual Environment

**Linux/macOS:**
```bash
source .venv/bin/activate
```

**Windows (PowerShell):**
```bash
.venv\Scripts\Activate.ps1
```

**Windows (cmd):**
```bash
.venv\Scripts\activate.bat
```

### Step 3: Install Dependencies

**Option A: Using requirements.txt (Current project setup)**
```bash
uv pip install -r requirements.txt
```

**Option B: Using pyproject.toml (Future migration path)**
```bash
uv sync  # Installs dependencies from pyproject.toml
```

### Step 4: Verify Installation

```bash
python -c "import flask; print(f"Flask version: {flask.__version__}")"
python -c "import telegram; print(f"Telegram bot library loaded")"
```

---

## Development Setup

### Environment Variables

Set these before running the application:

```bash
# Required for local development
export PYTHONPATH=/app  # Or adjust to your working directory
export PYTHONUNBUFFERED=1  # Real-time log output

```

### Configuration Files Setup

Before running the application, create required configuration files:

```bash
# Copy example files
cp names.csv.example names.csv
cp serverdata.txt.example serverdata.txt
cp private.key.example private.key
cp location.txt.example location.txt
cp adminChat.id.example adminChat.id
```

Edit `serverdata.txt`:
```
<access_token>
<host>
<target_latitude>
<target_longitude>
```

Add your Telegram bot token to `private.key` (single line).

### Running the Application

```bash
# Start the server
python main.py
```

The API will be available at `http://localhost:8000`

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=src --cov-report=html

# Run specific test file
pytest tests/test_telegram_bot.py -v
```

### Hot Reload (Development)

For automatic reload during development:
```bash
uv pip install watchdog
python -m watchdog.main.py  # Or use nodemon if available
```

---

## Docker Integration (Optional)

### Current Dockerfile (pip-based)

The existing `Dockerfile` uses `pip install`. This works fine and doesn't require changes.

### Alternative: uv-based Dockerfile

If you prefer using uv in Docker for faster builds:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy requirements
COPY requirements.txt .

# Install dependencies with uv (much faster than pip)
RUN uv pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY main.py .
COPY pyproject.tomol

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "main.py"]
```

### docker-compose Usage

```bash
# Build and start
docker compose up --build

# Run in background
docker compose up --build -d

# View logs
docker compose logs -f

# Stop
docker compose down
```

---

## Commands Reference

| Command | Description | When to Use |
|---------|-------------|-------------|
| `uv venv` | Create virtual environment | Initial setup |
| `uv pip install -r requirements.txt` | Install dependencies from requirements.txt | Standard install |
| `uv sync` | Sync dependencies from pyproject.toml | When using pyproject.toml |
| `uv add <package>` | Add a dependency to pyproject.toml | Adding new packages |
| `uv remove <package>` | Remove a dependency | Removing packages |
| `uv run <command>` | Run command in project environment | Running scripts/tools |
| `uv pip list` | List installed packages | Checking versions |
| `uv pip freeze > requirements.txt` | Export dependencies to requirements.txt | Updating requirements |

---

## Troubleshooting

### uv not found / command not found

**Solution**: Ensure uv is installed and PATH is configured:
```bash
# Check if uv is installed
which uv

# If not found, reinstall:
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

### Python version mismatch

**Error**: `error: No interpreter found for Python 3.12`

**Solution**: Install Python 3.12 or use available version:
```bash
# Install Python 3.12 (Ubuntu/Debian)
sudo apt update && sudo apt install python3.12

# Or use system Python:
uv venv .venv --system
```

### Dependency conflicts

**Error**: Conflicting package versions during install

**Solution**: Use fresh virtual environment:
```bash
rm -rf .venv
uv venv .venv
uv pip install -r requirements.txt
```

### Port already in use

**Error**: Address already in use on port 8000

**Solution**: Kill the process or change port:
```bash
# Find process on port 8000
lsof -i :8000

# Kill it
kill -9 <PID>

# Or modify serverdata.txt to use different port
```

### Permission errors

**Error**: Permission denied when installing packages

**Solution**: Use virtual environment instead of system Python:
```bash
# Don't use --system flag
uv venv .venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### Virtual environment activation fails

**Error**: `No such file or directory: .venv/bin/activate`

**Solution**: Recreate virtual environment:
```bash
rm -rf .venv
uv venv .venv
source .venv/bin/activate
```

---

## Best Practices

1. **Always use virtual environments** - Never install packages globally
2. **Keep requirements.txt updated** - After adding new packages, export them:
   ```bash
   uv pip freeze > requirements.txt
   ```
3. **Use `.gitignore`** - Exclude `.venv`, `__pycache__`, `.pyc` files
4. **Test before committing** - Run tests after any dependency changes
5. **Document new dependencies** - Explain why each package is needed

---

## Migration from pip to uv

If you're used to pip, here's the mapping:

| pip Command | uv Equivalent |
|-------------|---------------|
| `python -m venv .venv` | `uv venv .venv` |
| `pip install -r requirements.txt` | `uv pip install -r requirements.txt` |
| `pip install <package>` | `uv pip install <package>` |
| `pip freeze > requirements.txt` | `uv pip freeze > requirements.txt` |
| `pip list` | `uv pip list` |

**Benefits of uv over pip:**
- 10-30x faster installs
- Better dependency resolution
- Unified tool (no need for virtualenv, pip, etc.)
- Native support for pyproject.toml
- Excellent caching

---

## Support

For issues related to uv:
- [uv GitHub Repository](https://github.com/astral-sh/uv)
- [uv Documentation](https://docs.astral.sh/uv/)

For project-specific issues:
- Check README.md for project documentation
- Review troubleshooting section above
