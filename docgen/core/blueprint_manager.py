import json
from pathlib import Path

class BlueprintManager:
    def __init__(self, name: str, root_dir: Path):
        self.name = name.strip()
        self.dir = root_dir / "blueprints" / self.name
        # Do not create dir here, wait until save is called or check exists
        
    def ensure_dir(self):
        self.dir.mkdir(parents=True, exist_ok=True)

    def exists(self):
        return self.dir.exists()

    def save_bytes(self, filename, data):
        self.ensure_dir()
        with open(self.dir / filename, "wb") as f:
            f.write(data)

    def load_bytes(self, filename):
        p = self.dir / filename
        if p.exists():
            with open(p, "rb") as f:
                return f.read()
        return None

    def save_text(self, filename, content):
        self.ensure_dir()
        with open(self.dir / filename, "w", encoding="utf-8") as f:
            f.write(str(content))

    def load_text(self, filename):
        p = self.dir / filename
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
        return None

    def save_json(self, filename, data):
        self.ensure_dir()
        with open(self.dir / filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_json(self, filename):
        p = self.dir / filename
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def has_file(self, filename):
        return (self.dir / filename).exists()
