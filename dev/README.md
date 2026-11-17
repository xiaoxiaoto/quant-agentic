# dev/

One-off and experimental scripts. Nothing here is part of the runtime path or imported by `src/`, `main.py`, or `tests/`.

Rules:

- Keep throwaway scripts here, not in `src/`.
- Delete scripts once they have served their purpose.
- Run them with `uv run python dev/<script>.py` so they use the project environment.
