# Coding Agent Guide

Use this guide for implementation, refactoring, and bug fixes.

## Workflow

1. Identify the target layer and files before editing.
2. State assumptions when the request or existing code leaves room for interpretation.
3. Make the smallest change that solves the requested behavior.
4. Remove only unused code introduced by your own change.
5. Run the narrowest useful verification, then broaden if the change touches shared behavior.

## Change discipline

- 代码注释与文档字符串必须使用中文（Code comments and docstrings must be in Chinese），且注释中不得存在（§9.2）、G-1 等计划文档条款编号。
- Do not add features, abstractions, configuration, or defensive handling that was not requested.
- Match existing style and naming.
- Mention unrelated cleanup opportunities separately instead of folding them into the patch.

## Python naming

- Modules and packages: `lower_with_under`; internal modules: `_lower_with_under`.
- Classes and exceptions: `CapWords`; internal classes: `_CapWords`.
- Functions, methods, parameters, locals, and non-constant variables: `lower_with_under`; internal/protected names: `_lower_with_under`; private instance attributes and methods: `__lower_with_under`.
- Global and class constants: `CAPS_WITH_UNDER`; internal constants: `_CAPS_WITH_UNDER`.

## Python class organization

- New non-exempt `.py` modules must manage behavior with classes.
- Exempt paths are `main.py`, `src/routers/**`, and `tests/**`.
- Keep class bodies in this order: private variables, public variables, private methods, public methods.
- Do not add top-level business functions in non-exempt modules.
