from __future__ import annotations

import uuid

import pytest
from psycopg.types.json import Jsonb


def _seed():
    return {
        "characters": [
            {
                "name": "沈梧", "aliases": ["沈师兄"], "identity": "雾钟守护者",
                "background": "驻守北岸多年", "roleWeight": 0.9, "seniority": "穆蕾的师兄",
                "relationships": [
                    {"target": "穆蕾", "type": "同门", "label": "同门兄妹", "bidirectional": True}
                ],
            },
            {"name": "穆蕾", "identity": "夜航者"},
        ],
        "worldbook": [
            {"title": "雾钟", "category": "location", "content": "北岸旧驿站", "keywords": ["雾钟驿站"]}
        ],
        "foreshadows": [
            {"title": "铜铃", "status": "open", "detail": "第三声响起时开门", "chapterMin": 3}
        ],
        "outline": [
            {"title": "夜航开局", "kind": "arc", "summary": "沈梧带穆蕾抵达雾钟", "order": 1}
        ],
    }


def _fact():
    return {
        "chapter": 1, "title": "第一章", "viewpoint": "", "summary": "抵达雾钟", "story_phase": "开端",
        "story_time_label": "雨夜", "scene_count": 1, "token_estimate": 10,
        "characters": [{"name": "沈梧", "count": 1}], "locations": [], "factions": [],
        "concepts": [], "items": [], "relationships": [], "events": [], "confidence": 0.5,
    }


@pytest.fixture()
def env():
    from platform_app.db import connect, init_db
    from platform_app.knowledge._chunks import _upsert_chapter_fact, _upsert_document

    init_db()
    suffix = uuid.uuid4().hex[:10]
    with connect() as db:
        uid = db.execute(
            "insert into users(username,display_name) values (%s,'seed-test') returning id",
            (f"seed_test_{suffix}",),
        ).fetchone()["id"]
        sid = db.execute(
            "insert into scripts(owner_id,title,source_path,chapter_count,word_count,worldline_seed) "
            "values (%s,%s,%s,1,20,%s) returning id",
            (uid, f"seed-test-{suffix}", f"moneyai://releases/release_{suffix}", Jsonb(_seed())),
        ).fetchone()["id"]
        bid = db.execute(
            "insert into books(owner_id,script_id,title,slug) values (%s,%s,%s,%s) returning id",
            (uid, sid, "seed-test", f"seed-{suffix}"),
        ).fetchone()["id"]
        chapter = db.execute(
            "insert into script_chapters(script_id,chapter_index,title,content,word_count) "
            "values (%s,1,'第一章','沈梧带穆蕾抵达雾钟。',20) returning *",
            (sid,),
        ).fetchone()
        script = db.execute("select * from scripts where id=%s", (sid,)).fetchone()
        book = db.execute("select * from books where id=%s", (bid,)).fetchone()
        document = _upsert_document(db, book, script, chapter)
        _upsert_chapter_fact(db, book, script, chapter, document, _fact())
        save_id = db.execute(
            "insert into game_saves(user_id,script_id,title,state_path) values (%s,%s,%s,%s) returning id",
            (uid, sid, "seed-test", f"/tmp/seed-{suffix}.json"),
        ).fetchone()["id"]
        db.commit()
    try:
        yield {
            "connect": connect, "uid": uid, "sid": sid, "bid": bid, "save_id": save_id,
            "release_id": f"release_{suffix}",
        }
    finally:
        with connect() as db:
            db.execute("delete from game_saves where id=%s", (save_id,))
            db.execute("delete from books where id=%s", (bid,))
            db.execute("delete from scripts where id=%s", (sid,))
            db.execute("delete from users where id=%s", (uid,))
            db.commit()


def test_materialization_protection_and_steering(env):
    from extract.author_seed import materialize_author_seed
    from extract.resolve import CanonEntity, sync_character_cards_from_canon
    from gm_serving.steering import resolve_steering_target
    from kb import canon_repo

    with env["connect"]() as db:
        stats = materialize_author_seed(
            db, env["sid"], env["bid"], _seed(), release_id=env["release_id"],
        )
        assert stats["characters"] == 2
        assert stats["worldbook"] == 1
        assert stats["worldline_nodes"] == 2
        assert stats["relationships"] == 2

        canon = db.execute(
            "select * from kb_canon_entities where script_id=%s and name='沈梧'",
            (env["sid"],),
        ).fetchone()
        assert canon["attrs"]["source"] == "editor"
        assert canon["attrs"]["provenance"] == {"kind": "author_seed", "release_id": env["release_id"]}
        assert canon["identity"] == "雾钟守护者"

        # 同名 LLM canon/角色卡都不能越过作者保护类。
        protected = canon_repo.upsert_canon_entity(
            db, env["sid"], canon["logical_key"], name="沈梧", type="character",
            summary="LLM改写", identity="LLM身份", aliases=["错误别名"], attrs={},
        )
        assert protected is None
        sync_character_cards_from_canon(
            db, env["sid"],
            [CanonEntity(logical_key="沈梧", name="沈梧", type="character",
                         identity="LLM身份", background="LLM改写", aliases=["错误别名"])],
            book_id=env["bid"],
        )
        card = db.execute(
            "select * from character_cards where script_id=%s and name='沈梧' and card_type='npc'",
            (env["sid"],),
        ).fetchone()
        assert card["source"] == "editor"
        assert card["identity"] == "雾钟守护者"
        assert card["background"] == "驻守北岸多年"
        assert card["aliases"] == ["沈师兄"]
        assert card["metadata"]["author_fields"]["seniority"] == "穆蕾的师兄"
        assert card["metadata"]["provenance"]["kind"] == "author_seed"

        worldbook = db.execute(
            "select * from worldbook_entries where script_id=%s and title='雾钟'",
            (env["sid"],),
        ).fetchone()
        assert worldbook["content"] == "北岸旧驿站"
        assert worldbook["metadata"]["source"] == "editor"
        assert worldbook["metadata"]["provenance"]["kind"] == "author_seed"

        worldline = db.execute(
            "select * from script_worldlines where script_id=%s and wl_key='author_seed'",
            (env["sid"],),
        ).fetchone()
        assert worldline["source"] == "editor"
        assert worldline["metadata"]["provenance"]["kind"] == "author_seed"
        nodes = canon_repo.read_worldline_nodes(db, env["sid"], "author_seed")
        assert [node["node_key"] for node in nodes] == ["author_outline_1", "author_foreshadow_1"]
        steering = resolve_steering_target(
            db, save_id=env["save_id"], script_id=env["sid"], progress_chapter=1,
        )
        assert steering["worldline"] == "author_seed"
        assert steering["next_node"] == "author_outline_1"
        assert "夜航开局" in steering["soft_goal"]

        # 重放不制造重复行；物化后作者行仍是唯一权威版本。
        materialize_author_seed(db, env["sid"], env["bid"], _seed(), release_id=env["release_id"])
        assert db.execute(
            "select count(*) as c from character_cards where script_id=%s and card_type='npc'",
            (env["sid"],),
        ).fetchone()["c"] == 2
        assert db.execute(
            "select count(*) as c from kb_canon_entities where script_id=%s and name='沈梧'",
            (env["sid"],),
        ).fetchone()["c"] == 1

        # 同名跨类型各有 canon；已有生成卡时，作者揭示章/权重也优先于旧值。
        collision_seed = _seed()
        collision_seed["worldbook"].append({
            "title": "沈梧", "category": "location", "content": "与角色同名的地点",
        })
        materialize_author_seed(db, env["sid"], env["bid"], collision_seed, release_id=env["release_id"])
        same_name = db.execute(
            "select logical_key,type from kb_canon_entities where script_id=%s and name='沈梧'",
            (env["sid"],),
        ).fetchall()
        assert {row["type"] for row in same_name} == {"character", "location"}
        assert len({row["logical_key"] for row in same_name}) == 2
        db.execute(
            "insert into character_cards(book_id,script_id,name,card_type,source,scope,"
            "first_revealed_chapter,importance) values (%s,%s,'后期','npc','extracted','script',1,999)",
            (env["bid"], env["sid"]),
        )
        materialize_author_seed(
            db, env["sid"], env["bid"],
            {"characters": [{"name": "后期", "firstRevealedChapter": 20, "roleWeight": 0.5}]},
            release_id=env["release_id"],
        )
        late = db.execute(
            "select first_revealed_chapter,importance,source from character_cards "
            "where script_id=%s and name='后期'", (env["sid"],),
        ).fetchone()
        assert late["first_revealed_chapter"] == 20
        assert late["importance"] == 50
        assert late["source"] == "editor"
        db.commit()


def test_author_relations_survive_deterministic_rebuild(env):
    from platform_app.import_pipeline.rebuild_modules import rebuild_facts_from_db

    for _ in range(2):
        result = rebuild_facts_from_db(env["uid"], env["sid"])
        assert result["ok"] is True
    with env["connect"]() as db:
        rows = db.execute(
            "select chapter,relationships,metadata from chapter_facts where script_id=%s order by chapter",
            (env["sid"],),
        ).fetchall()
        assert [row["chapter"] for row in rows] == [1]  # 没有伪 ch0
        relations = rows[0]["relationships"]
        assert len(relations) == 2
        assert {(r["from"], r["to"], r["kind"]) for r in relations} == {
            ("沈梧", "穆蕾", "同门"), ("穆蕾", "沈梧", "同门"),
        }
        assert all(r["evidence"] == "同门兄妹" for r in relations)
        assert rows[0]["metadata"]["provenance"] == {
            "kind": "author_seed", "release_id": env["release_id"],
        }


def test_late_relationship_waits_for_reveal_chapter_in_facts_and_projection(env):
    from context_engine.projection import project_character_state
    from platform_app.import_pipeline.rebuild_modules import rebuild_facts_from_db

    seeded = _seed()
    seeded["characters"][0]["relationships"].append({
        "target": "后期人物", "type": "师徒", "label": "第五十章才揭示",
    })
    seeded["characters"].append({"name": "后期人物", "firstRevealedChapter": 50})
    with env["connect"]() as db:
        db.execute("update scripts set worldline_seed=%s where id=%s", (Jsonb(seeded), env["sid"]))
        db.execute(
            "insert into script_chapters(script_id,chapter_index,title,content,word_count) "
            "values (%s,50,'第五十章','后期人物正式拜师。',20)",
            (env["sid"],),
        )
        db.commit()
    assert rebuild_facts_from_db(env["uid"], env["sid"])["ok"] is True

    with env["connect"]() as db:
        rows = {
            row["chapter"]: row["relationships"]
            for row in db.execute(
                "select chapter,relationships from chapter_facts where script_id=%s order by chapter",
                (env["sid"],),
            ).fetchall()
        }
        assert len(rows[1]) == 2  # 原有的开局同门关系保留
        assert not any(rel["to"] == "后期人物" for rel in rows[1])
        assert rows[50] == [{
            "from": "沈梧", "to": "后期人物", "kind": "师徒", "evidence": "第五十章才揭示",
        }]
        early = project_character_state(
            db, env["sid"], "沈梧", progress_chapter=1, use_cache=False,
        )
        assert {rel["other"] for rel in early["relationships"]} == {"穆蕾"}
        late = project_character_state(
            db, env["sid"], "沈梧", progress_chapter=50, use_cache=False,
        )
        assert {rel["other"]: rel["kind"] for rel in late["relationships"]}["后期人物"] == "师徒"


def test_zero_llm_fallback_restores_author_cards_worldbook_and_relations(env):
    from platform_app.knowledge.session import sync_script_knowledge

    result = sync_script_knowledge(env["uid"], env["sid"], rebuild=True)
    assert result["author_seed"]["worldline_nodes"] == 2
    with env["connect"]() as db:
        card = db.execute(
            "select source,identity,metadata from character_cards "
            "where script_id=%s and name='沈梧' and card_type='npc'",
            (env["sid"],),
        ).fetchone()
        assert card["source"] == "editor"
        assert card["identity"] == "雾钟守护者"
        assert card["metadata"]["provenance"]["kind"] == "author_seed"
        worldbook = db.execute(
            "select content,metadata from worldbook_entries where script_id=%s and title='雾钟'",
            (env["sid"],),
        ).fetchone()
        assert worldbook["content"] == "北岸旧驿站"
        assert worldbook["metadata"]["source"] == "editor"
        relations = db.execute(
            "select relationships from chapter_facts where script_id=%s and chapter=1",
            (env["sid"],),
        ).fetchone()["relationships"]
        assert len(relations) == 2
        assert all(r["evidence"] == "同门兄妹" for r in relations)
