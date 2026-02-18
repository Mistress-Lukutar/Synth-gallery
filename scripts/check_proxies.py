#!/usr/bin/env python3
"""Check which functions are simple proxies"""
import re

with open('app/database.py', 'r') as f:
    content = f.read()

# Find all function definitions
funcs = re.findall(r'^def (\w+)\(', content, re.MULTILINE)

simple_proxies = []
complex_funcs = []

for func in funcs:
    # Find the function body
    pattern = rf'def {func}\([^)]*\):\s+(.*?)(?=\n\ndef |\nclass |\Z)'
    match = re.search(pattern, content, re.DOTALL)
    if match:
        body = match.group(1)
        # Check if it's a simple proxy (contains only docstring and return _get_*_repo)
        lines = [l.strip() for l in body.split('\n') if l.strip()]
        if len(lines) <= 2 and any('_get_' in l and '_repo()' in l for l in lines):
            simple_proxies.append(func)
        elif 'def ' not in body and body.strip():
            complex_funcs.append(func)

print('=== SIMPLE PROXIES ===')
for f in simple_proxies:
    print(f'  - {f}')

print(f'\n=== COMPLEX FUNCTIONS (need migration) ===')
for f in sorted(complex_funcs):
    if not f.startswith('_'):
        print(f'  - {f}')
