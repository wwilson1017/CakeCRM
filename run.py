#!/usr/bin/env python3
"""
CakeCRM — Local launcher.

Run this script to set up and start CakeCRM on your machine.

Usage:
    git clone https://github.com/wwilson1017/CakeCRM.git
    cd CakeCRM
    python run.py
"""

import os
import platform
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
NPM = "npm.cmd" if os.name == "nt" else "npm"


def _python() -> str:
    """Return the path to the venv Python interpreter."""
    if platform.system() == "Windows":
        return str(VENV / "Scripts" / "python.exe")
    return str(VENV / "bin" / "python")


def _pip() -> str:
    """Return the path to the venv pip."""
    if platform.system() == "Windows":
        return str(VENV / "Scripts" / "pip.exe")
    return str(VENV / "bin" / "pip")


def check_python():
    v = sys.version_info
    if v < (3, 10):
        print(f"Error: Python 3.10+ is required (you have {v.major}.{v.minor}).")
        print("Download Python from https://www.python.org/downloads/")
        sys.exit(1)
    print(f"  Python {v.major}.{v.minor}.{v.micro}")


def check_node():
    node = shutil.which("node")
    if not node:
        print("Error: Node.js is not installed.")
        print("Download Node.js from https://nodejs.org/")
        sys.exit(1)
    result = subprocess.run([node, "--version"], capture_output=True, text=True)
    version = result.stdout.strip().lstrip("v")
    major = int(version.split(".")[0])
    if major < 18:
        print(f"Error: Node.js 18+ is required (you have {version}).")
        print("Download Node.js from https://nodejs.org/")
        sys.exit(1)
    print(f"  Node.js {version}")


def setup_env():
    if ENV_FILE.exists():
        print("  .env file already exists, skipping.")
        return
    if ENV_EXAMPLE.exists():
        content = ENV_EXAMPLE.read_text(encoding="utf-8")
    else:
        content = (
            "AUTH_PASSWORD=changeme\n"
            "JWT_SECRET=change-me-in-production\n"
        )
    # Auto-generate a secure JWT secret
    content = content.replace("change-me-in-production", secrets.token_hex(32))
    # Prompt for password
    print()
    password = input("  Choose a login password (or press Enter for 'changeme'): ").strip()
    if password:
        # Quote the value to handle special characters (=, #, spaces)
        safe_password = password.replace("'", "'\\''")
        content = content.replace("AUTH_PASSWORD=changeme", f"AUTH_PASSWORD='{safe_password}'")
    ENV_FILE.write_text(content, encoding="utf-8")
    print("  Created .env file.")


def setup_venv():
    if VENV.exists():
        print("  Virtual environment already exists, skipping.")
        return
    print("  Creating virtual environment...")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    print("  Installing Python dependencies...")
    subprocess.run(
        [_pip(), "install", "-r", str(BACKEND / "requirements.txt")],
        check=True,
    )
    (VENV / "deps_installed").touch()


def install_deps():
    """Install pip deps if requirements have changed."""
    if VENV.exists() and (VENV / "deps_installed").exists():
        # Check if requirements.txt is newer than our marker
        req_mtime = (BACKEND / "requirements.txt").stat().st_mtime
        marker_mtime = (VENV / "deps_installed").stat().st_mtime
        if req_mtime <= marker_mtime:
            print("  Python dependencies up to date.")
            return
    print("  Installing Python dependencies...")
    subprocess.run(
        [_pip(), "install", "-r", str(BACKEND / "requirements.txt")],
        check=True,
    )
    (VENV / "deps_installed").touch()


def _database_url() -> str:
    """Read DATABASE_URL from the environment or the .env file."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return "postgresql://cakecrm:cakecrm_dev@localhost:5432/cakecrm"


def _postgres_reachable(url: str, timeout: float = 2.0) -> bool:
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_postgres():
    """Make sure PostgreSQL is reachable, starting docker compose if needed."""
    import time

    url = _database_url()
    if _postgres_reachable(url):
        print("  PostgreSQL is reachable.")
        return

    if shutil.which("docker"):
        print("  PostgreSQL not reachable — starting docker compose...")
        subprocess.run(["docker", "compose", "up", "-d"], cwd=str(ROOT), check=True)
        for _ in range(30):
            if _postgres_reachable(url):
                print("  PostgreSQL is up.")
                return
            time.sleep(1)
        print("Error: PostgreSQL did not become reachable after docker compose up.")
        sys.exit(1)

    print("Error: PostgreSQL is not reachable and Docker is not installed.")
    print("CakeCRM requires PostgreSQL. Either:")
    print("  - Install Docker Desktop (https://docker.com) and re-run: python run.py")
    print("  - Or point DATABASE_URL in .env at an existing PostgreSQL server.")
    sys.exit(1)


def setup_frontend():
    if not (FRONTEND / "node_modules").exists():
        print("  Installing frontend dependencies...")
        subprocess.run([NPM, "ci"], cwd=str(FRONTEND), check=True)
    else:
        print("  Frontend dependencies already installed.")

    print("  Building frontend...")
    subprocess.run([NPM, "run", "build"], cwd=str(FRONTEND), check=True)


def start_server():
    print()
    print("=" * 50)
    print("  CakeCRM is running at http://localhost:8000")
    print("  Press Ctrl+C to stop.")
    print("=" * 50)
    print()
    subprocess.run(
        [
            _python(), "-m", "uvicorn",
            "main:app",
            "--host", "127.0.0.1",
            "--port", "8000",
        ],
        cwd=str(BACKEND),
    )


def main():
    print()
    print("CakeCRM — Local Setup")
    print("=" * 50)

    print("\nChecking prerequisites...")
    check_python()
    check_node()

    print("\nSetting up environment...")
    setup_env()

    print("\nSetting up Python...")
    setup_venv()
    install_deps()

    print("\nChecking PostgreSQL...")
    ensure_postgres()

    print("\nSetting up frontend...")
    setup_frontend()

    start_server()


if __name__ == "__main__":
    main()
