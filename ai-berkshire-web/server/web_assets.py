"""Version HTML asset references so cached scripts cannot outlive their markup."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re


def render_index(static_dir: Path) -> tuple[str, str]:
    source = (static_dir / 'index.html').read_text(encoding='utf-8')
    versions = {}

    def version(match):
        prefix, url = match.groups()
        path = url.split('?', 1)[0]
        if path not in versions:
            target = (static_dir / path.removeprefix('/static/')).resolve()
            target.relative_to(static_dir.resolve())
            versions[path] = hashlib.sha256(target.read_bytes()).hexdigest()[:12]
        return prefix + path + '?v=' + versions[path] + '"'

    html = re.sub(r'((?:src|href)=")(/static/[^"#]+)"', version, source)
    return html, '"' + hashlib.sha256(html.encode()).hexdigest() + '"'
