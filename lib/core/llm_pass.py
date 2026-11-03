"""Shared mechanics for the LLM passes (`label` and `infer`).

Both passes do the same mechanical dance:

  * resolve on-disk sprites to metadata entries via ``pixel_hash``
  * skip sprites that are already done (per ``should_process``)
  * call the model per sprite (retry on transient error, skip on hard error)
  * persist every sprite to ``<metadata>.progress`` immediately (atomic)
  * merge that progress into the base metadata every ``milestone`` sprites (atomic)
  * wrap the whole run in a cross-process ``metadata_lock``
  * estimate + track cost

`run_llm_pass` centralises all of that. `label.py` / `infer.py` only supply the
two callbacks that actually differ between the passes:

  * ``should_process(entry) -> bool``   — is this entry still missing work?
  * ``apply_result(entry, result) -> bool | None`` — merge a model response
    into the entry in place; ``False`` means it was incomplete and the entry
    stays pending.

That removes the duplicated ~120-line loop that used to live in both commands.
"""
import json
import time

from lib.core.pixel_hash import pixel_hash
from lib.core.sprite_metadata import (
    load as load_metadata,
    set_entry,
    metadata_lock,
    variant_to_base,
)
from lib.llm.orcarouter import (
    call_vllm_with_fallback,
    check_api_auth,
    image_to_b64,
)
from lib.util.paths import project_path, iter_images, atomic_save


def run_llm_pass(
    meta_path,
    sprites_dir,
    *,
    api_key,
    model,
    use_image,
    cost_per_sprite,
    should_process,
    apply_result,
    milestone=5,
    sleep=1.5,
    debug=False,
    timeout=30,
    deadline=None,
    retries=2,
    fallback_models=None,
    prompts=None,
    recursive=False,
    extensions=(".png",),
):
    """Drive one LLM pass over the sprites in ``sprites_dir``.

    Args:
        api_key: ORCAROUTER_API_KEY value (validated + probed up front).
        model: OrcaRouter model id passed to ``call_vllm_with_fallback`` (primary
            model; ``fallback_models`` are tried after it on slow/error).
        use_image: whether to send the sprite image (vs. text-only).
        cost_per_sprite: estimated $/sprite for the cost readout, or ``None``
            to skip cost reporting (e.g. an unknown ``--model`` override).
        should_process: ``fn(entry) -> bool``; ``True`` means still pending.
        apply_result: ``fn(entry, result) -> bool | None``; mutates the entry in
            place. Returning ``False`` marks the response as incomplete.
        timeout: per-request socket timeout (seconds) for each model call.
        deadline: total per-sprite wall-clock budget (seconds) across all models
            + retries. When exceeded the sprite is skipped (error) instead of
            stalling the run. ``None`` disables the deadline.
        retries: retries per model when there is no fallback list (legacy path,
            used by ``label``). Ignored in favor of single-shot failover when
            ``fallback_models`` is provided.
        fallback_models: ordered list of model ids to try after ``model`` if it is
            slow or errors (non-auth). Surfaced in the per-sprite log via
            ``result["model_used"]``.
        prompts: optional domain :class:`~lib.domains.base.PromptSet`; defaults
            to the item prompts in the OrcaRouter client.
        recursive: scan subfolders (spell packs are nested; item folders are flat).
        extensions: image extensions to include (the catalogue always writes PNG).

    Returns ``(done, cost_spent)``.
    """
    if not api_key:
        raise SystemExit("ORCAROUTER_API_KEY environment variable is required")

    # Validate the key with a cheap probe before grinding through every sprite.
    print("Checking ORCAROUTER_API_KEY / API auth...")
    ok, detail = check_api_auth(api_key, model=model)
    if not ok:
        print(f"  API auth FAILED: {detail}")
        print("  Re-export a valid ORCAROUTER_API_KEY and re-run.")
        raise SystemExit(1)
    print("  API auth OK")

    meta_path = project_path(meta_path)
    sprites_dir = project_path(sprites_dir)

    # Only sprites present on disk participate; map pixel_hash -> path.
    image_files = sorted(iter_images(sprites_dir, recursive=recursive,
                                     extensions=extensions))
    hash_to_path = {pixel_hash(p): p for p in image_files}

    with metadata_lock(meta_path):
        meta = load_metadata(meta_path)

        # Recolor variants duplicate a base image; label/infer only the base.
        recolor_variants = set(variant_to_base(meta))

        # Resume from a previous partial run, if any.
        progress_path = meta_path.with_suffix(meta_path.suffix + ".progress")
        progress = {}
        if progress_path.exists():
            try:
                progress = json.loads(progress_path.read_text(encoding="utf-8"))
                for phash, entry in progress.items():
                    set_entry(meta, phash, entry)
                if progress:
                    print(f"Resumed {len(progress)} in-progress sprite(s) from {progress_path}")
            except (json.JSONDecodeError, OSError):
                print(f"Warning: corrupt progress file {progress_path}, starting fresh.")

        # Decide what's pending (only sprites we actually have on disk).
        pending = []
        skipped = 0
        for ph, path in hash_to_path.items():
            if ph in recolor_variants:
                skipped += 1
                continue
            entry = meta["entries"].get(ph) or {}
            if should_process(entry):
                pending.append((ph, entry, path))
            else:
                skipped += 1

        print(f"Images on disk: {len(image_files)}")
        if not pending:
            print(f"Nothing to do ({skipped} skipped).")
            return 0, 0.0

        if cost_per_sprite is not None:
            est_total = len(pending) * cost_per_sprite
            print(f"Pending: {len(pending)} sprite(s)")
            print(f"Estimated cost (off-peak): ~${est_total:.2f} (~${cost_per_sprite:.4f}/sprite)")
        else:
            print(f"Pending: {len(pending)} sprite(s)")
            print("  (cost estimate unavailable for this model)")

        done = 0
        cost_spent = 0.0
        total = len(pending)
        for phash, entry, path in pending:
            sprite_id = entry.get("name") or path.stem
            data_uri = image_to_b64(path) if use_image else None
            # The pack folder is real evidence when the filename is `Icon46`.
            rel_parent = path.parent.relative_to(sprites_dir).as_posix()
            context = {"pack": "" if rel_parent == "." else rel_parent,
                       "filename": path.name}
            result = call_vllm_with_fallback(
                api_key,
                data_uri,
                sprite_id,
                entry.get("category") or "",
                entry.get("name") or path.stem,
                debug=debug,
                retries=retries,
                model=model,
                use_image=use_image,
                timeout=timeout,
                deadline=deadline,
                fallback_models=fallback_models,
                prompts=prompts,
                context=context,
            )

            if result.get("auth_error"):
                # Flush what we have, then abort the whole batch.
                for p, en in progress.items():
                    set_entry(meta, p, en)
                atomic_save(meta, meta_path, lock=False)
                print(f"  FATAL AUTH ERROR: {result.get('notes')}")
                print("  Your ORCAROUTER_API_KEY is invalid/expired/revoked. Re-export a valid key and re-run.")
                raise SystemExit(1)

            if result.get("notes", "").startswith("error:"):
                done += 1
                if cost_per_sprite is not None:
                    cost_spent += cost_per_sprite
                print(f"  [{done}/{total}] {sprite_id} ({phash}) → ERROR: {result.get('notes')}")
                continue

            complete = apply_result(entry, result) is not False
            entry["pixel_hash"] = phash
            set_entry(meta, phash, entry)
            # Persist this sprite immediately to the progress file.
            progress[phash] = entry
            atomic_save(progress, progress_path)  # own .lock file

            done += 1
            if cost_per_sprite is not None:
                cost_spent += cost_per_sprite
            model_used = result.get("model_used")
            used_tag = f" [{model_used}]" if model_used and model_used != model else ""
            if complete:
                head = entry.get("name") or entry.get("category") or ""
                tags_preview = ", ".join((entry.get("tags") or [])[:4])
                print(f"  [{done}/{total}] {sprite_id} ({phash}) → {head} | {tags_preview}{used_tag}")
            else:
                missing = ", ".join((entry.get("vllm_last") or {}).get("missing", []))
                cut = " — reply hit max_tokens" if result.get("finish_reason") == "length" else ""
                print(f"  [{done}/{total}] {sprite_id} ({phash}) → INCOMPLETE "
                      f"(missing {missing or '?'}{cut}; left for retry/review){used_tag}")

            if done % milestone == 0 or done == total:
                if cost_per_sprite is not None:
                    remaining = (total - done) * cost_per_sprite
                    print(f"    cost milestone: spent ~${cost_spent:.2f} · remaining ~${remaining:.2f}")
                # Merge all progress into the base meta and save atomically.
                for p, en in progress.items():
                    set_entry(meta, p, en)
                atomic_save(meta, meta_path, lock=False)  # outer lock already held
                progress.clear()
                atomic_save(progress, progress_path)
            time.sleep(sleep)

        # Final merge + flush.
        for p, en in progress.items():
            set_entry(meta, p, en)
        atomic_save(meta, meta_path, lock=False)
        progress.clear()
        atomic_save(progress, progress_path)
        print(f"Done. Processed {done} sprite(s). Wrote {len(meta['entries'])} entries to {meta_path}")
        if cost_per_sprite is not None:
            print(f"Total estimated cost: ~${cost_spent:.2f} (off-peak)")
        return done, cost_spent
