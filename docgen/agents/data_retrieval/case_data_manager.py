import os
import json
from pathlib import Path
from typing import Any

class CaseDataManager:
    """
    Manages loading and saving cached case facts and extracted fields.
    Stores data in: <project_root>/case_facts/<firm_id>/<case_id>/key-facts.json
    """

    def __init__(self, base_dir: str | Path | None = None):
        if base_dir is None:
            # Default to project_root/case_facts
            root = Path(__file__).resolve().parent.parent.parent.parent
            self.base_dir = root / "case_facts"
        else:
            self.base_dir = Path(base_dir)

    def _get_path(self, firm_id: str | int, case_id: str | int) -> Path:
        # Sanitize inputs to prevent directory traversal
        f_id = str(firm_id).replace("/", "_").replace("\\", "_")
        c_id = str(case_id).replace("/", "_").replace("\\", "_")
        
        folder = self.base_dir / f_id / c_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / "key-facts.json"

    def load_case_data(self, firm_id: str | int, case_id: str | int) -> dict[str, Any]:
        """
        Returns a dict with 'fields' (dict) and 'facts' (list).
        """
        path = self._get_path(firm_id, case_id)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Ensure correct structure
                    return {
                        "fields": data.get("fields", {}),
                        "facts": data.get("facts", [])
                    }
            except Exception as e:
                print(f"[CaseDataManager] Error loading case data from {path}: {e}")
                
        return {"fields": {}, "facts": []}

    def save_case_data(self, firm_id: str | int, case_id: str | int, fields: dict[str, Any], facts: list[str]) -> None:
        """
        Merges new fields and facts with existing cached data and saves.
        """
        path = self._get_path(firm_id, case_id)
        
        existing = self.load_case_data(firm_id, case_id)
        
        # Merge fields
        merged_fields = existing.get("fields", {})
        merged_fields.update(fields)
        
        # Merge and deduplicate facts
        merged_facts = existing.get("facts", [])
        fact_set = set(merged_facts)
        for fact in facts:
            if fact and fact not in fact_set:
                merged_facts.append(fact)
                fact_set.add(fact)
                
        data = {
            "fields": merged_fields,
            "facts": merged_facts
        }
        
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"[CaseDataManager] Successfully saved data to {path}")
        except Exception as e:
            print(f"[CaseDataManager] Error saving case data: {e}")
