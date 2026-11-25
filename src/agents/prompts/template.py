"""Jinja2 提示词模板统一管理器。"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape


class PromptTemplateManager:
    """提示词模板加载与参数渲染管理器。"""

    _env = Environment(
        loader=FileSystemLoader(os.path.dirname(__file__)),
        autoescape=select_autoescape(),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    _week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    _tz = ZoneInfo("Asia/Shanghai")

    @classmethod
    def _current_time_text(cls) -> str:
        """格式化当前北京时间与星期几文本。"""
        now = datetime.now(cls._tz)
        curr = now.strftime("%Y年%m月%d日 %H:%M:%S")
        week = cls._week_list[now.weekday()]
        return f"{curr} {week}"

    @classmethod
    def get_prompt_template(cls, prompt_name: str) -> str:
        """加载并直接渲染无动态参数的提示词模板。"""
        try:
            template = cls._env.get_template(f"{prompt_name}.md.j2")
            return template.render()
        except Exception as e:
            raise ValueError(f"加载提示词模板 {prompt_name} 失败: {e}") from e

    @classmethod
    def apply_prompt_template(
        cls,
        prompt_name: str,
        **kwargs: object,
    ) -> str:
        """根据动态参数渲染指定提示词模板，自动注入当前时间。"""
        state_vars = {"CURRENT_TIME": cls._current_time_text(), **kwargs}

        try:
            template = cls._env.get_template(f"{prompt_name}.md.j2")
            system_prompt = template.render(**state_vars)
            return system_prompt
        except Exception as e:
            raise ValueError(f"应用提示词模板 {prompt_name} 失败: {e}") from e
