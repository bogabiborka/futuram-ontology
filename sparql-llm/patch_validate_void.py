"""Build-time patch: relax sparql-llm's VoID query validator (validate_sparql.py)
to reject a predicate only when the subject's type is known with a non-empty
predicate set, so an unresolved type does not reject a permitted predicate.
Idempotent; runs at DOCKER BUILD time.
"""
import importlib.util
import pathlib


def _pkg_dir(name):
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit(f"cannot locate installed package {name!r}")
    return pathlib.Path(list(spec.submodule_search_locations)[0])


TARGET = _pkg_dir("sparql_llm") / "validate_sparql.py"
src = TARGET.read_text()

OLD = "                    elif pred not in void_dict.get(subj_type, {}) and not pred.startswith(\"?\"):"
# Require the subject type to be known with a non-empty predicate set before
# rejecting; an unresolved type (empty map) is left to pass.
NEW = ("                    elif (void_dict.get(subj_type)\n"
       "                          and pred not in void_dict.get(subj_type, {})\n"
       "                          and not pred.startswith(\"?\")):")

if NEW.split("\n")[0].strip() in src:
    print("patch_validate_void: already applied — skipping")
elif OLD in src:
    src = src.replace(OLD, NEW, 1)
    TARGET.write_text(src)
    print(f"patch_validate_void: relaxed VoID predicate check in {TARGET}")
else:
    raise SystemExit(
        "patch_validate_void: anchor line not found — sparql-llm validate_sparql.py "
        "changed; re-inspect before relying on the patch.")
