"""Loads versioned prompt files from prompts/. The file name is the prompt version, and that
version is stored with every LLM output so results stay traceable after prompts change."""

from dataclasses import dataclass

from outreach.config import PROJECT_ROOT

PROMPTS_DIR = PROJECT_ROOT / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    version: str
    text: str

    def render(self, **values: str) -> str:
        # {{name}} placeholders, so literal JSON braces in prompts need no escaping.
        rendered = self.text
        for name, value in values.items():
            rendered = rendered.replace("{{" + name + "}}", value)
        return rendered


def load_prompt(version: str) -> PromptTemplate:
    return PromptTemplate(version, (PROMPTS_DIR / f"{version}.md").read_text(encoding="utf-8"))
