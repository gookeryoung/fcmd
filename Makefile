# Makefile - fcmd project shortcuts
# Run `make help` to list all available commands

PACKAGE := fcmd
COV_THRESHOLD := 95
PYTEST_JOBS := 8  # pytest-xdist worker count; 8 by default on Windows to avoid handle exhaustion

# Override with: make push REMOTES="origin codeup"
REMOTES = $(shell git remote)

.PHONY: help sync build b clean c test cov lint typecheck typecheck-ci check check-docs check-fast flow gitkeep-check adr-check prek tox pub bump patch minor major push

help: ## Show this help
	@uv run python -c "import re,sys;ms=[(m.group(1),m.group(2).strip()) for f in sys.argv[1:] for l in open(f,encoding='utf-8') if (m:=re.match(r'^([a-zA-Z][\w -]*):.*?##\s*(.*)',l))];[print(f'  {n:<14} {d}') for n,d in ms]" $(MAKEFILE_LIST)

sync: ## Install development dependencies
	uv sync --extra dev

build b: ## Build distribution packages (wheel + sdist)
	uv build

clean c: ## Remove build artifacts and caches
	rm -rf build/ dist/ wheels/ *.egg-info htmlcov/ .coverage .coverage.* coverage.xml .pytest_cache/ .tox/
	rm -rf .ruff_cache/ .pyrefly_cache/ .mypy_cache/
	find src tests -type d -name __pycache__ -exec rm -rf {} +
	find src tests -type f -name "*.py[oc]" -delete

test: ## Run tests (no coverage)
	uv run pytest -m "not slow" -n $(PYTEST_JOBS)

cov: ## Run tests and generate an HTML coverage report
	uv run pytest --cov --cov-report=term --cov-fail-under=$(COV_THRESHOLD) --cov-report=html -n $(PYTEST_JOBS)
	@uv run python -c "print('Coverage report: htmlcov/index.html')"

lint: ## Lint code style (ruff)
	uv run ruff check .
	uv run ruff format --check .

typecheck: ## Type check (pyrefly)
	uv run pyrefly check

typecheck-ci: ## Type check (pyrefly, CI platform linux - catch cross-platform issues)
	uv run pyrefly check --python-platform linux

gitkeep-check: ## 校验占位目录未被清空（空目录须有 .gitkeep）
	@uv run python -c "import pathlib,sys;ds=[p for p in map(pathlib.Path,['docs/adr','docs/requirements','docs/_static']) if p.is_dir()];bad=[str(p) for p in ds if not any(p.iterdir())];[print('gitkeep-check: 空目录缺少 .gitkeep -> '+b) for b in bad];print('gitkeep-check: 通过') if not bad else None;sys.exit(1 if bad else 0)"

adr-check: ## 校验 ADR 编号/命名与索引登记
	@uv run python -c "import pathlib,re,sys;d=pathlib.Path('docs/adr');fs=[p for p in sorted(d.glob('*.md')) if p.name!='README.md'] if d.is_dir() else [];ns=[p.name for p in fs];idx=(d/'README.md').read_text(encoding='utf-8') if (d/'README.md').is_file() else '';dups=sorted({n[:4] for n in ns if sum(m[:4]==n[:4] for m in ns)>1});bad=[f'{n}: 文件名应为 NNNN-<topic>.md' for n in ns if not re.fullmatch(r'\d{4}-[\w-]+\.md',n)]+[f'编号 {x} 重复' for x in dups]+(['docs/adr/README.md 缺失'] if fs and not (d/'README.md').is_file() else [])+[f'{n}: 未登记到 docs/adr/README.md 索引' for n in ns if n not in idx];[print('adr-check: '+b) for b in bad];print(f'adr-check: 通过 ({len(fs)} ADR)') if not bad else None;sys.exit(1 if bad else 0)"

check-docs: gitkeep-check adr-check ## L0 文档门禁（gitkeep-check + adr-check）
check-fast: gitkeep-check adr-check lint typecheck ## L1 配置/内环门禁（gitkeep + adr + lint + typecheck）
flow: check-fast test ## L2 代码门禁（check-fast + test）

check: check-fast typecheck-ci cov ## 全量门禁（check-fast + typecheck-ci + cov）

prek: ## Run pre-commit hooks on all files (prek)
	uv run prek run -a


tox: ## Run multi-version tests (tox)
	uvx tox -p auto

BUMP_PART := $(filter-out bump,$(MAKECMDGOALS))

bump: ## Bump version (default patch, usage: make bump [minor|major])
	@uvx bump-my-version bump $(if $(BUMP_PART),$(firstword $(BUMP_PART)),patch) --tag
	uv sync --extra dev
	git add uv.lock
	-git diff --cached --quiet || git commit -m "chore: sync uv.lock after version bump"

patch minor major:
	@:

pub:  ## Upload to PyPI
	uvx twine upload dist/*.whl dist/*.tar.gz

push: ## Push code to all remotes (continues on failure, non-zero exit if any failed)
	@uv run python -c "import subprocess as sp,sys; remotes='$(REMOTES)'.split(); failed=[]; [failed.append(r) for r in remotes if print(f'Pushing {r}...',flush=True) or sp.run(['git','push',r]).returncode or sp.run(['git','push',r,'--tags']).returncode]; print('Failed remotes: '+', '.join(failed) if failed else 'All remotes pushed.'); sys.exit(1 if failed else 0)"

