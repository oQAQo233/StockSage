"""带证据的属性图；报告/提及关系不解释为价格因果。"""
import hashlib


def build_graph(stock, news):
    nodes, edges = {}, []
    def node(identifier, label, kind, **attributes):
        nodes[identifier] = dict(id=identifier, label=label, kind=kind, **attributes)
        return identifier
    def edge(source, target, relation, evidence=None, **attributes):
        edges.append(dict(source=source, target=target, relation=relation, evidence_id=evidence, **attributes))
    security = node("stock:" + stock["symbol"], stock["symbol"], "stock")
    company = node("company:" + stock["symbol"], stock["company"], "company")
    industry = node("industry:" + stock["industry"], stock["industry"], "industry")
    edge(company, security, "发行")
    edge(company, industry, "属于")
    aliases = {name.strip().casefold() for name in [stock["company"], *stock.get("aliases", "").split("|")] if name.strip()}
    def entity(name):
        if name.casefold() in aliases:
            return company
        identifier = "entity:" + hashlib.sha256(name.casefold().encode()).hexdigest()[:16]
        return node(identifier, name, "entity")
    triples = []
    for item in news:
        article = node("news:" + item["id"], item["title"], "news", evidence_id=item["id"],
                       published_at=item["published_at"], available_at=item["available_at"], url=item["url"])
        event = node("event:" + item["id"], item["event_type"] or "未分类事件", "event", evidence_id=item["id"])
        source = node("source:" + item["source"], item["source"], "source")
        edge(article, company, "提及", item["id"])
        edge(article, event, "报道", item["id"])
        edge(source, article, "发布", item["id"])
        for triple in item.get("triples", []):
            subject, obj = entity(triple["subject"]), entity(triple["object"])
            edge(subject, obj, triple["predicate"], item["id"], triple_id=triple["id"],
                 backend=triple["backend"], confidence=triple["confidence"], evidence_text=triple["evidence_text"],
                 verification=triple["verification"])
            edge(article, subject, "提及", item["id"])
            edge(article, obj, "提及", item["id"])
            triples.append(triple)
    return dict(nodes=list(nodes.values()), edges=edges,
                triples=triples, engine="stocksage.news_knowledge",
                note="抽取关系来自内置新闻知识引擎；事件标签沿用输入，未分类保留未分类。表面实体匹配、词典情绪及关系均不构成因果证明。")
