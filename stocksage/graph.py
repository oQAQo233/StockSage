"""带证据的属性图；报告/提及关系不解释为价格因果。"""


def build_graph(stock, news):
    nodes, edges = {}, []
    def node(identifier, label, kind, **attributes):
        nodes[identifier] = dict(id=identifier, label=label, kind=kind, **attributes)
        return identifier
    def edge(source, target, relation, evidence=None):
        edges.append(dict(source=source, target=target, relation=relation, evidence_id=evidence))
    security = node("stock:" + stock["symbol"], stock["symbol"], "stock")
    company = node("company:" + stock["symbol"], stock["company"], "company")
    industry = node("industry:" + stock["industry"], stock["industry"], "industry")
    edge(company, security, "发行")
    edge(company, industry, "属于")
    for item in news:
        article = node("news:" + item["id"], item["title"], "news", evidence_id=item["id"],
                       published_at=item["published_at"], available_at=item["available_at"], url=item["url"])
        event = node("event:" + item["id"], item["event_type"] or "未分类事件", "event", evidence_id=item["id"])
        source = node("source:" + item["source"], item["source"], "source")
        edge(article, company, "提及", item["id"])
        edge(article, event, "报道", item["id"])
        edge(source, article, "发布", item["id"])
    return dict(nodes=list(nodes.values()), edges=edges,
                note="事件分类与股票关联来自输入字段；情绪为词典规则。图谱关系不构成价格因果证明。")
