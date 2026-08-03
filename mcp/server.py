import os
import subprocess
from pathlib import Path
from mcp.server.fastmcp import FastMCP

# ------------------------------------------------------------------
# 1. Фиксация рабочей директории проекта
# Скрипт находится в C:\Games\mcp\server.py,
# поэтому родительская папка на уровень выше — это C:\Games
# ------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)

# 2. Инициализация MCP сервера
mcp = FastMCP("Amargon's Void MCP", host="127.0.0.1", port=8000)


# ------------------------------------------------------------------
# Инструменты (Tools)
# ------------------------------------------------------------------

@mcp.tool()
def git_status() -> str:
    """Показать краткое состояние git-репозитория."""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return result.stdout if result.stdout else "Репозиторий чист."
    except Exception as e:
        return f"Ошибка при выполнении git status: {e}"


@mcp.tool()
def git_diff() -> str:
    """Показать текущий git diff (изменения в файлах)."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return (
            result.stdout[:30000]
            if result.stdout
            else "Нет незафиксированных изменений."
        )
    except Exception as e:
        return f"Ошибка при выполнении git diff: {e}"


@mcp.tool()
def run_tests() -> dict:
    """Запустить тесты проекта через pytest."""
    try:
        result = subprocess.run(
            ["pytest", "-q"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[-10000:],
            "stderr": result.stderr[-5000:],
        }
    except Exception as e:
        return {"error": f"Ошибка при запуске pytest: {e}"}


@mcp.tool()
def project_tree(max_depth: int = 3) -> str:
    """Получить структуру проекта без использования внешних команд.

    Автоматически игнорирует системные директории, виртуальные окружения и кэш.
    """
    root_dir = Path.cwd()

    # Папки, которые полностью скрываются из дерева
    ignore_set = {
        "__pycache__",
        ".git",
        ".pytest_cache",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        ".claude",
        "graphify-out",
    }

    tree_lines = [f"{root_dir.name}/"]

    def build_branch(directory: Path, prefix: str = "", depth: int = 1):
        if depth > max_depth:
            return

        try:
            entries = [
                e
                for e in directory.iterdir()
                if e.name not in ignore_set and not e.name.endswith(".pyc")
            ]
        except PermissionError:
            return

        entries = sorted(
            entries, key=lambda x: (not x.is_dir(), x.name.lower())
        )
        total = len(entries)

        for index, entry in enumerate(entries):
            is_last = index == (total - 1)
            connector = "└── " if is_last else "├── "

            display_name = f"{entry.name}/" if entry.is_dir() else entry.name
            tree_lines.append(f"{prefix}{connector}{display_name}")

            if entry.is_dir():
                indent = "    " if is_last else "│   "
                build_branch(entry, prefix=prefix + indent, depth=depth + 1)

    build_branch(root_dir, depth=1)
    return "\n".join(tree_lines)


# ------------------------------------------------------------------
# Запуск сервера
# ------------------------------------------------------------------
if __name__ == "__main__":
    # Вариант А: Если подключаете к веб-версии Gemini Spark через ngrok:
    mcp.run(transport="sse")

    # Вариант Б: Если используете локальный клиент/IDE (в stdio режиме):
    # mcp.run()