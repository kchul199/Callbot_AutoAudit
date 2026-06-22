"""
cp1_preprocessing/transcript.py
대화 직접 입력(붙여넣기) → CallLog 파싱 (대화 품질 검증 입력용).

지원 입력:
  1) 라인 transcript:
       고객: 요금제 알려줘
       콜봇: 5G 프리미엄은 월 69,000원입니다.
       [정답] 5G 프리미엄 요금제는 월 69,000원입니다.   (선택 — answer_correctness용)
  2) JSON: {"turns":[{"role":"user|bot","content":"..."}], "ground_truths":["..."]}

역할 키워드(한/영) 자동 인식. '[정답]'/'정답:' 라인은 직전 봇 답변의 기대답변으로 수집.
반환: (CallLog, ground_truths)  — ground_truths는 추출 순서대로 QA 쌍에 매핑.
"""
from __future__ import annotations

import json
import re
import uuid

from AutoAudit.app.core.types import CallLog, ConversationTurn, TurnRole

_USER_KW = ("고객", "사용자", "손님", "user", "customer", "q", "질문")
_BOT_KW = ("콜봇", "봇", "상담봇", "챗봇", "bot", "assistant", "agent", "a", "답변", "상담원")
_SYS_KW = ("시스템", "system")
_GT_KW = ("정답", "기대답변", "기대 답변", "expected", "gt", "ground_truth")

# 접두 추출: "[역할] 내용" / "[역할]: 내용" (대괄호) 또는 "역할: 내용" (콜론)
_BRACKET_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*[::]?\s*(.*)$")
_COLON_RE = re.compile(r"^\s*([가-힣A-Za-z _]{1,15}?)\s*[::]\s*(.*)$")


def _match_prefix(line: str):
    """라인에서 (역할라벨, 내용) 추출. 접두 없으면 None."""
    return _BRACKET_RE.match(line) or _COLON_RE.match(line)


def _role_of(label: str) -> str | None:
    low = label.strip().lower()
    if any(k in low for k in _GT_KW):
        return "ground_truth"
    if any(low == k or low.startswith(k) for k in _SYS_KW):
        return "system"
    if any(low == k or low.startswith(k) for k in _USER_KW):
        return "user"
    if any(low == k or low.startswith(k) for k in _BOT_KW):
        return "bot"
    return None


def _from_json(text: str, call_id: str) -> tuple[CallLog, list[str]]:
    raw = json.loads(text)
    if isinstance(raw, list):  # turns 배열만 준 경우
        raw = {"turns": raw}
    turns = [
        ConversationTurn(
            turn_id=i,
            role=TurnRole(str(t.get("role", "user")).lower() if str(t.get("role", "user")).lower()
                          in ("user", "bot", "system") else "user"),
            content=(t.get("content") or "").strip(),
        )
        for i, t in enumerate(raw.get("turns", []))
        if (t.get("content") or "").strip()
    ]
    gts = [str(g).strip() for g in raw.get("ground_truths", []) if str(g).strip()]
    # 턴별 expected/ground_truth도 수용
    if not gts:
        gts = [str(t.get("ground_truth") or t.get("expected") or "").strip()
               for t in raw.get("turns", []) if str(t.get("role", "")).lower() == "bot"]
        gts = [g for g in gts if g]
    return CallLog(call_id=raw.get("call_id", call_id),
                   subscriber_id=raw.get("subscriber_id", "직접입력"), turns=turns), gts


def _from_lines(text: str, call_id: str) -> tuple[CallLog, list[str]]:
    turns: list[ConversationTurn] = []
    ground_truths: list[str] = []
    cur_role: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = _match_prefix(line)
        role = _role_of(m.group(1)) if m else None
        if role == "ground_truth":
            ground_truths.append(m.group(2).strip())
            cur_role = None
            continue
        if role in ("user", "bot", "system"):
            content = m.group(2).strip()
            if content:
                turns.append(ConversationTurn(turn_id=len(turns), role=TurnRole(role), content=content))
                cur_role = role
            continue
        # 접두 없는 줄 → 직전 턴 내용 이어붙이기
        if turns and cur_role is not None:
            turns[-1].content += " " + line
    return CallLog(call_id=call_id, subscriber_id="직접입력", turns=turns), ground_truths


def parse_transcript(text: str, call_id: str | None = None) -> tuple[CallLog, list[str]]:
    """직접 입력 텍스트(transcript 또는 JSON) → (CallLog, ground_truths)."""
    text = (text or "").strip()
    if not text:
        return CallLog(call_id=call_id or "manual", subscriber_id="직접입력", turns=[]), []
    cid = call_id or f"manual_{uuid.uuid4().hex[:8]}"
    if text[0] in "{[":
        try:
            return _from_json(text, cid)
        except (json.JSONDecodeError, ValueError):
            pass  # JSON 실패 시 라인 파서로 폴백
    return _from_lines(text, cid)
