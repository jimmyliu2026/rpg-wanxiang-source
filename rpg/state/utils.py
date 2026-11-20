"""state/utils.py — 通用工具函数 (_deep_update, _latest_assistant_text, _hit_score, _player_action_text)"""
from __future__ import annotations

import json
import re

from state.parsers import _clean_item


def _deep_update(target: dict, source: dict):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _latest_assistant_text(history: list[dict]) -> str:
    for msg in reversed(history or []):
        if msg.get("role") == "assistant":
            return str(msg.get("content") or "")
    return ""


def _hit_score(context: str, needles: tuple[str, ...]) -> int:
    return sum(8 for needle in needles if needle and needle in context)


def _player_action_text(text: str) -> str:
    text = _clean_item(text)
    text = re.sub(r"^(?:我|我们|你)\s*", "", text)
    text = re.sub(r"^(先|然后)\s*(?:我|我们|你)\s*", r"\1", text)
    return text.rstrip("。.!！?？")


def _item_projection_text(item) -> str:
    if isinstance(item, dict):
        name = item.get("name") or item.get("item_name") or item.get("item_id") or item.get("id")
        if name:
            quantity = item.get("quantity", item.get("qty"))
            return f"{name}×{quantity}" if quantity is not None else str(name)
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    return str(item)


def format_current_scene_projection(state_data: dict) -> str:
    """Read-only authoritative scene facts shared by GM and narrative microtasks.

    No recall/history inference and no private namespaces. Unknown stays unknown.
    """
    player = state_data.get("player") or {}
    world = state_data.get("world") or {}
    memory = state_data.get("memory") or {}
    character = state_data.get("player_character") or {}
    inventory = character.get("inventory")
    if not inventory:
        inventory = memory.get("resources") or []
    items = [_item_projection_text(item) for item in inventory]
    location = str(player.get("current_location") or "").strip()
    current_time = str(world.get("time") or "").strip()
    pending = (world.get("timeline") or {}).get("pending_jump")
    present = []
    trusted_sources = {"room_data", "encounter", "recorder"}
    inactive = {"absent", "departed", "left", "inactive", "dead", "removed"}
    for entity in state_data.get("active_entities") or []:
        if not isinstance(entity, dict) or entity.get("source") not in trusted_sources:
            continue
        status = str(entity.get("status") or "present").strip().lower()
        entity_location = str(entity.get("location") or "").strip()
        if status in inactive or (location and entity_location and entity_location != location):
            continue
        name = str(entity.get("name") or entity.get("id") or "").strip()
        if name:
            present.append(f"{name}（{entity_location or '位置未记录'}）")
    return "\n".join([
        "【权威场景投影】",
        f"当前地点：{location or '未记录'}",
        f"当前时间：{current_time or '未记录'}",
        "待确认时间跳转：" + (json.dumps(pending, ensure_ascii=False) if pending else "无"),
        "玩家持有（完整）：" + ("、".join(items) if items else "未记录"),
        "可信在场人物：" + ("、".join(present) if present else "未记录；不得因被提及而自行添加"),
        "投影未记录的状态不得从召回或人物谈论中推断。",
    ])
