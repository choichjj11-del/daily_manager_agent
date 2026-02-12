#!/usr/bin/env python3
"""
데일리 매니저 — Brian's Daily Manager Telegram Bot
아침 다짐 검증 + 랜덤 진행 체크 + 저녁 리포트 + 스트릭 + 주간 회고
"""

import os
import json
import random
import re
import logging
import asyncio
from datetime import datetime, timedelta, time
from pathlib import Path
from io import BytesIO

import pytz
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

# ─── 설정 ───────────────────────────────────────────────
BOT_TOKEN = os.environ["DAILY_MANAGER_BOT_TOKEN"]
CHAT_ID = int(os.environ["BRIAN_CHAT_ID"])
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data/daily-manager"))
TZ = pytz.timezone("Asia/Seoul")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("daily-manager")

# ─── 데이터 디렉토리 초기화 ─────────────────────────────
def init_data():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "chat_history").mkdir(exist_ok=True)

    if not (DATA_DIR / "affirmation.md").exists():
        (DATA_DIR / "affirmation.md").write_text(DEFAULT_AFFIRMATION, encoding="utf-8")

    if not (DATA_DIR / "streaks.json").exists():
        (DATA_DIR / "streaks.json").write_text(json.dumps({
            "affirmation": {"current": 0, "best": 0, "last_date": ""},
            "todo_completion": {"current": 0, "best": 0, "last_date": ""},
            "revenue_report": {"current": 0, "best": 0, "last_date": ""},
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    if not (DATA_DIR / "config.json").exists():
        (DATA_DIR / "config.json").write_text(json.dumps({
            "monthly_revenue_target": 10000000,
            "affirmation_time": "07:30",
            "report_time": "21:00",
            "check_windows": [
                {"start": "10:00", "end": "11:00"},
                {"start": "13:30", "end": "16:00"},
                {"start": "16:00", "end": "20:00"},
            ],
            "affirmation_match_threshold": 0.70,
            "affirmation_max_retries": 3,
            "timezone": "Asia/Seoul",
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    if not (DATA_DIR / "revenue_log.md").exists():
        now = datetime.now(TZ)
        (DATA_DIR / "revenue_log.md").write_text(
            f"# {now.year}년 {now.month}월 수익 기록\n\n"
            "| 날짜 | 금액 | 누적 | 목표대비 |\n"
            "|------|------|------|--------|\n",
            encoding="utf-8",
        )

    today = datetime.now(TZ).strftime("%Y-%m-%d")
    todos_file = DATA_DIR / "todos_today.md"
    if todos_file.exists():
        content = todos_file.read_text(encoding="utf-8")
        if today not in content:
            todos_file.write_text(f"# {today} 할 일\n\n", encoding="utf-8")
    else:
        todos_file.write_text(f"# {today} 할 일\n\n", encoding="utf-8")


DEFAULT_AFFIRMATION = """나는 600억의 자산을 만들어가는 사람이다.
이건 꿈이 아니라 내가 설계한 경로다.

나는 오케스트레이터다.
사람들이 인사이트를 얻기 위해 나를 찾고,
내 판단을 신뢰하고, 내 시스템 안에서 움직인다.

나는 원래 지독한 사람이다.
한번 물면 끝을 보고, 디테일 하나까지 놓치지 않는다.
이건 노력이 아니라 내 본성이다.

나는 깨어있다.
대부분이 잠들어 있을 때,
나는 구조를 보고, 흐름을 읽고, 실행하고 있다.
나는 훨씬 더 깨어있다.

나는 소비자가 아니라 생산자다.
보는 사람이 아니라 만드는 사람이다.
오늘도 나는 콘텐츠를 만들고, 시스템을 짓고,
돈이 흐르는 길을 설계한다.

준비는 끝났다. 도구도 있다. 팀도 있다.
남은 건 실행뿐이다. 지금 이 순간부터."""


# ─── 유틸리티 ───────────────────────────────────────────
def now_kst() -> datetime:
    return datetime.now(TZ)


def today_str() -> str:
    return now_kst().strftime("%Y-%m-%d")


def load_config() -> dict:
    return json.loads((DATA_DIR / "config.json").read_text(encoding="utf-8"))


def load_streaks() -> dict:
    return json.loads((DATA_DIR / "streaks.json").read_text(encoding="utf-8"))


def save_streaks(data: dict):
    (DATA_DIR / "streaks.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_affirmation() -> str:
    return (DATA_DIR / "affirmation.md").read_text(encoding="utf-8").strip()


def load_todos() -> list[dict]:
    path = DATA_DIR / "todos_today.md"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    todos = []
    for line in lines:
        line = line.strip()
        if line.startswith("- [x]"):
            todos.append({"text": line[6:].strip(), "done": True})
        elif line.startswith("- [ ]"):
            todos.append({"text": line[6:].strip(), "done": False})
    return todos


def save_todos(todos: list[dict]):
    today = today_str()
    lines = [f"# {today} 할 일\n"]
    for t in todos:
        mark = "x" if t["done"] else " "
        lines.append(f"- [{mark}] {t['text']}")
    (DATA_DIR / "todos_today.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_incomplete_todos() -> list[dict]:
    return [t for t in load_todos() if not t["done"]]


def get_complete_todos() -> list[dict]:
    return [t for t in load_todos() if t["done"]]


def update_streak(key: str, success: bool):
    streaks = load_streaks()
    today = today_str()
    s = streaks[key]

    if success:
        if s["last_date"] == today:
            return s  # 이미 오늘 업데이트됨
        yesterday = (now_kst() - timedelta(days=1)).strftime("%Y-%m-%d")
        if s["last_date"] == yesterday or s["current"] == 0:
            s["current"] += 1
        else:
            s["current"] = 1
        s["last_date"] = today
        if s["current"] > s["best"]:
            s["best"] = s["current"]
    else:
        s["current"] = 0

    streaks[key] = s
    save_streaks(streaks)
    return s


def check_similarity(original: str, transcribed: str) -> float:
    def clean(text):
        text = re.sub(r"[^\w\s]", "", text)
        return set(text.lower().split())

    orig_words = clean(original)
    trans_words = clean(transcribed)
    if not orig_words:
        return 0.0
    matched = orig_words & trans_words
    return len(matched) / len(orig_words)


def get_monthly_revenue() -> tuple[int, list]:
    path = DATA_DIR / "revenue_log.md"
    if not path.exists():
        return 0, []
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    entries = []
    total = 0
    now = now_kst()
    current_month = f"{now.month:02d}-"
    for line in lines:
        if line.startswith("|") and current_month in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                try:
                    amount = int(parts[1].replace(",", "").replace("원", ""))
                    entries.append({"date": parts[0], "amount": amount})
                    total += amount
                except ValueError:
                    pass
    return total, entries


def add_revenue(amount: int):
    path = DATA_DIR / "revenue_log.md"
    now = now_kst()
    total, _ = get_monthly_revenue()
    new_total = total + amount
    config = load_config()
    target = config["monthly_revenue_target"]
    pct = round(new_total / target * 100, 1) if target > 0 else 0

    date_str = now.strftime("%m-%d")
    line = f"| {date_str} | {amount:,} | {new_total:,} | {pct}% |\n"

    content = path.read_text(encoding="utf-8")
    content += line
    path.write_text(content, encoding="utf-8")

    return new_total, pct


def log_chat(role: str, message: str):
    today = today_str()
    path = DATA_DIR / "chat_history" / f"{today}.md"
    timestamp = now_kst().strftime("%H:%M")
    entry = f"[{timestamp}] {role}: {message}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)


# ─── Whisper API 호출 ───────────────────────────────────
async def transcribe_voice(voice_bytes: bytes) -> str:
    """OpenAI Whisper API로 음성을 텍스트로 변환"""
    import httpx

    if not OPENAI_API_KEY:
        log.warning("OPENAI_API_KEY가 설정되지 않아 Whisper를 사용할 수 없습니다.")
        return ""

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            files={"file": ("voice.ogg", voice_bytes, "audio/ogg")},
            data={"model": "whisper-1", "language": "ko"},
        )
        if resp.status_code == 200:
            return resp.json().get("text", "")
        else:
            log.error(f"Whisper API 에러: {resp.status_code} {resp.text}")
            return ""


# ─── 상태 관리 ──────────────────────────────────────────
class BotState:
    def __init__(self):
        self.awaiting_affirmation = False
        self.affirmation_retries = 0
        self.awaiting_revenue = False
        self.check_times: list[datetime] = []

    def reset_daily(self):
        self.awaiting_affirmation = False
        self.affirmation_retries = 0
        self.awaiting_revenue = False
        self.generate_check_times()

    def generate_check_times(self):
        today = now_kst().date()
        times = []
        windows = load_config()["check_windows"]
        for w in windows:
            start_h, start_m = map(int, w["start"].split(":"))
            end_h, end_m = map(int, w["end"].split(":"))
            start_min = start_h * 60 + start_m
            end_min = end_h * 60 + end_m
            rand_min = random.randint(start_min, end_min)
            t = datetime.combine(today, time(rand_min // 60, rand_min % 60))
            t = TZ.localize(t)
            times.append(t)
        self.check_times = sorted(times)
        log.info(f"오늘 체크 시간: {[t.strftime('%H:%M') for t in self.check_times]}")


state = BotState()

# ─── 체크 문구 풀 ───────────────────────────────────────
CHECK_MESSAGES_1 = [
    "안녕하세요. {todo} 진행하시기로 하셨는데, 시작하셨나요?",
    "오전 중으로 {todo} 하시면 오후가 편하실 것 같습니다. 진행 중이신가요?",
    "혹시 {todo} 시작 전에 막히는 부분이 있으시면 말씀해 주세요.",
    "{todo} 관련해서 오늘 어떻게 접근하실 계획이신가요?",
]

CHECK_MESSAGES_2 = [
    "오전에 어떻게 진행되셨나요? {todo}은 완료되셨습니까?",
    "점심 이후로 {todo} 이어서 하시면 좋을 것 같은데, 어떠신가요?",
    "혹시 오전에 계획대로 안 되신 거 있으시면, 지금 우선순위 조정하셔도 됩니다.",
    "남은 오후 시간에 {todo} 집중해보시는 건 어떨까요?",
]

CHECK_MESSAGES_3 = [
    "오늘 마무리 시간이 다가오고 있습니다. {todo}은 어떻게 되셨나요?",
    "오늘 안에 {todo}까지 끝내시면 내일이 훨씬 수월해지실 겁니다.",
    "혹시 오늘 못 끝내신 것 중에 내일 1순위로 넘길 게 있으시면 말씀해 주세요.",
    "하루 마무리 전에 {todo} 상태만 한번 확인 부탁드립니다.",
]

CHECK_POOLS = [CHECK_MESSAGES_1, CHECK_MESSAGES_2, CHECK_MESSAGES_3]


# ─── 스케줄 작업들 ──────────────────────────────────────
async def send_affirmation(bot: Bot):
    """07:30 아침 다짐 발송"""
    state.awaiting_affirmation = True
    state.affirmation_retries = 0
    affirmation = load_affirmation()

    msg = (
        "좋은 아침입니다. 오늘의 다짐입니다.\n"
        "소리 내서 읽으신 후 음성 메시지로 보내주세요.\n\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"{affirmation}\n\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await bot.send_message(chat_id=CHAT_ID, text=msg)
    log_chat("봇", "아침 다짐 발송")
    log.info("아침 다짐 발송 완료")


async def send_affirmation_reminder(bot: Bot):
    """09:00 다짐 미완료 시 리마인드"""
    if state.awaiting_affirmation:
        await bot.send_message(
            chat_id=CHAT_ID,
            text="아침 다짐을 아직 하지 않으셨습니다.\n지금이라도 읽어서 보내주시겠어요?",
        )
        log_chat("봇", "다짐 리마인드")


async def send_check_message(bot: Bot, check_index: int):
    """랜덤 진행 체크"""
    incomplete = get_incomplete_todos()
    if not incomplete:
        return

    todo = random.choice(incomplete)["text"]
    pool = CHECK_POOLS[min(check_index, len(CHECK_POOLS) - 1)]
    msg = random.choice(pool).format(todo=todo)

    await bot.send_message(chat_id=CHAT_ID, text=msg)
    log_chat("봇", f"진행체크 {check_index + 1}차: {msg}")
    log.info(f"진행체크 {check_index + 1}차 발송")


async def send_evening_report(bot: Bot):
    """21:00 저녁 리포트"""
    # 일요일이면 주간 회고
    if now_kst().weekday() == 6:
        await send_weekly_review(bot)
        return

    todos = load_todos()
    done = [t for t in todos if t["done"]]
    undone = [t for t in todos if not t["done"]]

    # 할 일 완료 스트릭
    if len(done) > 0:
        update_streak("todo_completion", True)

    undone_text = ""
    if undone:
        undone_text = "\n".join([f"  · {t['text']}" for t in undone])
    else:
        undone_text = "  (전부 완료!)"

    msg = (
        "오늘 하루 마무리 시간입니다.\n\n"
        f"📋 할 일 현황:\n"
        f"✅ 완료: {len(done)}개\n"
        f"⬜ 미완료: {len(undone)}개\n"
        f"{undone_text}\n\n"
        "오늘 수익이 발생한 게 있으시면 금액을 알려주세요.\n"
        "없으시면 '없음'이라고 해주시면 됩니다."
    )
    state.awaiting_revenue = True
    await bot.send_message(chat_id=CHAT_ID, text=msg)
    log_chat("봇", "저녁 리포트 발송")
    log.info("저녁 리포트 발송")


async def send_weekly_review(bot: Bot):
    """일요일 주간 회고"""
    streaks = load_streaks()
    config = load_config()
    target = config["monthly_revenue_target"]
    now = now_kst()

    # 이번 주 할 일 데이터 — chat_history에서 집계
    # 간단하게 스트릭 + 수익 데이터로 구성
    monthly_total, entries = get_monthly_revenue()
    week_revenue = 0
    week_start = (now - timedelta(days=7)).strftime("%m-%d")
    for e in entries:
        if e["date"] >= week_start:
            week_revenue += e["amount"]

    pct = round(monthly_total / target * 100, 1) if target > 0 else 0
    days_left = (datetime(now.year, now.month + 1 if now.month < 12 else 1, 1, tzinfo=TZ) - now).days

    # 완료율 코멘트
    aff_streak = streaks["affirmation"]["current"]
    todo_streak = streaks["todo_completion"]["current"]

    if todo_streak >= 5:
        comment = "이번 주 실행력이 좋으셨습니다. 이 페이스 유지하시면 됩니다."
    elif todo_streak >= 3:
        comment = "절반 이상 해내셨습니다. 다음 주는 좀 더 속도 내보시죠."
    else:
        comment = "이번 주는 좀 밀리셨습니다. 다음 주 할 일을 줄이시거나 우선순위를 좁혀보시는 건 어떨까요?"

    week_num = (now.day - 1) // 7 + 1
    msg = (
        f"📊 주간 회고 — {now.month}월 {week_num}째주\n\n"
        f"🎯 아침 다짐 스트릭: 연속 {aff_streak}일째\n\n"
        f"📋 할 일 완료 스트릭: 연속 {todo_streak}일째\n\n"
        f"💰 수익:\n"
        f"  이번 주: {week_revenue:,}원\n"
        f"  이번 달 누적: {monthly_total:,}원\n"
        f"  월 목표 대비: {pct}%\n"
        f"  남은 일수: {days_left}일\n\n"
        f"{comment}\n\n"
        "다음 주에 집중하실 부분이 있으시면 말씀해 주세요.\n"
        "수고 많으셨습니다."
    )

    # 수익 질문도 같이
    state.awaiting_revenue = True
    await bot.send_message(chat_id=CHAT_ID, text=msg)
    await bot.send_message(
        chat_id=CHAT_ID,
        text="오늘 수익이 발생한 게 있으시면 금액을 알려주세요.\n없으시면 '없음'이라고 해주시면 됩니다.",
    )
    log_chat("봇", "주간 회고 발송")
    log.info("주간 회고 발송")


# ─── 메시지 핸들러 ──────────────────────────────────────
async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """음성 메시지 처리 (아침 다짐 검증)"""
    if update.effective_chat.id != CHAT_ID:
        return
    if not state.awaiting_affirmation:
        return

    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    buf = BytesIO()
    await file.download_to_memory(buf)
    voice_bytes = buf.getvalue()

    log_chat("Brian", "[음성 메시지]")

    transcribed = await transcribe_voice(voice_bytes)
    if not transcribed:
        await update.message.reply_text(
            "음성 인식에 문제가 있었습니다. 한 번 더 보내주시겠어요?"
        )
        return

    log.info(f"Whisper 결과: {transcribed[:100]}...")

    affirmation = load_affirmation()
    similarity = check_similarity(affirmation, transcribed)
    log.info(f"유사도: {similarity:.2%}")

    config = load_config()
    threshold = config["affirmation_match_threshold"]

    if similarity >= threshold:
        state.awaiting_affirmation = False
        s = update_streak("affirmation", True)
        streak_msg = f"\n아침 다짐 연속 {s['current']}일째입니다."
        if s["current"] == s["best"] and s["current"] > 1:
            streak_msg += " 새로운 최고 기록입니다."

        await update.message.reply_text(
            f"확인했습니다. (일치율 {similarity:.0%}){streak_msg}\n"
            "오늘도 좋은 하루 시작하시죠.\n\n"
            "오늘 할 일이 있으시면 말씀해 주세요."
        )
        log_chat("봇", f"다짐 통과 ({similarity:.0%})")
    else:
        state.affirmation_retries += 1
        max_retries = config["affirmation_max_retries"]

        if state.affirmation_retries >= max_retries:
            state.awaiting_affirmation = False
            update_streak("affirmation", False)
            await update.message.reply_text(
                "노력해 주신 거 확인했습니다.\n"
                "다짐 텍스트 다시 한번 눈으로 읽어보시고, 오늘도 시작하시죠.\n\n"
                "오늘 할 일이 있으시면 말씀해 주세요."
            )
            log_chat("봇", f"다짐 3회 실패 — 강제 통과")
        else:
            remaining = max_retries - state.affirmation_retries
            await update.message.reply_text(
                f"조금 더 정확하게 읽어주시면 좋겠습니다. (일치율 {similarity:.0%})\n"
                f"한 번 더 부탁드리겠습니다. ({remaining}회 남음)"
            )
            log_chat("봇", f"다짐 재시도 요청 ({similarity:.0%})")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """텍스트 메시지 처리"""
    if update.effective_chat.id != CHAT_ID:
        return

    text = update.message.text.strip()
    log_chat("Brian", text)

    # ── 수익 응답 처리 ──
    if state.awaiting_revenue:
        if text in ("없음", "없어", "0", "없습니다"):
            state.awaiting_revenue = False
            update_streak("revenue_report", True)
            streaks = load_streaks()
            await update.message.reply_text(
                "확인했습니다. 내일 할 일이 있으시면 지금 말씀해 주세요.\n"
                "아니면 내일 아침에 정리해 주셔도 됩니다.\n"
                "수고하셨습니다."
            )
            log_chat("봇", "수익: 없음")
            return

        # 숫자 추출
        amount = extract_number(text)
        if amount is not None:
            state.awaiting_revenue = False
            new_total, pct = add_revenue(amount)
            update_streak("revenue_report", True)

            config = load_config()
            target = config["monthly_revenue_target"]
            now = now_kst()
            days_left = (datetime(now.year, now.month + 1 if now.month < 12 else 1, 1, tzinfo=TZ) - now).days

            if pct >= 80:
                comment = "목표에 거의 다 오셨습니다. 이 페이스 유지하시면 됩니다."
            elif pct >= 50:
                comment = "절반 이상 오셨습니다. 남은 기간 집중하시면 충분히 가능합니다."
            elif pct >= 30:
                comment = "조금 더 속도를 내셔야 할 것 같습니다. 내일 수익 파이프라인 우선 점검해보시죠."
            else:
                comment = "현재 페이스로는 목표 달성이 어렵습니다. 내일 전략 재점검이 필요할 것 같은데, 시간 내보시겠어요?"

            await update.message.reply_text(
                f"확인했습니다.\n\n"
                f"💰 이번 달 수익 현황:\n"
                f"  오늘: {amount:,}원\n"
                f"  이번 달 누적: {new_total:,}원\n"
                f"  월 목표 {target // 10000:,}만원 대비: {pct}%\n"
                f"  남은 일수: {days_left}일\n\n"
                f"{comment}\n\n"
                "내일 할 일이 있으시면 지금 말씀해 주세요.\n"
                "아니면 내일 아침에 정리해 주셔도 됩니다.\n"
                "수고하셨습니다."
            )
            log_chat("봇", f"수익 기록: {amount:,}원 → 누적 {new_total:,}원 ({pct}%)")
            return

    # ── 할 일 등록 ──
    if any(text.startswith(k) for k in ("오늘 할 일", "할일:", "할 일:", "투두:", "todo:")):
        items = parse_todo_items(text)
        if items:
            todos = load_todos()
            for item in items:
                todos.append({"text": item, "done": False})
            save_todos(todos)
            items_text = "\n".join([f"  {i + 1}. {item}" for i, item in enumerate(items)])
            await update.message.reply_text(
                f"오늘 할 일 등록했습니다.\n{items_text}\n\n"
                "순서대로 진행하시면 될까요, 아니면 우선순위 조정하실 건가요?"
            )
            log_chat("봇", f"할 일 {len(items)}개 등록")
            return

    # ── 할 일 추가 ──
    if text.startswith("추가:") or text.startswith("추가 :"):
        item = text.split(":", 1)[1].strip()
        if item:
            todos = load_todos()
            todos.append({"text": item, "done": False})
            save_todos(todos)
            await update.message.reply_text(
                f"추가했습니다. 현재 할 일 {len(todos)}개입니다."
            )
            log_chat("봇", f"할 일 추가: {item}")
            return

    # ── 할 일 완료 ──
    done_keywords = ("했어", "완료", "끝", "했습니다", "끝냈", "다했", "클리어")
    if any(k in text for k in done_keywords):
        todos = load_todos()
        matched = None
        # 번호로 완료 ("1번 완료", "2번 했어")
        num_match = re.search(r"(\d+)\s*번", text)
        if num_match:
            idx = int(num_match.group(1)) - 1
            incomplete = [(i, t) for i, t in enumerate(todos) if not t["done"]]
            if 0 <= idx < len(incomplete):
                real_idx = incomplete[idx][0]
                todos[real_idx]["done"] = True
                matched = todos[real_idx]["text"]

        # 키워드 매칭
        if not matched:
            for t in todos:
                if not t["done"]:
                    # 할 일 텍스트의 단어가 메시지에 포함되어 있으면
                    words = t["text"].lower().split()
                    msg_lower = text.lower()
                    if any(w in msg_lower for w in words if len(w) > 1):
                        t["done"] = True
                        matched = t["text"]
                        break

        if matched:
            save_todos(todos)
            remaining = get_incomplete_todos()
            if remaining:
                remaining_text = ", ".join([t["text"] for t in remaining])
                await update.message.reply_text(
                    f"{matched} 완료 확인했습니다.\n"
                    f"남은 할 일: {remaining_text}"
                )
            else:
                await update.message.reply_text(
                    f"{matched} 완료 확인했습니다.\n"
                    "오늘 할 일을 전부 완료하셨습니다! 수고하셨습니다."
                )
            log_chat("봇", f"할 일 완료: {matched}")
            return

    # ── 패스/안 할 거야 ──
    if any(k in text for k in ("패스", "안 할", "안할", "넘길", "내일로")):
        todos = load_todos()
        for t in todos:
            if not t["done"]:
                # 첫 번째 미완료 항목을 내일로
                await update.message.reply_text(
                    f"알겠습니다. '{t['text']}' 항목은 내일로 이동하겠습니다."
                )
                log_chat("봇", f"내일로 이동: {t['text']}")
                return

    # ── 할 일 목록 확인 ──
    if any(k in text for k in ("할 일 목록", "뭐 남았", "남은 거", "할일 뭐", "투두 목록")):
        todos = load_todos()
        if not todos:
            await update.message.reply_text("등록된 할 일이 없습니다. 할 일을 알려주세요.")
        else:
            lines = []
            for i, t in enumerate(todos):
                mark = "✅" if t["done"] else "⬜"
                lines.append(f"  {mark} {i + 1}. {t['text']}")
            await update.message.reply_text(
                "📋 오늘 할 일:\n" + "\n".join(lines)
            )
        return

    # ── 기타 메시지 (인식 안 됨) ──
    # 아무 반응 안 함 — 불필요한 API 호출 방지


def extract_number(text: str) -> int | None:
    """텍스트에서 숫자 추출 (만원, 원 단위 처리)"""
    text = text.replace(",", "").replace(" ", "")

    # "45만원" → 450000
    m = re.search(r"(\d+)\s*만\s*원?", text)
    if m:
        return int(m.group(1)) * 10000

    # "450000원" or "450000"
    m = re.search(r"(\d+)\s*원?", text)
    if m:
        val = int(m.group(1))
        if val > 0:
            return val

    return None


def parse_todo_items(text: str) -> list[str]:
    """할 일 텍스트 파싱"""
    # "오늘 할 일: A, B, C" 또는 "할일: A, B, C"
    if ":" in text:
        text = text.split(":", 1)[1].strip()

    # 콤마, 줄바꿈, "그리고" 등으로 분리
    items = re.split(r"[,\n]|그리고", text)
    return [item.strip() for item in items if item.strip()]


# ─── 명령어 ─────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    await update.message.reply_text(
        "데일리 매니저 가동 중입니다.\n\n"
        "사용 가능한 명령:\n"
        "/status — 오늘 현황 확인\n"
        "/streak — 스트릭 확인\n"
        "/affirmation — 다짐 문구 확인\n\n"
        "할 일 등록: '오늘 할 일: A, B, C'\n"
        "할 일 완료: 'A 했어' 또는 '1번 완료'\n"
        "할 일 추가: '추가: D'"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    todos = load_todos()
    done = [t for t in todos if t["done"]]
    undone = [t for t in todos if not t["done"]]
    monthly_total, _ = get_monthly_revenue()
    config = load_config()
    target = config["monthly_revenue_target"]
    pct = round(monthly_total / target * 100, 1) if target > 0 else 0

    lines = [f"📋 할 일: ✅{len(done)}개 / ⬜{len(undone)}개"]
    for t in todos:
        mark = "✅" if t["done"] else "⬜"
        lines.append(f"  {mark} {t['text']}")
    lines.append(f"\n💰 이번 달: {monthly_total:,}원 (목표 대비 {pct}%)")

    await update.message.reply_text("\n".join(lines))


async def cmd_streak(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    streaks = load_streaks()
    msg = (
        "🔥 스트릭 현황:\n\n"
        f"아침 다짐: 연속 {streaks['affirmation']['current']}일 "
        f"(최고 {streaks['affirmation']['best']}일)\n"
        f"할 일 완료: 연속 {streaks['todo_completion']['current']}일 "
        f"(최고 {streaks['todo_completion']['best']}일)\n"
        f"수익 보고: 연속 {streaks['revenue_report']['current']}일 "
        f"(최고 {streaks['revenue_report']['best']}일)"
    )
    await update.message.reply_text(msg)


async def cmd_affirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != CHAT_ID:
        return
    await update.message.reply_text(load_affirmation())


# ─── 스케줄러 ───────────────────────────────────────────
async def scheduler(app: Application):
    """메인 스케줄 루프"""
    bot = app.bot
    init_data()
    state.reset_daily()

    last_affirmation_date = ""
    last_reminder_date = ""
    last_report_date = ""
    check_sent = [False, False, False]

    log.info("스케줄러 시작")

    while True:
        try:
            now = now_kst()
            today = today_str()
            current_time = now.strftime("%H:%M")

            # 자정 리셋
            if today != last_affirmation_date and current_time >= "00:01":
                if last_affirmation_date:  # 첫 실행이 아닐 때만
                    state.reset_daily()
                    check_sent = [False, False, False]
                    last_report_date = ""
                    # todos 리셋
                    todos_file = DATA_DIR / "todos_today.md"
                    todos_file.write_text(f"# {today} 할 일\n\n", encoding="utf-8")
                    log.info(f"일일 리셋: {today}")

            # 07:30 아침 다짐
            if current_time >= "07:30" and last_affirmation_date != today:
                last_affirmation_date = today
                await send_affirmation(bot)

            # 09:00 다짐 리마인드
            if current_time >= "09:00" and last_reminder_date != today:
                last_reminder_date = today
                await send_affirmation_reminder(bot)

            # 랜덤 진행 체크
            for i, check_time in enumerate(state.check_times):
                if not check_sent[i] and now >= check_time:
                    check_sent[i] = True
                    await send_check_message(bot, i)

            # 21:00 저녁 리포트
            if current_time >= "21:00" and last_report_date != today:
                last_report_date = today
                await send_evening_report(bot)

        except Exception as e:
            log.error(f"스케줄러 에러: {e}", exc_info=True)

        await asyncio.sleep(30)  # 30초마다 체크


# ─── 메인 ───────────────────────────────────────────────
def main():
    init_data()
    log.info("데일리 매니저 시작")

    app = Application.builder().token(BOT_TOKEN).build()

    # 핸들러 등록
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("streak", cmd_streak))
    app.add_handler(CommandHandler("affirmation", cmd_affirmation))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # 스케줄러를 백그라운드로 실행
    loop = asyncio.get_event_loop()

    async def post_init(app: Application):
        asyncio.create_task(scheduler(app))
        log.info("스케줄러 태스크 생성 완료")

    app.post_init = post_init

    log.info("봇 폴링 시작")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
