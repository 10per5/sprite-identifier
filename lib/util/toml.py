"""TOML serialization helpers for the catalogued library.

Avoids a hard dependency on `tomli_w` by falling back to a small hand-rolled
serializer that covers exactly the dict shapes we emit (str/int/float/bool/
None/list of str or scalar).
"""
try:
    import tomli_w  # type: ignore
except ImportError:
    tomli_w = None


def _toml_scalar(v):
    """Render a scalar (str/int/float/bool) as TOML text."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        # repr() keeps floats unambiguous (e.g. 0.5, not .5).
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    # Last resort — shouldn't normally be hit for our data shapes.
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_inline(v):
    """Render a value as an inline TOML expression (for use inside a table)."""
    if isinstance(v, dict):
        inner = ", ".join(f"{k} = {_toml_inline(val)}" for k, val in v.items())
        return "{" + inner + "}"
    if isinstance(v, list):
        return "[" + ", ".join(_toml_inline(item) for item in v) + "]"
    return _toml_scalar(v)


def toml_dumps(data):
    """Serialize a dict to TOML text.

    Prefers ``tomli_w`` when available; otherwise falls back to a small
    hand-rolled serializer that supports nested dicts/lists (e.g. the
    ``entries`` array of tables in the master catalog index), not just flat
    scalars. TOML has no ``null`` type, so unset (``None``) keys are omitted
    rather than emitted as ``null`` (which would be invalid TOML).
    """
    if tomli_w is not None:
        return tomli_w.dumps(data)

    if not isinstance(data, dict):
        raise TypeError("toml_dumps top-level value must be a dict")

    lines = []
    for k, v in data.items():
        if v is None:
            continue  # TOML has no null — omit unset keys
        # An array of table-like dicts is emitted as TOML [[key]] blocks, the
        # robust, readable representation for the catalog index.
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            for item in v:
                lines.append(f"[[{k}]]")
                for ik, iv in item.items():
                    if iv is None:
                        continue
                    lines.append(f"{ik} = {_toml_inline(iv)}")
        else:
            lines.append(f"{k} = {_toml_inline(v)}")
    return "\n".join(lines) + "\n"
