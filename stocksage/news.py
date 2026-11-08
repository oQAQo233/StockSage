"""将内置来源与正文处理结果转换为规范新闻，记录失败与未关联状态。"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .contracts import local_file, publication_bound
from .data import timestamp
from .knowledge import link_symbols
from .news_knowledge import dedup, models

CORPUS_ROOT = Path(__file__).resolve().parent / "news_knowledge" / "assets"


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _record(spec, body, observed, mode, stocks):
    for key in ("id", "title", "source", "published_at"):
        if not isinstance(spec.get(key), str) or not spec[key].strip():
            raise ValueError(f"新闻缺少有效字段 {key}")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("新闻正文为空或格式无效")
    row = dict(id=spec["id"], title=spec["title"], body=body, source=spec["source"],
               url=spec.get("url", ""), published_at=spec["published_at"],
               published_precision=spec.get("published_precision", "second"),
               fetched_at=observed, first_seen_at=observed if mode == "observed" else "",
               availability_basis=mode, event_type="未分类", text_kind="excerpt",
               lang=spec.get("lang", "zh"), raw_ref=spec.get("raw_ref", ""),
               markdown_ref=spec.get("markdown_ref", ""), license=spec.get("license", "来源许可需核验"))
    bound = publication_bound(row)
    if timestamp(observed) < bound:
        raise ValueError("新闻发布时间晚于实际观测时间")
    row["available_at"] = (max(timestamp(observed), bound) if mode == "observed" else bound + timedelta(minutes=5)).isoformat()
    symbols, evidence, ambiguous = link_symbols(row, stocks)
    row.update(symbols=symbols, link_method="exact_code_or_alias", link_evidence=evidence,
               ambiguous_links=ambiguous, event_evidence="")
    # 已有人工核验只在原链接对应的同一报道上继承；不将弱别名命中升级成人工标注。
    if spec.get("link_method") == "manual_verified":
        if not isinstance(spec.get("symbols"), list) or set(spec["symbols"]) - {stock["symbol"] for stock in stocks}:
            raise ValueError("人工关联包含未知证券或格式无效")
        row.update(symbols=list(spec["symbols"]), link_method="manual_verified", link_evidence=spec["link_evidence"])
        if spec.get("event_evidence"):
            row.update(event_type=spec.get("event_type", "未分类"), event_evidence=spec["event_evidence"])
    return row


def _read_frozen(path):
    text = path.read_text(encoding="utf-8-sig")
    if not text.startswith("---\n"):
        raise ValueError("缺少 Markdown 元数据头")
    head, body = text.split("---", 2)[1:]
    metadata = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    for key in ("article_id", "source", "url", "title", "published_at"):
        if not metadata.get(key):
            raise ValueError(f"冻结新闻缺少 {key}")
    return dict(id=metadata["article_id"], **{k: metadata[k] for k in ("source", "url", "title", "published_at")},
                lang=metadata.get("lang", "en"), license=metadata.get("license", "来源许可需核验")), body.strip()


def collect_news(stocks, dataset_news, output_dir, source="dataset", limit=20, days=7,
                 mode="observed", query=None, snapshot=None):
    if limit < 1 or days < 1:
        raise ValueError("news-limit 和 news-days 必须为正整数")
    if mode not in {"observed", "historical_assumption"}:
        raise ValueError("新闻可用时间模式无效")
    folder = Path(output_dir) / "news_inputs"
    folder.mkdir(parents=True, exist_ok=True)
    diagnostics = dict(source=source, availability_mode=mode, discovered=0, collected=0, failures=[],
                       source_failures=[], records=[], notes=[])
    if source == "dataset":
        diagnostics.update(discovered=len(dataset_news), collected=len(dataset_news),
                           availability_mode="preserved_from_dataset")
        return [dict(item) for item in dataset_news], diagnostics
    if mode == "historical_assumption":
        diagnostics["notes"].append("正文为当前取得的版本；历史可用时间按发布精度上界加5分钟假设，只能用于回顾性验收。")
    news = []
    if source == "snapshot":
        if snapshot is None:
            raise ValueError("snapshot 来源需要 --news-snapshot 指向采集 manifest.json")
        snapshot = Path(snapshot)
        if snapshot.parent.resolve() == folder.resolve():
            raise ValueError("快照输入与输出目录相同；请指定独立 --output-dir，避免改写输入")
        previous = json.loads(snapshot.read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not {"documents_ref", "documents_sha256", "records"} <= previous.keys():
            raise ValueError("新闻快照 manifest 缺少 documents_ref/documents_sha256/records")
        path = local_file(snapshot.parent, previous["documents_ref"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != previous["documents_sha256"]:
            raise ValueError("新闻快照 SHA256 不匹配")
        documents = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(documents, list) or any(not isinstance(item, dict) for item in documents):
            raise ValueError("新闻快照 documents 须为对象列表")
        documents = documents[:limit]
        diagnostics["discovered"] = len(documents)
        diagnostics["parent_snapshot"] = str(snapshot.resolve())
        for item in documents:
            # 观测模式保留实际采集时间；回顾模式必须由命令显式选择。
            observed = item["fetched_at"]
            curated = next((n for n in dataset_news if n.get("url") == item.get("url") and n.get("link_method") == "manual_verified"), None)
            spec = {**item}
            if curated:
                spec.update({key: curated[key] for key in ("symbols", "link_method", "link_evidence", "event_type", "event_evidence")})
            row = _record(spec, item["body"], observed, mode, stocks)
            news.append(row)
        diagnostics["records"] = previous["records"]
        diagnostics["notes"].append("离线重放已校验内容哈希的新闻正文快照；未重新抓取网页。")
    elif source == "corpus":
        paths = sorted((CORPUS_ROOT / "md").glob("*.md"))
        if query:
            paths = [p for p in paths if query.casefold() in p.read_text(encoding="utf-8-sig").casefold()]
        paths = paths[:limit]
        diagnostics["discovered"] = len(paths)
        for path in paths:
            try:
                spec, body = _read_frozen(path)
                # 无原始接收日志的冻结文档按此次实际读取时间可用，绝不伪造历史 first_seen。
                observed = datetime.now(timezone.utc).isoformat()
                spec["markdown_ref"] = str(path.relative_to(CORPUS_ROOT))
                row = _record(spec, body, observed, mode, stocks)
                news.append(row)
                diagnostics["records"].append(dict(id=row["id"], origin=str(path.relative_to(CORPUS_ROOT)),
                                                   sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            except (ValueError, OSError) as error:
                diagnostics["failures"].append(dict(origin=path.name, reason=str(error)))
        diagnostics["notes"].append("冻结语料仅重放，未验证原网站当前可达性；发布时间精度沿用原始文档，不代表历史首次可用。")
    else:
        # 仅在线采集或显式转换正文时加载可选依赖。
        try:
            from .news_knowledge import convert
            from .news_knowledge.sources.base import RateLimiter
        except ModuleNotFoundError as error:
            raise ValueError(f"新闻采集缺少 {error.name}；请安装 requirements-news.txt") from error
        if source == "urls":
            stubs = [models.ArticleStub(source=n["source"], url=n["url"], title=n["title"],
                                        published_at=n["published_at"], lang=n.get("lang", "zh"),
                                        extra={"dataset_record": n}) for n in dataset_news[:limit]]
        else:
            from .news_knowledge import sources
            # 逐源运行并记录失败；不用旧 MultiSource 的仅打印错误路径。
            names = ["eastmoney-rss", "eastmoney", "ths", "sina-fin"] if source == "cn-finance" else source.split(",")
            stubs = []
            for name in names:
                try:
                    adapter = sources.build_source(name.strip())
                    filtered = sources.FilteredSource(adapter, topic="all", keep_undated=False)
                    stubs.extend(filtered.discover(query, limit, days))
                except (Exception, SystemExit) as error:
                    diagnostics["source_failures"].append(dict(source=name, reason=str(error)))
            stubs = stubs[:limit]
        diagnostics["discovered"] = len(stubs)
        limiter = RateLimiter(1.0)
        seen_urls = set()
        for stub in stubs:
            canonical = dedup.canonicalize_url(stub.url)
            if canonical in seen_urls:
                continue
            seen_urls.add(canonical)
            try:
                limiter.wait(stub.url)
                if source in {"fixtures", "fixture", "offline"}:
                    from .news_knowledge.config import FIXTURES
                    raw = local_file(FIXTURES, stub.extra["file"]).read_text(encoding="utf-8")
                else:
                    raw = convert.fetch_html(stub.url)
                observed = datetime.now(timezone.utc).isoformat()
                document = convert.extract_article(raw, url=stub.url)
                if not document.body_text:
                    raise ValueError("正文为空：可能是付费墙、反爬或不支持页面")
                original = stub.extra.get("dataset_record")
                identifier = original["id"] if original else hashlib.sha256(canonical.encode()).hexdigest()[:20]
                spec = dict(id=identifier, source=stub.source, url=stub.url,
                            title=document.title or stub.title, published_at=document.published_at or stub.published_at,
                            lang=stub.lang, license=stub.license)
                if original:
                    # 已经人工核验过的发布时间/精度优先，不能用页面提取器的日期截断覆盖它。
                    spec.update({key: original[key] for key in ("published_at", "published_precision", "symbols", "link_method", "link_evidence", "event_type", "event_evidence") if key in original})
                filename = hashlib.sha256(identifier.encode()).hexdigest()[:24]
                raw_path, md_path = folder / f"{filename}.html", folder / f"{filename}.md"
                raw_path.write_text(raw, encoding="utf-8")
                md_path.write_text(convert.build_markdown_document(spec, document.body_markdown), encoding="utf-8")
                spec.update(raw_ref=str(raw_path.relative_to(output_dir)), markdown_ref=str(md_path.relative_to(output_dir)))
                row = _record(spec, document.body_text, observed, mode, stocks)
                news.append(row)
                diagnostics["records"].append(dict(id=identifier, url=stub.url, fetched_at=observed,
                                                   raw_ref=spec["raw_ref"], sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
                                                   text_engine=document.engine))
            except (Exception, SystemExit) as error:
                diagnostics["failures"].append(dict(url=stub.url, reason=str(error)))
    diagnostics["collected"] = len(news)
    documents_path = folder / "documents.json"
    save_json(documents_path, news)
    diagnostics.update(documents_ref="documents.json", documents_sha256=hashlib.sha256(documents_path.read_bytes()).hexdigest())
    save_json(folder / "manifest.json", diagnostics)
    if not news:
        raise ValueError(f"新闻来源 {source} 没有成功记录；详见 {folder / 'manifest.json'}")
    return news, diagnostics
