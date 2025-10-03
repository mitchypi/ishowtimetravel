from pathlib import Path
import re
text = Path('app.py').read_text(encoding='utf-8')
positions = [m.start() for m in re.finditer('"""', text)]
print(len(positions))
for idx, pos in enumerate(positions, start=1):
    status = 'open' if idx % 2 else 'close'
    segment = text[pos-20:pos+20]
    segment = segment.replace('\n', '\\n')
    print(idx, status, segment)
