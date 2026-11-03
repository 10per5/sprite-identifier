"""Shared OrcaRouter LLM client used by label.py and infer.py.

Centralises the duplicated sprite-labeling client logic (image encoding, the
chat-completions call with retries + auth fast-fail, JSON salvage, and the
auth probe) so the vision-tagging step and the text-only re-inference step
share one implementation.

Model defaults:
  - VISION_MODEL: used by label.py (sends the sprite image).
  - TEXT_MODEL:   cheap text-only default for infer.py. The sprites are
                  already named, so category/subcategory/most tags can be derived
                  from the name without vision. Visual-only tags (color/glow/sheen)
                  are the only thing lost vs. vision.

Pick a TEXT_MODEL that is a *chat/completion* model on OrcaRouter, e.g. one of:
  qwen/qwen3.5-flash, z-ai/glm-5.3-flash, xiaomi/mimo-v2.6-flash
(NOTE: openai/text-embedding-3-small is an *embedding* model and CANNOT generate
 the JSON labels this step needs — do not use it here.)
"""
import base64
import io
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image
from lib.llm.taxonomy import (
    SYSTEM_PROMPT, USER_PROMPT_TEMPLATE,
    TEXT_SYSTEM_PROMPT, TEXT_USER_PROMPT_TEMPLATE,
)

API_BASE = "https://api.orcarouter.ai/v1"
VISION_MODEL = "deepseek/deepseek-v4-flash-vision-exp"
# Cheap text-only default for the `infer` re-inference pass. Override via --model.
TEXT_MODEL = "qwen/qwen3.5-flash"


def image_to_b64(png_path, target_size=256):
    """Upscale a PNG to target_size (nearest-neighbor) and return base64 data URI."""
    with Image.open(png_path) as im:
        im = im.convert("RGBA")
        if im.size != (target_size, target_size):
            im = im.resize((target_size, target_size), Image.Resampling.NEAREST)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _salvage_json(content, sprite_id, name, category):
    """Extract fields from truncated/malformed JSON using regex."""
    result = {}
    for key in ("name", "category", "subcategory", "notes"):
        m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)', content)
        if m:
            result[key] = m.group(1)
    # The array may be followed by more keys (a cut-off ``notes``) or be cut
    # off itself; keep every fully quoted tag either way.
    m = re.search(r'"tags"\s*:\s*\[([^\]]*)', content)
    if m:
        result["tags"] = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
    m = re.search(r'"confidence"\s*:\s*([0-9.]+)', content)
    if m:
        try:
            result["confidence"] = float(m.group(1))
        except ValueError:
            pass
    return result


# Output budget per reply. 300 cut ~250 spell replies off mid-``notes``; the
# JSON itself is ~150 tokens, the rest is headroom for verbose/reasoning models.
MAX_TOKENS = 600


def call_vllm(api_key, data_uri, sprite_id, category, name, debug=False, retries=2,
              model=VISION_MODEL, use_image=True, timeout=30, prompts=None,
              context=None):
    """Call an OrcaRouter chat-completions model and return parsed label fields.

    use_image=False sends a TEXT-ONLY request (no sprite image). Much cheaper, and
    sufficient for already-named sprites where the name + taxonomy carry the
    semantics. The only thing lost is purely visual info (actual color, glow,
    material sheen) not encoded in the name.

    ``prompts`` is an optional :class:`lib.domains.base.PromptSet`; when omitted
    the original item prompts are used for backward compatibility.

    ``context`` adds extra template fields (e.g. ``pack`` for spell icons);
    templates that don't use a field simply ignore it.

    Parsed results carry ``finish_reason`` (``"length"`` = the reply hit
    ``MAX_TOKENS``) so callers can tell truncation from a model that answered
    badly.

    On auth failure (401/403) returns a dict with ``auth_error=True`` so callers
    can abort the whole batch instead of retrying a doomed key.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if prompts is None:
        sys_prompt = SYSTEM_PROMPT if use_image else TEXT_SYSTEM_PROMPT
        user_prompt_tmpl = USER_PROMPT_TEMPLATE if use_image else TEXT_USER_PROMPT_TEMPLATE
    else:
        sys_prompt = prompts.vision_system if use_image else prompts.text_system
        user_prompt_tmpl = prompts.vision_user if use_image else prompts.text_user
    fields = {"pack": "", "filename": sprite_id, **(context or {})}
    user_content = [{"type": "text", "text": user_prompt_tmpl.format(
        sprite_id=sprite_id, category=category, name=name, **fields
    )}]
    if use_image:
        user_content.append({"type": "image_url", "image_url": {"url": data_uri}})
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS,
    }
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    last_error = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            choice = body["choices"][0]
            finish_reason = choice.get("finish_reason")
            content = (choice["message"].get("content") or "").strip()
            if content.startswith("```"):
                content = content.split("```", 2)[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            if not content:
                last_error = "empty response from VLLM"
                if debug:
                    print(f"    DEBUG empty VLLM response ({sprite_id}), attempt {attempt + 1}/{retries + 1}")
                if attempt < retries:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return {"name": name or sprite_id, "category": category or "other",
                        "subcategory": "", "tags": [], "confidence": None,
                        "notes": f"error: empty response after {retries + 1} attempts"}
            result = json.loads(content)
            # Only what the model said: falling back to the hints here used to
            # launder filenames (`Icon43`) and heuristic categories into labels.
            return {
                "name": result.get("name", ""),
                "category": result.get("category", ""),
                "subcategory": result.get("subcategory", ""),
                "tags": result.get("tags", []),
                "confidence": result.get("confidence"),
                "notes": result.get("notes", ""),
                "finish_reason": finish_reason,
            }
        except json.JSONDecodeError as exc:
            last_error = exc
            if debug:
                print(f"    DEBUG invalid JSON from VLLM ({sprite_id}): {content[:300]!r}")
            salvaged = _salvage_json(content, sprite_id, name, category)
            if salvaged:
                print(f"    Salvaged: {salvaged}")
                return {
                    "name": salvaged.get("name", ""),
                    "category": salvaged.get("category", ""),
                    "subcategory": salvaged.get("subcategory", ""),
                    "tags": salvaged.get("tags", []),
                    "confidence": salvaged.get("confidence"),
                    "notes": salvaged.get("notes", "salvaged from truncated response"),
                    "finish_reason": finish_reason,
                }
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"name": name or sprite_id, "category": category or "other",
                    "subcategory": "", "tags": [], "confidence": None,
                    "notes": f"error: {last_error}"}
        except urllib.error.HTTPError as exc:
            # Surface the API's own error message (e.g. "Invalid API key").
            detail = f"{exc.code} {exc.reason}"
            try:
                err_body = json.loads(exc.read().decode("utf-8"))
                msg = err_body.get("error", {}).get("message")
                if msg:
                    detail = f"{detail}: {msg}"
            except Exception:
                pass
            last_error = detail
            # Auth failures are fatal — no point retrying with the same key.
            if exc.code in (401, 403):
                return {"name": name or sprite_id, "category": category or "other",
                        "subcategory": "", "tags": [], "confidence": None,
                        "notes": f"error: auth: {detail}", "auth_error": True}
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"name": name or sprite_id, "category": category or "other",
                    "subcategory": "", "tags": [], "confidence": None,
                    "notes": f"error: {detail}"}
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            return {"name": name or sprite_id, "category": category or "other",
                    "subcategory": "", "tags": [], "confidence": None,
                    "notes": f"error: {last_error}"}


def check_api_auth(api_key, model=VISION_MODEL):
    """Cheap auth/connectivity probe. Returns (ok, detail).

    detail is the API's own error message on failure (e.g. "Invalid API key"),
    so callers can show a clear, actionable message instead of a generic 401.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    req = urllib.request.Request(
        f"{API_BASE}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return True, ""
    except urllib.error.HTTPError as exc:
        detail = f"{exc.code} {exc.reason}"
        try:
            err_body = json.loads(exc.read().decode("utf-8"))
            msg = err_body.get("error", {}).get("message")
            if msg:
                detail = f"{detail}: {msg}"
        except Exception:
            pass
        return False, detail
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


# Transient errors come back FAST (empty completions, truncated/malformed JSON),
# so they're worth retrying on the SAME model. Slowness, time-outs and HTTP errors
# are the ones we fail over to the next model for.
_TRANSIENT_MARKERS = (
    "empty response",
    "expecting value",
    "jsondecodeerror",
    "json decode",
)


def _is_transient_error(notes):
    n = (notes or "").lower()
    return any(m in n for m in _TRANSIENT_MARKERS)


def call_vllm_with_fallback(api_key, data_uri, sprite_id, category, name,
                            debug=False, retries=2, model=TEXT_MODEL, use_image=True,
                            timeout=30, deadline=None, fallback_models=None,
                            transient_retries=2, prompts=None, context=None):
    """Try ``model``, then each ``fallback_models`` entry, within a hard ``deadline``.

    Solves the "one slow model blocks the whole batch" problem: if the primary
    model is slow or errors (non-auth), we fail over to the next model instead of
    grinding through retries on a model that isn't responding. A ``deadline`` caps
    the total per-sprite wall-clock time, so a stuck sprite is skipped instead of
    stalling the run.

    Transient, fast errors (empty completions, malformed/truncated JSON) are
    retried on the SAME model up to ``transient_retries`` times — these usually
    succeed on a second attempt and cost little time. Slow/time-out/HTTP errors
    fail over to the next model immediately so we don't burn the deadline on a
    stuck model.

    Args:
        timeout: per-request socket timeout (seconds). Capped by the remaining
            deadline budget.
        deadline: total per-sprite budget in seconds across all models + retries.
            ``None`` disables the deadline (legacy behavior — full retries on the
            single model, as used by ``label``).
        fallback_models: list of model ids to try after ``model`` if it is slow or
            errors. Duplicate models (incl. the primary) are de-duplicated, keeping
            order.
        transient_retries: extra attempts (on the same model) for fast/transient
            errors like empty responses, before failing over to the next model.

    Returns the first successful result dict, with ``model_used`` recording which
    model produced it. On a fatal auth error (401/403) the error result is returned
    immediately so the caller can abort the batch. If the deadline is exceeded or
    every model fails, an ``error:`` result is returned so the sprite can be skipped
    and retried later.
    """
    models = [model] + list(fallback_models or [])
    seen, ordered = set(), []
    for m in models:
        if m not in seen:
            seen.add(m)
            ordered.append(m)

    # Legacy path: no deadline and a single model → behave exactly like call_vllm
    # (preserves the existing ``label`` retry behavior, including its own retries).
    if deadline is None and len(ordered) == 1:
        result = call_vllm(api_key, data_uri, sprite_id, category, name,
                           debug=debug, retries=retries, model=ordered[0],
                           use_image=use_image, timeout=timeout, prompts=prompts,
                           context=context)
        result.setdefault("model_used", ordered[0])
        return result

    start = time.monotonic()
    last_notes = None
    for m in ordered:
        elapsed = time.monotonic() - start
        # Bail once the whole budget is exhausted.
        if deadline is not None and elapsed >= deadline:
            return {"name": name or sprite_id, "category": category or "other",
                    "subcategory": "", "tags": [], "confidence": None,
                    "notes": f"error: deadline exceeded ({deadline:.0f}s) on {m}",
                    "model_used": m}
        remaining = (deadline - elapsed) if deadline is not None else None
        # Cap the per-request timeout by the remaining budget so a single request
        # can never overrun the deadline (give at least 1s so a near-deadline
        # attempt can still run).
        req_timeout = max(1, min(int(remaining), timeout)) if remaining is not None else timeout

        # Try this model, retrying only fast/transient errors. Slow/time-out/HTTP
        # errors fail over to the next model right away.
        model_failed = None
        for attempt in range(transient_retries + 1):
            result = call_vllm(api_key, data_uri, sprite_id, category, name,
                               debug=debug, retries=0, model=m,
                               use_image=use_image, timeout=req_timeout,
                               prompts=prompts, context=context)
            if result.get("auth_error"):
                return result  # fatal — caller aborts the whole batch
            if not result.get("notes", "").startswith("error:"):
                result["model_used"] = m
                return result
            notes = result.get("notes", "")
            if _is_transient_error(notes) and attempt < transient_retries:
                if debug:
                    print(f"    transient error on {m} ({notes}); "
                          f"retry {attempt + 1}/{transient_retries}")
                time.sleep(1.0)
                continue
            # Non-transient failure, or transient retries exhausted → next model.
            model_failed = result
            if debug and not _is_transient_error(notes):
                rem = f"{remaining:.0f}s left" if remaining is not None else "no deadline"
                print(f"    fallback: {m} failed ({notes}); {rem}")
            break
        last_notes = model_failed.get("notes") if model_failed else last_notes
        continue  # try the next model

    return {"name": name or sprite_id, "category": category or "other",
            "subcategory": "", "tags": [], "confidence": None,
            "notes": f"error: all models failed — last: {last_notes}",
            "model_used": ordered[-1]}
