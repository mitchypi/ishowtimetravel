from pathlib import Path
text = Path('app.py').read_text(encoding='utf-8')
print(text.count('"""'))
