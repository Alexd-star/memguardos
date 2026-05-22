.PHONY: test eval demo doctor lint coverage docs-check competition-ready ci package clean

test:
	python -m pytest -q

eval:
	python -m memguard.eval.run_eval

eval-report:
	python -m memguard.eval.run_eval --output docs/eval_result.json --markdown docs/eval_report.md

eval-load:
	python -m memguard.eval.run_eval --latency-size 1000 --output docs/eval_result.json --markdown docs/eval_report.md

doctor:
	python -m memguard.cli.memctl doctor

demo:
	python scripts/demo_smoke.py

lint:
	python -m compileall -q memguard scripts
	python -m ruff check memguard tests scripts

coverage:
	python -m pytest --cov=memguard --cov=scripts --cov-report=term-missing

docs-check:
	python scripts/check_docs_links.py

competition-ready:
	python scripts/check_competition_ready.py --output docs/competition_ready.json --markdown docs/竞赛就绪自检报告.md

ci: lint test docs-check competition-ready

package:
	python scripts/package_submission.py

clean:
	python -c "import pathlib, shutil; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('__pycache__') if p.is_dir()]; [shutil.rmtree(p) for p in pathlib.Path('.').rglob('.pytest_cache') if p.is_dir()]"
