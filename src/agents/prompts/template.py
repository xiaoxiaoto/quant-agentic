import os
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, select_autoescape


class PromptTemplateManager:
    _env = Environment(
        loader=FileSystemLoader(os.path.dirname(__file__)),
        autoescape=select_autoescape(),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    _week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

    @classmethod
    def _current_time_text(cls) -> str:
        now = datetime.now()
        curr = now.strftime("%Y年%m月%d日 %H:%M:%S")
        week = cls._week_list[now.weekday()]
        return f"{curr} {week}"

    @classmethod
    def get_prompt_template(cls, prompt_name: str) -> str:
        try:
            template = cls._env.get_template(f"{prompt_name}.md.j2")
            return template.render()
        except Exception as e:
            raise ValueError(f"Error loading template {prompt_name}: {e}")

    @classmethod
    def apply_prompt_template(
        cls,
        prompt_name: str,
        **kwargs: object,
    ) -> str:
        state_vars = {"CURRENT_TIME": cls._current_time_text(), **kwargs}

        try:
            template = cls._env.get_template(f"{prompt_name}.md.j2")
            system_prompt = template.render(**state_vars)
            return system_prompt
        except Exception as e:
            raise ValueError(f"Error applying template {prompt_name}: {e}")
