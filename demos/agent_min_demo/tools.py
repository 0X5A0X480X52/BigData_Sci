"""Compatibility exports for the Lab 3 poetry skill tools."""

from __future__ import annotations

from typing import Any, Dict

from lab3.poetry_skill import PoemRequest, PoetrySkill, TOOL_SCHEMAS


class PoemGeneratorTool:
    name = "generate_poem"
    description = "根据主题、体裁、情感、失败记忆和反馈生成候选诗。"

    def __init__(self) -> None:
        self.skill = PoetrySkill()

    def __call__(self, request: PoemRequest, feedback: str = "") -> str:
        return self.skill.generate_poem(
            theme=request.theme,
            genre=request.genre,
            emotion=request.emotion,
            feedback=feedback,
            failed_attempts=[],
        )


class MetricCheckTool:
    name = "check_metric"
    description = "检查诗歌句数、字数、押韵情况，返回结构化报告。"

    def __init__(self) -> None:
        self.skill = PoetrySkill()

    def __call__(self, poem: str, genre: str = "七言绝句") -> Dict[str, Any]:
        return self.skill.check_metric(poem=poem, genre=genre)


class RhymeLookupTool:
    name = "lookup_rhyme"
    description = "查询汉字韵母，辅助修改尾字。"

    def __init__(self) -> None:
        self.skill = PoetrySkill()

    def __call__(self, char: str) -> Dict[str, str]:
        return self.skill.lookup_rhyme(char)


class ToolRegistry:
    """Backward-compatible dispatcher backed by PoetrySkill."""

    def __init__(self) -> None:
        self.skill = PoetrySkill()

    def dispatch(self, name: str, arguments: Dict[str, Any]) -> Any:
        return self.skill.dispatch(name, arguments)
