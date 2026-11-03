# Repository Guidelines

## Project Structure & Module Organization

FRRouting is a routing suite built around separate protocol daemons. Directories such as `bgpd/`, `ospfd/`, `isisd/`, and `zebra/` contain daemon code; `lib/` holds shared C libraries, `vtysh/` the CLI, and `yang/` management models. Python utilities live in `python/` and `tools/`. Tests are under `tests/`, including network integration tests in `tests/topotests/`. Developer and user documentation live in `doc/developer/` and `doc/user/`; capstone material is in `CAPSTONE.md` and `doc/capstone/`.

## Build, Test, and Development Commands

- `./bootstrap.sh` generates the Autotools build infrastructure from a source checkout.
- `./configure` configures the build; consult the platform-specific guides in `doc/developer/` for dependencies and options.
- `make -j` builds the configured project; `make check` runs the build's test suite.
- From `tests/topotests/`, `sudo -E pytest <test-directory>` runs a selected topology test after its documented Linux, FRR, and networking prerequisites are installed.

## Coding Style & Naming Conventions

Follow the surrounding daemon's conventions and use descriptive, consistent names. Format C changes using the repository's `.clang-format`; `tools/indent.py` also handles FRR CLI macros. Follow existing Python indentation and the settings in `.flake8` and `.isort.cfg`. Avoid unrelated formatting changes. See `doc/developer/workflow.rst` for detailed style guidance.

## Testing Guidelines

Add focused regression coverage for behavioral changes. Topotests use pytest, typically with `test_*.py` files and `test_*` functions; keep topology configurations and expected results alongside the relevant test directory. Run the narrowest applicable tests first, then `make check` or broader integration tests as appropriate. Topotests require privileged networking setup; see `doc/developer/topotests.rst`. Record any tests you could not run and why.

## Commit & Pull Request Guidelines

Recent commits use concise, scoped subjects such as `tests: fix controller IP` and `doc: update capstone cookbook`. Explain the motivation and behavior in the commit or PR description, and link relevant issues. Include test commands and results, and screenshots for visual changes. FRR requires a `Signed-off-by` line with your real name and email on every patch (`git commit -s`); see `doc/developer/workflow.rst` for the contributor certification and full PR guidance.
