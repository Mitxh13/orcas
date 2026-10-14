import os
import yaml
from typing import Any, Dict, Optional


class PromptLoader:
    """Loads and formats versioned prompt YAML artifacts from the prompts/ directory."""
    def __init__(self, prompts_root: Optional[str] = None):
        if not prompts_root:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.prompts_root = base_dir
        else:
            self.prompts_root = prompts_root
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get_prompt(self, name: str, version: str = "v1") -> Dict[str, Any]:
        key = f"{name}:{version}"
        if key in self._cache:
            return self._cache[key]

        file_path = os.path.join(self.prompts_root, name, f"{version}.yaml")
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Prompt artifact not found at: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            self._cache[key] = data
            return data

    def render(self, name: str, version: str = "v1", **kwargs) -> str:
        """Loads template and safely interpolates keyword arguments without colliding with JSON braces."""
        prompt_data = self.get_prompt(name, version)
        template = prompt_data.get("template", "")
        for k, v in kwargs.items():
            template = template.replace(f"{{{k}}}", str(v))
        return template


prompt_loader = PromptLoader()
