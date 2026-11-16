import yaml
from pathlib import Path


def load_skills(skills_dir: str = "skills") -> list[dict]:
    skills = []
    for skill_file in Path(skills_dir).rglob("SKILL.md"):
        text = skill_file.read_text(encoding="utf-8")
        frontmatter, body = _split_frontmatter(text)
        meta = yaml.safe_load(frontmatter) or {}
        skills.append({
            "file": str(skill_file),
            "name": meta.get("name", ""),
            "risk": meta.get("risk", "low"),
            "description": meta.get("description", ""),
            "body": body,
        })
    _validate(skills)
    return skills


def _split_frontmatter(text: str) -> tuple[str, str]:
    text = text.strip()
    if not text.startswith("---"):
        return "", text
    end = text.find("\n---")
    if end == -1:
        return "", text
    return text[3:end], text[end + 3:].strip()


def _validate(skills: list[dict]) -> None:
    names = [s["name"] for s in skills if s["name"]]
    if len(names) != len(set(names)):
        raise ValueError("duplicate skill names detected")
    for skill in skills:
        if not skill["name"]:
            raise ValueError(f"skill missing name: {skill['file']}")