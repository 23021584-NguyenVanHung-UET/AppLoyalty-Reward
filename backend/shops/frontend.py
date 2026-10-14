"""Resolve Vite's generated files without a Node process at request time."""
import json
from pathlib import PurePosixPath

from django.conf import settings


def manifest_assets():
    try:
        manifest = json.loads((settings.FRONTEND_DIST_DIR / ".vite" / "manifest.json").read_text(encoding="utf-8"))
        scripts, styles, preloads, seen = [], [], [], set()

        def safe_path(value):
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or not value.startswith("assets/"):
                raise ValueError("Invalid asset")
            return "/app-static/" + value

        def visit(name):
            if name in seen:
                return
            seen.add(name)
            chunk = manifest[name]
            for value in chunk.get("css", []):
                url = safe_path(value)
                if url not in styles:
                    styles.append(url)
            for dependency in chunk.get("imports", []):
                preloads.append(safe_path(manifest[dependency]["file"]))
                visit(dependency)

        scripts.append(safe_path(manifest["src/main.ts"]["file"]))
        visit("src/main.ts")
        return dict(scripts=scripts, styles=styles, preloads=list(dict.fromkeys(preloads)), build_ready=True)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return dict(scripts=[], styles=[], preloads=[], build_ready=False)
