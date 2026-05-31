#!/usr/bin/env python3
"""
Title generation worker — standalone one-shot process for generating chat titles.

Uses a small, fast 1B model (Llama-3.2-1B-Instruct-4bit) so title generation
never blocks the main model. Reads a JSON prompt from stdin, writes the title
to stdout, then exits.

Protocol (one-shot):
  stdin:  {"prompt": "Summarize the following into a 3-6 word title...\n\n..."}
  stdout: {"title": "Generated Title"}
  stdout: {"error": "message"}  on failure
"""

import sys
import os
import json

# Make server package importable when run as a subprocess
_script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

from server.services.llm import set_offline_mode

TITLE_MODEL = "mlx-community/Llama-3.2-1B-Instruct-4bit"


def main() -> None:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            _respond({"error": "No input received"})
            return

        data = json.loads(raw)
        prompt = data.get("prompt", "")
        if not prompt:
            _respond({"error": "No prompt provided"})
            return

        # Load model (offline first, retry with networking if needed)
        print(f"[title_worker] loading {TITLE_MODEL}", file=sys.stderr)
        import mlx_lm
        try:
            model, tokenizer = mlx_lm.load(TITLE_MODEL)
        except Exception:
            print("[title_worker] retrying with networking enabled", file=sys.stderr)
            set_offline_mode(False)
            model, tokenizer = mlx_lm.load(TITLE_MODEL)

        # Generate
        messages = [{"role": "user", "content": prompt}]
        formatted = tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )

        result = mlx_lm.generate(
            model, tokenizer,
            prompt=formatted,
            max_tokens=16,
            verbose=False,
        )
        text = result if isinstance(result, str) else result.text
        title = text.strip().strip('"').strip("'")

        print(f"[title_worker] generated: {title}", file=sys.stderr)
        _respond({"title": title})

    except Exception as e:
        print(f"[title_worker] error: {e}", file=sys.stderr)
        _respond({"error": str(e)})


def _respond(data: dict) -> None:
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
