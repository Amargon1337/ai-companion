import ast
import os
import json
from collections import defaultdict
import re

def analyze_project(root_dir):
    results = {
        "files": {},
        "imports": defaultdict(list),
        "classes": defaultdict(list),
        "functions": defaultdict(list),
        "calls": defaultdict(list),
        "todos": [],
        "not_implemented": []
    }

    for dirpath, _, filenames in os.walk(root_dir):
        if '__pycache__' in dirpath: continue
        for filename in filenames:
            if not filename.endswith('.py'): continue
            filepath = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(filepath, root_dir)
            
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.split('\n')
                
            for i, line in enumerate(lines):
                if 'TODO' in line or 'FIXME' in line:
                    results["todos"].append({"file": rel_path, "line": i+1, "content": line.strip()})
                if 'NotImplementedError' in line or 'pass' == line.strip():
                    results["not_implemented"].append({"file": rel_path, "line": i+1, "content": line.strip()})
                    
            try:
                tree = ast.parse(content)
            except Exception as e:
                print(f"Error parsing {filepath}: {e}")
                continue
                
            results["files"][rel_path] = {"size": len(content), "classes": [], "functions": [], "imports": []}
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        results["files"][rel_path]["imports"].append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        results["files"][rel_path]["imports"].append(node.module)
                elif isinstance(node, ast.ClassDef):
                    results["files"][rel_path]["classes"].append(node.name)
                    results["classes"][node.name].append(rel_path)
                elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                    results["files"][rel_path]["functions"].append(node.name)
                    results["functions"][node.name].append(rel_path)
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        results["calls"][node.func.id].append(rel_path)
                    elif isinstance(node.func, ast.Attribute):
                        results["calls"][node.func.attr].append(rel_path)
                        
    with open(os.path.join(root_dir, 'audit_ast_results.json'), 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    analyze_project('c:\\Games\\companion')
