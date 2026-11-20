"""R2 retrieval observation tests; no DB, no model calls."""
import os

import pytest

from kb import recall as recall_mod
from kb.recall import (
    RecallResult,
    drain_turn_observations,
    retrieve_fn_compat,
    start_turn_observations,
)


def test_no_collector_attached_is_silent(monkeypatch):
    """累加器未挂(默认)时观测零行为——旧 caller/测试完全不受影响。"""
    monkeypatch.delenv("RPG_TKB_RECALL", raising=False)
    monkeypatch.setattr(recall_mod, "_recall_shadow", lambda: False)
    called = []

    def fake_old_path(query, *, state=None, user_id=None, script_id=None):
        called.append(query)
        return "旧路文本"

    import retrieval
    monkeypatch.setattr(retrieval, "retrieve_context", fake_old_path)

    out = retrieve_fn_compat("测试查询", script_id=7)
    assert out == "旧路文本"
    assert called == ["测试查询"]
    assert drain_turn_observations() == []


def test_legacy_path_records_observation(monkeypatch):
    """flag off(生产现状):走旧路,仍记录 query/path/体量。"""
    monkeypatch.setenv("RPG_TKB_RECALL", "off")
    import retrieval
    monkeypatch.setattr(retrieval, "retrieve_context",
                        lambda *a, **k: "旧路正文串")

    start_turn_observations()
    out = retrieve_fn_compat("查询铜牌下落", script_id=3)
    assert out == "旧路正文串"
    obs = drain_turn_observations()
    assert len(obs) == 1
    assert obs[0]["path"] == "legacy"
    assert obs[0]["query"] == "查询铜牌下落"
    assert obs[0]["script_id"] == 3
    assert obs[0]["returned_chars"] == len("旧路正文串")
    assert obs[0]["hits"] is None  # 旧路命中细节不采集
    assert drain_turn_observations() == []  # drain 后清空


def test_start_is_idempotent_across_turns(monkeypatch):
    """同线程下一回合 start 覆盖上一回合残留(线程复用不串数据)。"""
    monkeypatch.setenv("RPG_TKB_RECALL", "off")
    import retrieval
    monkeypatch.setattr(retrieval, "retrieve_context", lambda *a, **k: "x")

    start_turn_observations()
    retrieve_fn_compat("第一回合查询", script_id=1)
    # 模拟上一回合未 drain 的残留场景:直接重新 start
    start_turn_observations()
    retrieve_fn_compat("第二回合查询", script_id=1)
    obs = drain_turn_observations()
    assert len(obs) == 1
    assert obs[0]["query"] == "第二回合查询"


def test_recall_result_carries_vector_metadata():
    """RecallResult 新字段默认值不破坏既有构造/比较。"""
    result = RecallResult()
    assert result.vector_used is False
    assert result.degraded_reason is None


def test_record_helper_caps_query_length():
    """超长 query 截断到 200(表列约束对齐)。"""
    start_turn_observations()
    from kb.recall import _record_retrieval_observation
    _record_retrieval_observation(query="长" * 500, path="legacy", save_id=None,
                                  script_id=None, returned_chars=1)
    obs = drain_turn_observations()
    assert len(obs[0]["query"]) == 200


def test_record_helper_full_fields_from_result():
    """新路观测:hits/chunks/ceil/vector_used/degraded/missed 全量来自 RecallResult。"""
    start_turn_observations()
    from kb.recall import _record_retrieval_observation
    result = RecallResult(
        candidates=[{"node_kind": "character", "node_key": "char:掌柜",
                     "name": "掌柜", "first_revealed_chapter": 2}],
        chunks=[{"id": 1}, {"id": 2}],
        ceil_chap=5,
        vector_used=True,
        degraded_reason=None,
    )
    _record_retrieval_observation(query="掌柜是谁", path="new", save_id=9,
                                  script_id=4, result=result, returned_chars=120)
    obs = drain_turn_observations()
    assert obs[0]["path"] == "new"
    assert obs[0]["save_id"] == 9
    assert obs[0]["vector_used"] is True
    assert obs[0]["missed"] is False
    assert obs[0]["chunks_count"] == 2
    assert obs[0]["ceil_chap"] == 5
    assert obs[0]["hits"] == [
        {"kind": "character", "key": "char:掌柜", "name": "掌柜", "chap": 2},
    ]


def test_missed_semantics_on_empty_result():
    """空召回:missed=True(「本次未找到」,禁止解释为「作品中没有该事实」)。"""
    start_turn_observations()
    from kb.recall import _record_retrieval_observation
    result = RecallResult(candidates=[], chunks=[], ceil_chap=1,
                          degraded_reason="no_vectors")
    _record_retrieval_observation(query="不存在的东西", path="new", save_id=1,
                                  script_id=1, result=result, returned_chars=0)
    obs = drain_turn_observations()
    assert obs[0]["missed"] is True
    assert obs[0]["degraded_reason"] == "no_vectors"


def test_vector_dim_parser():
    from platform_app.knowledge._search import _vector_dim
    assert _vector_dim("[0.1,0.2,0.3]") == 3
    assert _vector_dim("[1]") == 1
    assert _vector_dim("not-a-vector") is None
    assert _vector_dim("[]") is None
    assert _vector_dim("") is None


def test_dim_mismatch_rejects_vector(monkeypatch):
    """维度不符 → 拒绝该向量(返回 None 降级),不进 SQL 混读。"""
    import platform_app.knowledge._search as search_mod

    class FakeDB:
        def execute(self, sql, params=()):
            class Row(dict):
                pass
            row = Row()
            row["embedding_vec"] = "[0.1,0.2,0.3,0.4]"  # 建库 4 维
            class Cur:
                def fetchone(self):
                    return row
            return Cur()

    monkeypatch.setattr(search_mod, "_script_vector_dim", lambda db, sid: 4)
    monkeypatch.setattr(search_mod, "_SCRIPT_VEC_DIM_CACHE", {}, {})

    from platform_app.knowledge.embedding import embed_query as _eq  # noqa: F401

    import platform_app.knowledge.embedding as emb
    monkeypatch.setattr(emb, "embed_query",
                        lambda text, **k: "[0.1,0.2,0.3]")  # 新嵌入 3 维

    out = search_mod._embed_query("文本", script_id=42, user_id=1, db=FakeDB())
    assert out is None  # 拒绝混读 → 降级


def test_dim_match_passes_vector(monkeypatch):
    """维度一致 → 正常返回向量。"""
    import platform_app.knowledge._search as search_mod
    monkeypatch.setattr(search_mod, "_script_vector_dim", lambda db, sid: 3)
    import platform_app.knowledge.embedding as emb
    monkeypatch.setattr(emb, "embed_query",
                        lambda text, **k: "[0.1,0.2,0.3]")
    out = search_mod._embed_query("文本", script_id=42, user_id=1, db=object())
    assert out == "[0.1,0.2,0.3]"
