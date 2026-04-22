"""
mjml_compile.py — Server-side MJML→HTML compile via the global `mjml` CLI.

Used as a fallback when the client (mjml-browser in nexpo-admin) failed to
compile and stored an empty/raw `html_compiled`. The Dockerfile installs
Node.js 20 + `npm i -g mjml@4.15.3` so the binary is on PATH.

Usage:
    html = compile_mjml(mjml_source)
    if html is None: ...handle compile failure...
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15


def compile_mjml(mjml: str) -> str | None:
    """Compile MJML source to HTML via the global `mjml` CLI.

    Returns the compiled HTML on success, or None if the CLI is unavailable
    or compile fails. Caller decides how to surface the error.
    """
    if not mjml or not mjml.strip():
        return None

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".mjml", mode="w", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(mjml)
            tmp_path = tf.name

        # mjml CLI v4: default prints to stdout with a leading "<!-- FILE: ... -->"
        # comment. Capture stdout, strip the FILE comment, return HTML.
        result = subprocess.run(
            ["mjml", tmp_path],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            log.warning("mjml CLI exit %d: %s", result.returncode, result.stderr[:300])
            return None

        raw = result.stdout
        # Remove leading "<!-- FILE: /tmp/xxx.mjml -->" comment line if present
        if raw.lstrip().startswith("<!-- FILE:"):
            raw = raw.split("-->", 1)[1] if "-->" in raw else raw
        html = raw.strip()
        if not html or html.lower().startswith("<mjml"):
            log.warning("mjml CLI produced empty/raw output")
            return None
        return html

    except FileNotFoundError:
        log.error("mjml CLI not installed on PATH (Dockerfile must `npm i -g mjml`)")
        return None
    except subprocess.TimeoutExpired:
        log.warning("mjml CLI timed out after %ds", _TIMEOUT_SECONDS)
        return None
    except Exception as exc:
        log.warning("mjml compile error: %s", exc)
        return None
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
