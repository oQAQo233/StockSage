"""三方对照实验：同一个语料、同一份 gold，跑多个抽取器。

    python -m newskg.cli compare --backends rule,llm,kggen

每跑完一个后端就落一行结果，最后生成 out/comparison.md 与 out/comparison.csv。
某个后端缺依赖或缺 key 时不会中断实验，只在表里记 unavailable 和原因。
"""

from __future__ import annotations

import csv
import json
import time
from pathlib import Path

from . import evaluate as ev
from .config import OUT
from .kg import build_extractor
from .pipeline import build_graph

TIMING = OUT / "timing.json"


def _load_timing() -> dict:
    if TIMING.exists():
        try:
            return json.loads(TIMING.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_timing(data: dict) -> None:
    TIMING.parent.mkdir(parents=True, exist_ok=True)
    TIMING.write_text(json.dumps(data, indent=2), encoding="utf-8")


def run(conn, gold_path: Path, backends: list[str], limit: int | None = None) -> list[dict]:
    gold = ev.load_gold(gold_path)
    if not gold:
        raise SystemExit(f"gold 集为空或格式不对：{gold_path}")

    results: list[dict] = []
    for name in backends:
        print(f"\n===== 后端 {name} =====")
        try:
            extractor = build_extractor(name)
        except SystemExit as exc:
            print(f"      跳过：{exc}")
            results.append({"backend": name, "status": "unavailable", "note": str(exc).strip()})
            continue

        started = time.time()
        try:
            added = build_graph(conn, extractor, limit=limit, only_ids=set(gold))
        except Exception as exc:  # noqa: BLE001  额度/网络/解析问题都不该中断整场对照
            elapsed = time.time() - started
            print(f"      失败：{exc}")
            results.append(
                {
                    "backend": name,
                    "status": "failed",
                    "note": str(exc),
                    "elapsed_sec": round(elapsed, 1),
                }
            )
            continue
        elapsed = time.time() - started
        # 第二次跑同一批文章是缓存命中的，耗时为 0——别把 0.0s 写进表里冒充真实成本。
        timing = _load_timing()
        if added > 0:
            timing[name] = round(elapsed, 1)
            _save_timing(timing)
        elapsed_label = f"{timing[name]:.1f}s" if name in timing else "—"

        metrics = ev.evaluate_extractor(conn, gold, extractor.name)
        metrics.update(
            {
                "backend": name,
                "status": "ok",
                "added": added,
                "elapsed_sec": round(elapsed, 1),
                "elapsed_label": elapsed_label,
                "note": "",
            }
        )
        results.append(metrics)
        print(
            f"      写入 {added} 条 | strict F1={metrics['strict']['f1']:.2%} "
            f"| relaxed F1={metrics['relaxed']['f1']:.2%} | {elapsed:.1f}s"
        )

    return results


def write_markdown(results: list[dict], path: Path, meta: dict) -> Path:
    ok = [r for r in results if r.get("status") == "ok"]
    lines = [
        "# 三方对照实验",
        "",
        f"- 语料：{meta.get('corpus_articles', '?')} 篇文章，来自 {meta.get('corpus_sources', '?')}",
        f"- gold：{meta.get('gold_total', '?')} 条人工标注（逐篇标注，{meta.get('gold_articles', '?')} 篇有标注）",
        f"- 生成时间：{meta.get('generated_at', '')}",
        "",
        "## 总表",
        "",
        "| 后端 | 产出三元组 | Strict P | Strict R | Strict F1 | Relaxed P | Relaxed R | Relaxed F1 | 耗时 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        if r.get("status") != "ok":
            lines.append(f"| `{r['backend']}` | — | — | — | — | — | — | — | 不可用 |")
            continue
        s, rl = r["strict"], r["relaxed"]
        lines.append(
            f"| `{r['backend']}` | {r['predicted_total']} | {s['precision']:.2%} | {s['recall']:.2%} "
            f"| **{s['f1']:.2%}** | {rl['precision']:.2%} | {rl['recall']:.2%} | {rl['f1']:.2%} "
            f"| {r.get('elapsed_label') or (str(r.get('elapsed_sec')) + 's')} |"
        )

    unavailable = [r for r in results if r.get("status") != "ok"]
    if unavailable:
        lines += ["", "## 未跑成的后端", ""]
        for r in unavailable:
            tag = "失败" if r.get("status") == "failed" else "跳过"
            lines.append(f"- `{r['backend']}`（{tag}）：{r['note']}")

    if ok:
        best = max(ok, key=lambda r: r["strict"]["f1"])
        gap = [
            r for r in ok if r["relaxed"]["f1"] - r["strict"]["f1"] > 0.05
        ]
        lines += ["", "## 怎么读这张表", ""]
        lines.append(f"- strict F1 最高的是 `{best['backend']}`（{best['strict']['f1']:.2%}）。")
        for r in gap:
            lines.append(
                f"- `{r['backend']}` 的 relaxed 比 strict 高 "
                f"{(r['relaxed']['f1'] - r['strict']['f1']):.2%}：实体配对找对了，"
                "关系词用词和 gold 不一致。这类差异在 LLM 抽取里很常见。"
            )

        gold_total = ok[0].get("gold_total") or 0
        verbose = [r for r in ok if gold_total and r["predicted_total"] > gold_total * 1.3]
        if verbose:
            names = "、".join(f"`{r['backend']}`" for r in verbose)
            lines.append(
                f"- **精确率只是下界**：{names} 的产出量超过 gold 的 1.3 倍。"
                "gold 是「精选」而非「穷尽」——多出来的三元组很多是对的、只是没被标注，"
                "却被记成了 false positive。所以这里的 Precision 不能当准确率读，"
                "**Recall 才是这个设定下可信的指标**。要拿到真实精确率，只能人工抽查未命中的部分。"
            )
        lines.append(
            "- strict 与 relaxed 的差值是「谓词自由度」的代价。要压小这个差，"
            "要么给抽取器一个封闭谓词表，要么在评估前做谓词归一（同义词表）。"
        )

    lines += ["", "## 复现命令", "", "```bat", "python -m newskg.cli compare --backends rule,llm,kggen", "```", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_csv(results: list[dict], path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["backend", "status", "triples", "strict_p", "strict_r", "strict_f1",
             "relaxed_p", "relaxed_r", "relaxed_f1", "macro_f1", "elapsed_sec"]
        )
        for r in results:
            if r.get("status") != "ok":
                writer.writerow([r["backend"], r.get("status"), "", "", "", "", "", "", "", "", ""])
                continue
            writer.writerow(
                [
                    r["backend"], "ok", r["predicted_total"],
                    r["strict"]["precision"], r["strict"]["recall"], r["strict"]["f1"],
                    r["relaxed"]["precision"], r["relaxed"]["recall"], r["relaxed"]["f1"],
                    r["macro"]["f1"], r.get("elapsed_label") or r["elapsed_sec"],
                ]
            )
    return path