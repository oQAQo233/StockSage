"""Tushare 市场数据采集与本地知识库；只依赖标准库，不在导入时联网。"""

from .client import TushareClient, TushareError
from .collector import collect_market_data
from .models import StockScope
from .store import MarketKnowledgeBase

__all__ = ["TushareClient", "TushareError", "StockScope", "MarketKnowledgeBase", "collect_market_data"]
