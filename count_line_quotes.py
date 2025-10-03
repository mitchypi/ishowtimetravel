from pathlib import Path
line = Path('app.py').read_text(encoding='utf-8').splitlines()[1095]
print(line)
print(line.count('"""'))
