# -*- coding: utf-8 -*-
"""真·多 Agent 编排：4 个大师视角并行研究 → Team Lead 综合。

对应 ai-berkshire 的 /investment-team、/earnings-team 设计——
把 Claude Code 的 TeamCreate/Task 编排，搬到网页版后端用线程池实现。
事件经队列流式转发（role_start / role_done / synthesis_start / delta / done）。
"""
from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterator

from . import prompt_adapter as pa
from .llm_client import stream_research

logger = logging.getLogger(__name__)

TEAM_SKILLS = {"investment-team", "earnings-team", "investment-research"}


def _run_role(role: dict, prompt: str, user_id: str, cancelled=None) -> str:
    """单角色研究（工作线程执行，完整收集输出）。"""
    chunks = []
    if cancelled is not None and cancelled.is_set():
        return ""
    for chunk in stream_research(prompt, user_id):
        if cancelled is not None and cancelled.is_set():
            break
        chunks.append(chunk)
    return "".join(chunks)


def iter_team_research(skill_content: str, arguments: str,
                       skill_id: str = "", market: str = "all",
                       params: dict | None = None,
                       user_id: str = "") -> Iterator[dict]:
    """流式产出多 Agent 研究事件。"""
    q: "queue.Queue[dict | None]" = queue.Queue()
    cancelled = threading.Event()

    def worker() -> None:
        try:
            sections: list[dict] = []
            failed_roles = 0
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {}
                for role in pa.ROLES:
                    q.put({"type": "role_start",
                           "role_id": role["id"], "label": role["label"]})
                    prompt = pa.build_role_prompt(role, skill_content, arguments,
                                                  skill_id=skill_id, market=market,
                                                  params=params)
                    futures[pool.submit(_run_role, role, prompt, user_id, cancelled)] = role
                for fut in as_completed(futures):
                    role = futures[fut]
                    role_failed = False
                    try:
                        text = fut.result()
                    except Exception:
                        logger.exception("Research role failed: %s", role["id"])
                        role_failed = True
                        failed_roles += 1
                        q.put({"type":"role_error", "role_id":role["id"], "label":role["label"]})
                        text = "（本视角未完成，以下综合结论不包含此视角的有效证据）"
                    sections.append({"label": role["label"], "text": text,
                                     "role_id": role["id"]})
                    if not role_failed:
                        q.put({"type": "role_done", "role_id": role["id"],
                               "label": role["label"], "text": text})

            if cancelled.is_set():
                return
            if failed_roles == len(pa.ROLES):
                raise RuntimeError("所有研究视角均未完成")
            order = {r["id"]: i for i, r in enumerate(pa.ROLES)}
            sections.sort(key=lambda s: order.get(s["role_id"], 99))

            q.put({"type": "synthesis_start"})
            synth_prompt = pa.build_synthesis_prompt(arguments, sections)
            buf: list[str] = []
            for chunk in stream_research(synth_prompt, user_id):
                if cancelled.is_set():
                    return
                buf.append(chunk)
                q.put({"type": "delta", "text": chunk})
            full = "".join(buf).strip()
            if failed_roles:
                full = f"> 注意：{failed_roles} 个研究视角未完成，本报告仅基于已完成视角综合。\n\n" + full
            q.put({"type": "done", "full_text": full})
        except Exception:
            logger.exception("Multi-view research failed")
            q.put({"type": "error", "message": "多视角研究未能完成，请稍后重试或检查模型配置"})
        finally:
            q.put(None)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    try:
        while True:
            item = q.get()
            if item is None:
                break
            yield item
    finally:
        cancelled.set()
        # Keep the usage reservation until in-flight role calls have actually
        # returned, so deployment cannot interrupt them after a disconnect.
        worker_thread.join()
