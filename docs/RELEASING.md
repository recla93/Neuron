# Releasing Neuron

Releases are cut from the umbrella repo, **recla93/GrayMatterEnvironment**, on
`main`. One release = one commit + one tag. Done this way for 6.6.1 on
2026-10-26.

## 1. The release commit

Bump the version in five places, in one commit (`chore(release): neuron X.Y.Z`):

| File | What |
|---|---|
| `neuron/src/neuron/__init__.py` | `__version__` |
| `neuron/pyproject.toml` | `version` |
| `neuron/README.md` | the version badge |
| `README.md` (umbrella) | Neuron's badge, and the example tag names in "Development" |
| `neuron/CHANGELOG.md` | a new `## X.Y.Z (date)` section on top |

SemVer: PATCH = fixes only; MINOR = backward-compatible features; MAJOR = data
or behaviour migrations (e.g. a new default embedding model).

`GM_VERSION` in `neuron/install.ps1` and `neuron/install.sh` must equal Gray
Matter's version in this tree. Touch it only when Gray Matter is released too.

Run the version tests before tagging:

```
python -m pytest neuron/tests/test_version_consistency.py neuron/tests/test_version_flag.py -q
python -m pytest gray_matter/tests/test_version_consistency.py gray_matter/tests/test_version_parity.py -q
```

## 2. The tag

```
git tag -a neuron-vX.Y.Z -m "neuron X.Y.Z"
git push origin main
git push origin neuron-vX.Y.Z
```

The prefix must be the folder name, `neuron-`. A bare `v*` would release all
three projects at once.

## 3. What the tag does

`release-neuron.yml`:

1. runs the Neuron suite and checks that `GM_VERSION` matches — red, no release;
2. builds the Windows pyturso wheel and the Neuron wheel and sdist;
3. publishes the GitHub Release with the three files attached.

`mirror.yml` then pushes the tag to `recla93/Neuron` as `vX.Y.Z` and publishes
the same Release there.

Check: `gh release view neuron-vX.Y.Z` lists three assets, and the mirror's
tags include `vX.Y.Z`.
