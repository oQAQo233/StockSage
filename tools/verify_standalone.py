"""在不含原子项目的隔离副本中执行实验、可视化和全部回归测试。

用法：.venv/Scripts/python.exe tools/verify_standalone.py
默认使用当前新能源真实快照，全程不需要外网；不移动或删除原目录。
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = """import runpy,sys
sys.path.insert(0,sys.argv.pop(1))
mode,target=sys.argv[1:3]
sys.argv=[target,*sys.argv[3:]]
if mode=='path': runpy.run_path(target,run_name='__main__')
else: runpy.run_module(target,run_name='__main__',alter_sys=True)
"""


def run_stage(stage, output, name, args, *, standard_library=False):
    flags = ["-I"] + (["-S"] if standard_library else [])
    command = [sys.executable, *flags, "-c", BOOTSTRAP, str(stage), *args]
    print(f"Running {name} (no original subproject)...", flush=True)
    result = subprocess.run(command, cwd=stage, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding="utf-8", errors="replace", timeout=180,
                            env={**__import__('os').environ, "PYTHONIOENCODING": "utf-8"})
    (output/f"{name}.log").write_text(result.stdout, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"{name} failed ({result.returncode}):\n{result.stdout[-4000:]}")
    return dict(name=name, status="passed", standard_library_only=standard_library,
                log=f"{name}.log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT/"output/standalone_validation_20261108")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    evidence = dict(created_at=datetime.now(timezone.utc).isoformat(), status="running",
                    original_subproject_present_in_copy=False, checks=[])
    manifest = output/"verification.json"
    try:
        with tempfile.TemporaryDirectory(prefix="stocksage-standalone-") as folder:
            stage = Path(folder)
            for name in ("stocksage", "tests", "configs", "data"):
                shutil.copytree(ROOT/name, stage/name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copyfile(ROOT/"run.py", stage/"run.py")
            assert not (stage/"knowlegde_merge").exists()
            dataset = "data/fresh_energy_20261108"
            destination = "output/fresh_energy_20261108"
            for backend in ("rule", "none"):
                target = destination if backend == "rule" else "output/no_extraction"
                evidence["checks"].append(run_stage(stage, output, f"pipeline_{backend}",
                    ["path", "run.py", "--data-dir", dataset, "--output-dir", target,
                     "--knowledge-backend", backend, "--no-server"], standard_library=True))

            new_report = json.loads((stage/destination/"report.json").read_text(encoding="utf-8"))
            baseline = ROOT/destination/"report.json"
            if baseline.exists():
                old_report = json.loads(baseline.read_text(encoding="utf-8"))
                comparisons = {}
                for symbol, analysis in new_report["reports"].items():
                    previous = old_report["reports"][symbol]
                    fields = ("prices", "news", "features", "forecast", "summary", "knowledge", "report")
                    comparisons[symbol] = {key: analysis[key] == previous[key] for key in fields}
                    comparisons[symbol]["graph"] = all(analysis["graph"][key] == previous["graph"][key]
                                                          for key in ("nodes", "edges", "triples"))
                evidence["baseline_comparison"] = comparisons
                if not all(all(values.values()) for values in comparisons.values()):
                    raise AssertionError("迁移前后的实验数据或数值结果不一致")
            evidence["checks"].append(run_stage(stage, output, "interactive_graph",
                ["module", "stocksage.visualization", "--report", destination+"/report.json",
                 "--out", destination+"/graph_interactive.html"]))
            evidence["checks"].append(run_stage(stage, output, "regression_tests",
                ["module", "unittest", "discover", "-s", "tests", "-v"]))
            for file in (stage/destination).iterdir():
                if file.is_file():
                    shutil.copyfile(file, output/file.name)
            evidence.update(status="passed", dataset_id=new_report["dataset"]["dataset_id"],
                            stocks=len(new_report["stocks"]), articles=new_report["knowledge"]["articles"],
                            triples=new_report["knowledge"]["triples"])
            evidence["source_sha256"] = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (ROOT/"stocksage").rglob("*.py")}
    except Exception as error:
        evidence.update(status="failed", error=str(error))
        raise
    finally:
        manifest.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Standalone validation passed: {manifest}", flush=True)


if __name__ == "__main__":
    main()
