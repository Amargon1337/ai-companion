import os
import ast
import textwrap

target_dir = r"C:\Games\companion"
output_file = r"C:\Games\exocortex_structure.txt"

def generate_tree(dir_path, prefix=""):
    tree_str = ""
    if not os.path.isdir(dir_path):
        return tree_str
    items = sorted(os.listdir(dir_path))
    items = [i for i in items if i != "__pycache__" and not i.startswith(".")]
    
    for i, item in enumerate(items):
        path = os.path.join(dir_path, item)
        is_last = (i == len(items) - 1)
        connector = "└── " if is_last else "├── "
        tree_str += f"{prefix}{connector}{item}\n"
        
        if os.path.isdir(path):
            extension = "    " if is_last else "│   "
            tree_str += generate_tree(path, prefix + extension)
    return tree_str

def analyze_module(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        source = f.read()
    
    lines_of_code = len(source.splitlines())
    
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {"classes": [], "functions": [], "loc": lines_of_code, "docstring": "Syntax Error"}
    
    docstring = ast.get_docstring(tree)
    
    classes = []
    functions = []
    
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            c_doc = ast.get_docstring(node)
            classes.append({"name": node.name, "doc": c_doc})
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            f_doc = ast.get_docstring(node)
            if not node.name.startswith("_") or node.name == "__init__":
                functions.append({"name": node.name, "doc": f_doc})
            
    return {
        "classes": classes,
        "functions": functions,
        "loc": lines_of_code,
        "docstring": docstring,
        "source": source
    }

def main():
    report = []
    report.append("================================================================================")
    report.append("EXOCORTEX (СЫН) - АРХИТЕКТУРНЫЙ АУДИТ И СТРУКТУРА ПРОЕКТА")
    report.append("================================================================================\n")

    report.append("1. ДЕРЕВО ДИРЕКТОРИЙ")
    report.append("--------------------------------------------------------------------------------")
    report.append("companion/")
    report.append(generate_tree(target_dir))

    report.append("2. СПИСОК МОДУЛЕЙ И ФУНКЦИОНАЛ")
    report.append("--------------------------------------------------------------------------------")

    total_loc = 0
    module_stats = []

    for root, _, files in os.walk(target_dir):
        if "__pycache__" in root or ".git" in root:
            continue
        for file in files:
            if file.endswith((".py", ".json", ".yaml", ".toml")):
                filepath = os.path.join(root, file)
                rel_path = os.path.relpath(filepath, target_dir)
                
                if file.endswith(".py"):
                    analysis = analyze_module(filepath)
                    total_loc += analysis['loc']
                    module_stats.append((rel_path, analysis['loc']))
                    
                    report.append(f"Модуль: {rel_path} ({analysis['loc']} строк)")
                    if analysis['docstring']:
                        doc = " ".join(analysis['docstring'].splitlines())
                        report.append(f"Роль: {textwrap.shorten(doc, width=120, placeholder='...')}")
                    else:
                        if file == "__init__.py":
                            report.append("Роль: Инициализатор пакета.")
                        else:
                            report.append("Роль: [Документация модуля отсутствует]")
                        
                    if analysis['classes']:
                        report.append("  Классы:")
                        for c in analysis['classes']:
                            desc = textwrap.shorten(c['doc'].splitlines()[0] if c['doc'] else "Без описания", width=80)
                            report.append(f"    - {c['name']}: {desc}")
                            
                    if analysis['functions']:
                        report.append("  Ключевые функции:")
                        for f in analysis['functions'][:10]:
                            desc = textwrap.shorten(f['doc'].splitlines()[0] if f['doc'] else "Без описания", width=80)
                            report.append(f"    - {f['name']}: {desc}")
                else:
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        lines = len(f.readlines())
                    report.append(f"Конфиг/Данные: {rel_path} ({lines} строк)")
                report.append("")

    report.append("3. ВЗАИМОСВЯЗИ И ЗАВИСИМОСТИ")
    report.append("--------------------------------------------------------------------------------")
    report.append("АРХИТЕКТУРНЫЕ СЛОИ И ПОТОКИ ДАННЫХ:")
    report.append("1. Точки входа (Entry Points):")
    report.append("   - main.py: Инициализирует бота (Aiogram), поднимает планировщик задач, запускает polling.")
    report.append("   - handlers/chat.py & media.py: Принимают пользовательские сообщения, формируют базовый запрос.")
    report.append("")
    report.append("2. Ядро и Память (Core & Memory):")
    report.append("   - memory/store.py (MemoryStore): Централизованный фасад. Использует storage/sqlite_db.py для SQL-хранения.")
    report.append("   - memory/vector_index.py: Интеграция с FAISS для семантического поиска.")
    report.append("   - memory/rollback.py: Обеспечивает транзакционность и согласованность между SQL и векторным индексом.")
    report.append("")
    report.append("3. Контекст и Рассуждение (Reasoning):")
    report.append("   - bot_core.py: Содержит тяжелую логику агрегации контекста (_load_retrieval_context), работает в thread pool.")
    report.append("   - reasoning.py + storage/sqlite_db.py: Анализируют цели, причинно-следственные связи.")
    report.append("   - llm/pipeline.py: Сборка промптов, вызов LLM (llm/client.py) и обработка structured output.")
    report.append("")
    report.append("4. Модели пользователя и личности (Identity):")
    report.append("   - user_model.py: Анализирует взаимодействие, формирует портрет собеседника.")
    report.append("   - self_model.py: Формирует самосознание бота, его архитектурную и личностную идентичность.")
    report.append("")
    report.append("5. Фоновые процессы (Background Tasks):")
    report.append("   - background_scheduler.py: Регулярное микро-обновление личности и рефлексия без блокировки основного event loop.")
    report.append("")

    report.append("4. СТАТИСТИКА ПРОЕКТА")
    report.append("--------------------------------------------------------------------------------")
    report.append(f"Общий объём Python-кода (в companion/): {total_loc} строк.")
    report.append("Топ-10 самых крупных модулей:")
    module_stats.sort(key=lambda x: x[1], reverse=True)
    for mod, loc in module_stats[:10]:
        report.append(f"  - {mod}: {loc} строк")

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
        
    print(f"Report written to {output_file}")

if __name__ == "__main__":
    main()
