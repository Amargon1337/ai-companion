import json

with open('c:\\Games\\companion\\audit_ast_results.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

with open('c:\\Games\\ast_summary.md', 'w', encoding='utf-8') as f:
    f.write("=== POTENTIAL DEAD CLASSES ===\n")
    for cls, files in data['classes'].items():
        if cls not in data['calls']:
            f.write(f"Class {cls} defined in {files} might be unused.\n")

    f.write("\n=== POTENTIAL DEAD FUNCTIONS ===\n")
    for func, files in data['functions'].items():
        if func.startswith('__') and func.endswith('__'): continue
        if func not in data['calls']:
            f.write(f"Function {func} defined in {files} might be unused.\n")

    f.write("\n=== TODOs & FIXMEs ===\n")
    for t in data['todos']:
        f.write(f"{t['file']}:{t['line']} -> {t['content']}\n")

    f.write("\n=== NOT IMPLEMENTED / PASS ===\n")
    for t in data['not_implemented']:
        f.write(f"{t['file']}:{t['line']} -> {t['content']}\n")

