"use strict";
const $ = id => document.getElementById(id);
const ns = "http://www.w3.org/2000/svg";
let current = null;
let datasetInfo = {};
let requestNumber = 0;
const pct = value => value === null ? "—" : `${(value * 100).toFixed(2)}%`;
const signed = value => `${value > 0 ? "+" : ""}${pct(value)}`;
const directionClass = value => value >= 0 ? "positive" : "negative";
const short = (value, limit) => value.length > limit ? value.slice(0, limit) + "…" : value;

function element(tag, attrs = {}, text = null) {
  const item = document.createElementNS(ns, tag);
  for (const [key, value] of Object.entries(attrs)) item.setAttribute(key, value);
  if (text !== null) item.textContent = text;
  return item;
}
function htmlElement(tag, className, text) {
  const item = document.createElement(tag);
  if (className) item.className = className;
  if (text !== undefined) item.textContent = text;
  return item;
}
async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`数据读取失败 (${response.status})`);
  return response.json();
}
function showError(error) { $("error").hidden = false; $("error").textContent = error.message; }

function renderMetrics(data) {
  const s = data.summary;
  const basis = data.prices.at(-1).price_basis;
  const priceLabel = basis === "qfq_snapshot" ? "前复权快照收盘价 · CNY" : basis === "hfq_snapshot" ? "后复权快照收盘价 · CNY" : "最新收盘价";
  const entries = [[priceLabel, s.latest_close.toFixed(2), s.latest_date, ""],
    ["当日涨跌幅", signed(s.daily_return), "相对上一有行情交易日", directionClass(s.daily_return)],
    ["区间累计变化", signed(s.period_return), `${data.prices.length} 个交易日`, directionClass(s.period_return)],
    ["已对齐新闻", String(s.news_count), `${s.pending_count} 条尚未到收盘可用时点`, ""]];
  $("metrics").replaceChildren(...entries.map(([label, value, note, cls]) => {
    const card = htmlElement("div", "metric");
    card.append(htmlElement("div", "metric-label", label), htmlElement("div", `metric-value ${cls}`, value), htmlElement("small", "", note));
    return card;
  }));
  $("brief").textContent = data.report.text;
  $("brief-evidence").replaceChildren(...(s.latest_news_ids.length ? s.latest_news_ids.map(id => htmlElement("span", "evidence-chip", id)) : [htmlElement("span", "muted", "当日没有对齐新闻，情绪特征记为 0。注意：无新闻不等于中性新闻。") ]));
}

function renderPrice(data) {
  const svg = $("price-chart"); svg.replaceChildren();
  const prices = data.prices, left = 58, top = 25, width = 700, height = 215;
  let lo = Math.min(...prices.map(p => p.close)), hi = Math.max(...prices.map(p => p.close));
  const padding = Math.max((hi - lo) * .12, lo * .005); lo -= padding; hi += padding;
  const x = i => left + i / (prices.length - 1) * width;
  const y = value => top + (hi - value) / (hi - lo) * height;
  for (let i = 0; i <= 4; i++) {
    const yy = top + height / 4 * i;
    svg.append(element("line", {x1:left, y1:yy, x2:left+width, y2:yy, class:"chart-grid"}), element("text", {x:left-10, y:yy+4, "text-anchor":"end"}, (hi-(hi-lo)*i/4).toFixed(1)));
  }
  const path = prices.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(2)},${y(p.close).toFixed(2)}`).join(" ");
  svg.append(element("path", {d:`${path} L${x(prices.length-1)},${top+height} L${left},${top+height} Z`, class:"chart-area"}), element("path", {d:path, class:"chart-line"}));
  const counts = new Map(); for (const item of data.news) counts.set(item.effective_date, (counts.get(item.effective_date)||0)+1);
  prices.forEach((p, i) => {
    if (counts.has(p.date)) {
      const dot = element("circle", {cx:x(i), cy:y(p.close), r:4, class:"news-dot"});
      dot.append(element("title", {}, `${p.date} · ${counts.get(p.date)} 条新闻`)); svg.append(dot);
    }
  });
  for (const i of [0, Math.floor(prices.length/3), Math.floor(prices.length*2/3), prices.length-1]) svg.append(element("text", {x:x(i), y:top+height+27, "text-anchor":"middle"}, prices[i].date.slice(5)));
  const timeNote = datasetInfo.news_time_mode === "historical_assumption" ? "按历史回填假设进入该收盘桶的新闻" : "按输入可用时间进入该收盘桶的新闻";
  $("chart-note").textContent = `${prices[0].date} — ${prices.at(-1).date} · 金色点表示${timeNote}；关联不代表因果。`;
}

function renderGraph(data) {
  const svg = $("graph"); svg.replaceChildren();
  const selected = new Set(data.news.slice(0, 6).map(n => n.id));
  const edges = data.graph.edges.filter(e => !e.evidence_id || selected.has(e.evidence_id));
  const used = new Set(edges.flatMap(e => [e.source,e.target]));
  const nodes = data.graph.nodes.filter(n => used.has(n.id));
  const byId = new Map(data.news.map(n => [n.id,n]));
  const columns = {stock:85,industry:85,company:280,source:280,news:565,event:835,entity:1030};
  const counts = {}, positions = new Map();
  for (const n of nodes) {
    const index=counts[n.kind]||0;counts[n.kind]=index+1;
    const y=n.kind==="stock"?135:n.kind==="industry"?335:n.kind==="company"?235:n.kind==="source"?45+index*55:75+index*64;
    positions.set(n.id,{...n,x:columns[n.kind]||1030,y,w:n.kind==="news"?270:n.kind==="entity"?150:160});
  }
  const height=Math.max(450,...[...positions.values()].map(n=>n.y+50));
  svg.setAttribute("viewBox",`0 0 1120 ${height}`);
  const showNews=id=>{const n=byId.get(id);if(n)$("graph-detail").textContent=`${n.id} ｜ ${n.title} ｜ 来源：${n.source} ｜ 可用：${n.available_at} ｜ 对齐：${n.effective_date} ｜ ${short(n.body||n.event_evidence||"仅保留标题",500)}`;};
  $("graph-detail").textContent="选择图谱新闻节点或关系表中的证据，可查看来源与原文。";
  for(const edge of edges){
    const a=positions.get(edge.source),b=positions.get(edge.target);
    const sameColumn=a.x===b.x;
    const path=element("path",{d:`M${a.x},${a.y} Q${sameColumn?a.x-100:(a.x+b.x)/2},${sameColumn?(a.y+b.y)/2:a.y} ${b.x},${b.y}`,class:edge.triple_id?"graph-edge extracted-edge":"graph-edge"});
    path.append(element("title",{},`${edge.relation}${edge.evidence_id?" · "+edge.evidence_id:""}`));svg.append(path);
    if(edge.triple_id||!edge.evidence_id)svg.append(element("text",{x:(a.x+b.x)/2-25,y:(a.y+b.y)/2-7,class:"edge-label"},edge.relation));
  }
  for(const p of positions.values()){
    const clickable=Boolean(p.evidence_id);
    const group=element("g",clickable?{class:"news-node",tabindex:"0",role:"button","aria-label":p.label}:{});
    group.append(element("rect",{x:p.x-p.w/2,y:p.y-19,width:p.w,height:38,rx:7,class:`node-${p.kind}`}),element("text",{x:p.x,y:p.y,class:"node-label"},short(p.label,p.kind==="news"?19:11)),element("title",{},p.label));
    if(clickable){group.addEventListener("click",()=>showNews(p.evidence_id));group.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();showNews(p.evidence_id);}});}
    svg.append(group);
  }
  const knowledge=data.knowledge||{triples:[],backends:[],no_relations:0};
  $("knowledge-status").textContent=`newskg / ${knowledge.backends.join(", ")||"暂无可用新闻"} · 当前股票时点可用 ${data.news.length} 篇，抽取关系 ${knowledge.triples.length} 条，${knowledge.no_relations} 篇未抽到关系。未抽到关系不表示新闻没有事实。`;
  $("knowledge-triples").replaceChildren(...knowledge.triples.map(t=>{
    const row=htmlElement("tr");for(const text of [t.subject,t.predicate,t.object,t.backend])row.append(htmlElement("td","",text));
    const cell=htmlElement("td"),button=htmlElement("button","evidence-button",t.evidence_id);
    button.addEventListener("click",()=>{$("graph-detail").textContent=`${t.subject} — ${t.predicate} → ${t.object} ｜ ${t.evidence_id} ｜ ${t.evidence_text||"仅能在全文定位两端实体，未定位到同一句"} ｜ 后端分数为启发值，非真实概率。`;});cell.append(button);row.append(cell);return row;
  }));
  if(!knowledge.triples.length){const row=htmlElement("tr"),cell=htmlElement("td","muted","该股票当前可用新闻未抽取出关系；可查看新闻证据，或配置 llm/kggen 后端重新运行。");cell.colSpan=5;row.append(cell);$("knowledge-triples").append(row);}
}

function renderForecast(data) {
  const f = data.forecast;
  $("forecast-asof").textContent = `基于 ${f.as_of} 收盘信息`;
  $("prediction").textContent = signed(f.next_return);
  $("prediction").className = `prediction ${directionClass(f.next_return)}`;
  $("prediction-note").textContent = `历史误差尺度 RMSE：${pct(f.residual_scale)}（非置信区间）。${datasetInfo.synthetic ? "合成数据不代表真实预测能力。" : datasetInfo.news_completeness === "curated_incomplete" ? "本小样本仅验证管线，不能据此判断预测有效性。" : "历史结果不保证未来表现。"}`;
  const names = {zero:"零收益基线", price_only:"纯行情 · 岭回归", fused:"新闻融合 · 岭回归"};
  $("model-metrics").replaceChildren(...Object.entries(f.metrics).map(([name,m]) => {
    const row=htmlElement("tr", name === "fused" ? "fused-row" : "");
    for (const text of [names[name], pct(m.mae), pct(m.rmse), pct(m.direction_accuracy)]) row.append(htmlElement("td","",text));
    return row;
  }));
  const traces = f.backtest;
  $("validation-note").textContent = `测试预测时点：${traces[0].date} 至 ${traces.at(-1).date}，共 ${f.test_samples} 次。每次仅使用当时已知标签，重新拟合标准化与模型。最新预测使用 ${f.train_samples} 个已标注样本。α=8 固定，未依据测试集调参。`;
  const svg = $("contributions");svg.replaceChildren();
  const max = Math.max(...f.contributions.map(Math.abs), .00001);
  svg.append(element("line", {x1:320,y1:10,x2:320,y2:205,class:"bar-axis"}));
  f.contributions.forEach((value,index) => {
    const yy=22+index*31, width=Math.abs(value)/max*132;
    svg.append(element("text", {x:4,y:yy+4}, data.feature_names[index]),element("rect", {x:value>=0?320:320-width,y:yy-9,width,height:17,rx:3,class:value>=0?"bar-positive":"bar-negative"}),element("text",{x:550,y:yy+4,"text-anchor":"end"},signed(value)));
  });
  svg.append(element("text",{x:4,y:231},`截距：${signed(f.intercept)}；数值为收益率贡献。`));
}

function renderNews() {
  const query = $("news-search").value.toLowerCase();
  const items = current.news.filter(n => `${n.title} ${n.event_type} ${n.id}`.toLowerCase().includes(query));
  $("news-table").replaceChildren(...items.map(n => {
    const row=htmlElement("tr");const title=htmlElement("td");
    title.append(htmlElement("div","news-title",n.title),htmlElement("div","news-source",`${n.id} · ${n.source}`));
    if (/^https?:\/\//i.test(n.url)) {
      const link=htmlElement("a","news-source","查看来源");link.href=n.url;link.target="_blank";link.rel="noopener noreferrer";title.append(link);
    }
    const score=htmlElement("td");score.append(htmlElement("span",`sentiment ${directionClass(n.sentiment)}`,`${n.sentiment>0?"正面":n.sentiment<0?"负面":"中性"} ${n.sentiment.toFixed(2)}`));
    row.append(title,htmlElement("td","",n.event_type||"未分类"),score,htmlElement("td","",n.available_at.replace("T"," ").slice(0,16)),htmlElement("td","",n.effective_date));
    return row;
  }));
  if (!items.length) { const row=htmlElement("tr"),cell=htmlElement("td","muted","没有匹配新闻");cell.colSpan=5;row.append(cell);$("news-table").append(row); }
  $("news-note").textContent = `显示 ${items.length} / ${current.news.length} 条已对齐新闻；${current.pending_news.length} 条晚于最新收盘时点，未参与当前分析。情绪分数来自简化中文词典，未进行金融语境模型训练。`;
}

async function loadSymbol(symbol) {
  const number=++requestNumber;
  try {
    const data=await getJSON(`/api/dashboard?symbol=${encodeURIComponent(symbol)}`);
    if(number!==requestNumber)return;
    current=data;$("error").hidden=true;
    renderMetrics(data);renderPrice(data);renderGraph(data);renderForecast(data);renderNews();
  } catch(error) { if(number===requestNumber)showError(error); }
}

async function init() {
  try {
    const info=await getJSON("/api/stocks");
    datasetInfo=info.dataset;
    const dataset=info.dataset;
    const acquired=dataset.acquisition_completed_at || info.stocks.map(s=>s.fetched_at||"").sort().at(-1);
    const provenance=[`数据集：${dataset.dataset_id||"合成演示 / 未声明标识"}`,
      `行情范围：${dataset.coverage?dataset.coverage.start+" 至 "+dataset.coverage.end:"以图表日期为准"}`,
      `采集完成：${acquired?new Date(acquired).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai",hour12:false})+"（北京时间）":"未提供"}`,
      `${info.stocks.length} 只股票 · ${info.quality.price_rows} 条行情 · ${info.quality.news_rows} 篇新闻`];
    $("dataset-provenance").replaceChildren(...provenance.map(text=>htmlElement("span","",text)));
    $("data-notice").textContent=info.dataset.synthetic ? "教学演示：当前使用虚构股票和合成数据，预测仅用于方法验证。" : info.dataset.synthetic === false ? "真实来源快照：" + (info.quality?.warnings || info.limitations).join(" ") : "导入数据的真实性未确认；预测结果仅用于方法验证。";
    $("dataset-label").textContent=info.dataset.label + (info.dataset.news_time_mode === "historical_assumption" ? " · 时间为回填假设，非历史实时接收" : "");
    if(info.knowledge?.ingestion?.source!=="dataset")$("data-notice").textContent+=` newskg 已采集 ${info.knowledge.articles} 篇，其中 ${info.knowledge.unlinked_articles} 篇未关联目标证券；股票时点可见关系 ${info.knowledge.visible_triples} 条。`;
    $("stock-select").replaceChildren(...info.stocks.map(stock=>{const option=htmlElement("option","",`${stock.symbol} · ${stock.company}`);option.value=stock.symbol;return option;}));
    $("agent-cards").replaceChildren(...info.agents.map((agent,index)=>{const card=htmlElement("article","agent-card");const num=htmlElement("div","agent-number",`0${index+1} / AGENT`);num.append(htmlElement("span","done",agent.status));card.append(num,htmlElement("h3","",agent.name),htmlElement("p","",agent.detail));return card;}));
    $("stock-select").addEventListener("change",e=>loadSymbol(e.target.value));
    $("news-search").addEventListener("input",()=>{if(current)renderNews();});
    await loadSymbol(info.stocks[0].symbol);
  }catch(error){showError(error);}
}
init();
