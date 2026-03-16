"""
QualityGate — 4-уровневая проверка качества AI-комментариев с авто-ретраем.

Инспирировано DO-Framework (Vels): каждый комментарий проходит
последовательные уровни верификации; при провале — авто-ретрай
с деградацией стратегии.

Уровни:
    Level 1  Syntax  — длина, кодировка, запрещённые паттерны
    Level 2  Content — релевантность посту, нет AI-клише, нет дженериков
    Level 3  Safety  — нет URLs, @юзеров, телефонов, спам-паттернов
    Level 4  Style   — число эмодзи, количество предложений, анти-паттерны

Авто-ретрай (до 3 попыток):
    Attempt 1  — оригинальный стиль
    Attempt 2  — деградация до "casual"
    Attempt 3  — эмодзи-фолбэк (emoji_first)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, List, Optional, Tuple

from utils.logger import log


# ---------------------------------------------------------------------------
# Константы
# ---------------------------------------------------------------------------

# Level 1 — Syntax
_MIN_LEN = 10
_MAX_LEN = 500
_MAX_REPEATED_CHAR_RUN = 4  # «аааа» → провал

_FORBIDDEN_RE = [
    re.compile(r"https?://\S+", re.IGNORECASE),          # прямые URL
    re.compile(r"t\.me/\S+", re.IGNORECASE),              # t.me ссылки
    re.compile(r"\+7[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"),  # RU телефоны
    re.compile(r"\+?\d[\d\s\-]{9,}"),                     # общий телефон
]

_REPEATED_CHAR_RE = re.compile(r"(.)\1{%d,}" % _MAX_REPEATED_CHAR_RUN)

# Level 2 — Content
_AI_CLICHES = [
    "безусловно",
    "на самом деле",
    "стоит отметить",
    "в целом",
    "как вы правильно заметили",
    "необходимо подчеркнуть",
    "следует отметить",
    "нельзя не отметить",
    "важно понимать",
    "подводя итог",
]

_GENERIC_PHRASES = [
    "отличный пост",
    "спасибо за информацию",
    "полностью согласен",
    "очень интересно",
    "хороший материал",
    "благодарю за пост",
    "полезная информация",
    "согласен с автором",
    "интересная статья",
]

_MIN_KEYWORD_WORDS = 3  # минимум слов в посте для проверки пересечения

# Level 3 — Safety
_SAFETY_URL_RE = re.compile(r"https?://|t\.me/", re.IGNORECASE)
_TELEGRAM_USERNAME_RE = re.compile(r"@[A-Za-z0-9_]{4,}")  # @username (4+ символа)
_PHONE_RE = re.compile(r"\+?[\d\s\-]{10,}")
_CRYPTO_WALLET_RE = re.compile(
    r"\b(0x[0-9a-fA-F]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|T[A-Za-z0-9]{33})\b"
)
_PROMO_KEYWORDS = [
    "скидка", "бесплатно", "переходи", "ссылка в био",
    "жми", "кликай", "промокод", "акция", "купить", "заказать",
    "перейди", "жми сюда", "ссылка в описании",
]

_MAX_SPAM_SCORE = 3  # порог: ≥ N совпадений = спам

# Level 4 — Style
_EMOJI_RANGES = [
    (0x1F600, 0x1F64F),
    (0x1F300, 0x1F5FF),
    (0x1F680, 0x1F6FF),
    (0x1F1E0, 0x1F1FF),
    (0x2600,  0x26FF),
    (0x2700,  0x27BF),
    (0xFE00,  0xFE0F),
    (0x1F900, 0x1F9FF),
    (0x1FA00, 0x1FA6F),
    (0x1FA70, 0x1FAFF),
]

_STYLE_EMOJI_BOUNDS: dict[str, Tuple[int, int]] = {
    "casual":      (1, 3),
    "professional": (0, 1),
    "emoji_first": (2, 5),
    "emoji":       (1, 4),
    "expert":      (0, 1),
    "question":    (0, 2),
    "agree":       (0, 2),
    "supplement":  (0, 1),
    "joke":        (1, 3),
    "personal":    (1, 3),
    "quote":       (0, 1),
    "controversial": (0, 1),
    "gratitude":   (1, 3),
}

_STYLE_SENTENCE_BOUNDS: dict[str, Tuple[int, int]] = {
    "casual":      (1, 3),
    "professional": (1, 4),
    "emoji_first": (1, 2),
    "emoji":       (1, 2),
    "expert":      (1, 3),
    "question":    (1, 2),
    "agree":       (1, 2),
    "supplement":  (1, 3),
    "joke":        (1, 2),
    "personal":    (1, 3),
    "quote":       (1, 3),
    "controversial": (1, 3),
    "gratitude":   (1, 2),
}

# Level 4 — антипаттерн начала комментария с «Я»
_STARTS_WITH_I_RE = re.compile(r"^[Яя]\s")

# Деградационная цепочка стилей при ретрае
_RETRY_STYLE_CHAIN = ["casual", "emoji_first"]


# ---------------------------------------------------------------------------
# QualityResult
# ---------------------------------------------------------------------------

@dataclass
class QualityResult:
    """Результат одного уровня или полной проверки."""
    passed: bool
    level: int        # 1-4 (0 = полная проверка)
    issues: List[str] = field(default_factory=list)
    score: float = 1.0  # 0.0–1.0


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

def _count_emojis(text: str) -> int:
    """Посчитать количество emoji-символов в строке."""
    count = 0
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _EMOJI_RANGES):
            count += 1
    return count


def _count_sentences(text: str) -> int:
    """Грубый подсчёт предложений по знакам препинания."""
    parts = re.split(r"[.!?…]+", text.strip())
    return max(1, sum(1 for p in parts if p.strip()))


def _extract_keywords(text: str) -> set[str]:
    """Извлечь множество слов длиной ≥ 4 символа (строчные)."""
    words = re.findall(r"[А-Яа-яA-Za-z]{4,}", text.lower())
    return set(words)


# ---------------------------------------------------------------------------
# QualityGate
# ---------------------------------------------------------------------------

class QualityGate:
    """
    4-уровневая проверка качества AI-сгенерированных комментариев с авто-ретраем.

    Инспирировано DO-Framework Vels:
      Level 1  Syntax  — длина, кодировка, запрещённые паттерны
      Level 2  Content — релевантность, нет AI-клише, нет дженериков
      Level 3  Safety  — нет URLs, @юзеров, телефонов, крипто, спам-слов
      Level 4  Style   — эмодзи в норме, предложений в норме, не начинается с «Я»

    Авто-ретрай (до 3 попыток):
      Attempt 1  — оригинальный стиль
      Attempt 2  — деградация до "casual"
      Attempt 3  — деградация до "emoji_first" (коротко, безопасно)
    """

    # ------------------------------------------------------------------
    # Level 1 — Syntax
    # ------------------------------------------------------------------

    def check_syntax(self, comment: str) -> QualityResult:
        """
        Проверка синтаксиса:
          - длина 10-500 символов
          - нет битой кодировки (символы-заменители)
          - нет URL, телефонов (грубая проверка)
          - нет серий повторяющихся символов (аааа)
        """
        issues: list[str] = []

        # Длина
        if len(comment) < _MIN_LEN:
            issues.append(f"comment too short: {len(comment)} chars (min {_MIN_LEN})")
        if len(comment) > _MAX_LEN:
            issues.append(f"comment too long: {len(comment)} chars (max {_MAX_LEN})")

        # Битая кодировка (символы замены Unicode: U+FFFD)
        if "\ufffd" in comment:
            issues.append("broken encoding: replacement character found")

        # Управляющие символы (кроме \n)
        for ch in comment:
            cat = unicodedata.category(ch)
            if cat in ("Cc", "Cs") and ch != "\n":
                issues.append(f"forbidden control character: U+{ord(ch):04X}")
                break

        # Запрещённые паттерны (URL, телефоны)
        for pattern in _FORBIDDEN_RE:
            if pattern.search(comment):
                issues.append(f"forbidden pattern: {pattern.pattern[:40]}")

        # Повторяющиеся символы
        if _REPEATED_CHAR_RE.search(comment):
            issues.append(
                f"repeated chars: run of {_MAX_REPEATED_CHAR_RUN + 1}+ identical chars"
            )

        passed = len(issues) == 0
        score = max(0.0, 1.0 - len(issues) * 0.25)
        result = QualityResult(passed=passed, level=1, issues=issues, score=score)
        log.debug("QualityGate L1 Syntax: passed=%s score=%.2f issues=%s", passed, score, issues)
        return result

    # ------------------------------------------------------------------
    # Level 2 — Content
    # ------------------------------------------------------------------

    def check_content(self, comment: str, post_text: str) -> QualityResult:
        """
        Проверка релевантности и содержания:
          - хотя бы 1 ключевое слово пересекается с постом
          - нет AI-клише
          - нет дженерик-фраз
          - не дублирует заголовок поста
        """
        issues: list[str] = []
        comment_lower = comment.lower()
        post_lower = post_text.lower()

        # Релевантность: пересечение ключевых слов
        post_words = _extract_keywords(post_text)
        if len(post_words) >= _MIN_KEYWORD_WORDS:
            comment_words = _extract_keywords(comment)
            if not comment_words.intersection(post_words):
                issues.append("no keyword overlap with post text")

        # AI-клише
        for cliche in _AI_CLICHES:
            if cliche in comment_lower:
                issues.append(f"AI cliche: \"{cliche}\"")

        # Дженерик-фразы
        for phrase in _GENERIC_PHRASES:
            if phrase in comment_lower:
                issues.append(f"generic phrase: \"{phrase}\"")

        # Дублирование первой строки поста
        post_first_line = (post_text.split("\n")[0] or "").strip().lower()
        if post_first_line and len(post_first_line) > 10:
            if post_first_line[:30] in comment_lower:
                issues.append("comment repeats post title/first line")

        passed = len(issues) == 0
        score = max(0.0, 1.0 - len(issues) * 0.2)
        result = QualityResult(passed=passed, level=2, issues=issues, score=score)
        log.debug("QualityGate L2 Content: passed=%s score=%.2f issues=%s", passed, score, issues)
        return result

    # ------------------------------------------------------------------
    # Level 3 — Safety
    # ------------------------------------------------------------------

    def check_safety(self, comment: str) -> QualityResult:
        """
        Проверка безопасности (анти-бан):
          - нет URL
          - нет @telegram_username
          - нет телефонов
          - нет крипто-кошельков
          - нет промо-ключевых слов
          - spam_score < 3
        """
        issues: list[str] = []
        spam_score = 0
        comment_lower = comment.lower()

        # URLs
        if _SAFETY_URL_RE.search(comment):
            issues.append("contains URL (t.me or http)")
            spam_score += 2

        # @telegram usernames
        usernames = _TELEGRAM_USERNAME_RE.findall(comment)
        if usernames:
            issues.append(f"contains @username: {usernames[:2]}")
            spam_score += 1

        # Телефоны
        if _PHONE_RE.search(comment):
            # Уточнение: только если ≥ 10 цифр подряд
            digits_only = re.findall(r"\d{10,}", re.sub(r"\s", "", comment))
            if digits_only:
                issues.append("contains phone number")
                spam_score += 2

        # Крипто-кошельки
        if _CRYPTO_WALLET_RE.search(comment):
            issues.append("contains crypto wallet address")
            spam_score += 3

        # Промо-ключевые слова
        for kw in _PROMO_KEYWORDS:
            if kw in comment_lower:
                issues.append(f"promo keyword: \"{kw}\"")
                spam_score += 1

        # Итоговый spam_score
        if spam_score >= _MAX_SPAM_SCORE and not issues:
            issues.append(f"spam score too high: {spam_score}")

        passed = len(issues) == 0
        score = max(0.0, 1.0 - spam_score * 0.15)
        result = QualityResult(passed=passed, level=3, issues=issues, score=score)
        log.debug("QualityGate L3 Safety: passed=%s score=%.2f spam_score=%d", passed, score, spam_score)
        return result

    # ------------------------------------------------------------------
    # Level 4 — Style
    # ------------------------------------------------------------------

    def check_style(self, comment: str, style: str) -> QualityResult:
        """
        Проверка стилевого соответствия:
          - количество эмодзи в допустимом диапазоне для стиля
          - количество предложений в допустимом диапазоне
          - не начинается с «Я» (анти-паттерн)
        """
        issues: list[str] = []

        # Количество эмодзи
        emoji_count = _count_emojis(comment)
        emoji_lo, emoji_hi = _STYLE_EMOJI_BOUNDS.get(style, (0, 5))
        if emoji_count < emoji_lo:
            issues.append(
                f"too few emojis for style '{style}': {emoji_count} (min {emoji_lo})"
            )
        elif emoji_count > emoji_hi:
            issues.append(
                f"too many emojis for style '{style}': {emoji_count} (max {emoji_hi})"
            )

        # Количество предложений
        sent_count = _count_sentences(comment)
        sent_lo, sent_hi = _STYLE_SENTENCE_BOUNDS.get(style, (1, 5))
        if sent_count < sent_lo:
            issues.append(
                f"too few sentences for style '{style}': {sent_count} (min {sent_lo})"
            )
        elif sent_count > sent_hi:
            issues.append(
                f"too many sentences for style '{style}': {sent_count} (max {sent_hi})"
            )

        # Анти-паттерн: начинается с «Я»
        if _STARTS_WITH_I_RE.match(comment.strip()):
            issues.append("comment starts with 'Я' (anti-pattern for Telegram comments)")

        passed = len(issues) == 0
        score = max(0.0, 1.0 - len(issues) * 0.2)
        result = QualityResult(passed=passed, level=4, issues=issues, score=score)
        log.debug("QualityGate L4 Style: passed=%s score=%.2f issues=%s", passed, score, issues)
        return result

    # ------------------------------------------------------------------
    # Полная проверка (все 4 уровня)
    # ------------------------------------------------------------------

    def run_all_checks(
        self,
        comment: str,
        post_text: str,
        style: str,
    ) -> QualityResult:
        """
        Запускает все 4 уровня по порядку.
        Возвращает первый провал или итоговый pass с усреднённым score.

        Не прерывается на первом провале — собирает все проблемы,
        чтобы ретрай-логика могла выбрать лучший из провальных вариантов.
        """
        results = [
            self.check_syntax(comment),
            self.check_content(comment, post_text),
            self.check_safety(comment),
            self.check_style(comment, style),
        ]

        all_issues: list[str] = []
        avg_score = 0.0
        first_fail: Optional[QualityResult] = None

        for r in results:
            avg_score += r.score
            all_issues.extend(r.issues)
            if not r.passed and first_fail is None:
                first_fail = r

        avg_score /= len(results)

        if first_fail is not None:
            # Возвращаем сводный провал: уровень первого нарушения, все проблемы
            combined = QualityResult(
                passed=False,
                level=first_fail.level,
                issues=all_issues,
                score=avg_score,
            )
            log.info(
                "QualityGate FAIL level=%d score=%.2f issues_count=%d",
                first_fail.level,
                avg_score,
                len(all_issues),
            )
            return combined

        result = QualityResult(passed=True, level=0, issues=[], score=avg_score)
        log.info("QualityGate PASS score=%.2f", avg_score)
        return result

    # ------------------------------------------------------------------
    # Авто-ретрай с деградацией стиля
    # ------------------------------------------------------------------

    async def auto_retry(
        self,
        generate_fn: Callable[..., Awaitable[Optional[str]]],
        post_text: str,
        style: str,
        max_attempts: int = 3,
    ) -> Tuple[Optional[str], int]:
        """
        Выполняет до max_attempts попыток генерации с деградацией стиля.

        Стратегия деградации:
          Attempt 1  — оригинальный style
          Attempt 2  — "casual"
          Attempt 3  — "emoji_first"

        Args:
            generate_fn:  async-функция (style: str) -> Optional[str]
                          Принимает именованный аргумент `style`.
            post_text:    текст поста для проверки релевантности.
            style:        исходный стиль.
            max_attempts: максимум попыток (по умолчанию 3).

        Returns:
            (comment, attempts_used)
            comment — лучший текст (даже если провальный: с максимальным score).
            attempts_used — сколько попыток потребовалось.

        Если generate_fn вернул None на всех попытках — возвращает (None, max_attempts).
        """
        styles_to_try = [style] + _RETRY_STYLE_CHAIN
        styles_to_try = styles_to_try[:max_attempts]

        best_comment: Optional[str] = None
        best_score: float = -1.0
        attempts_used = 0

        for attempt_idx, attempt_style in enumerate(styles_to_try, start=1):
            attempts_used = attempt_idx

            log.info(
                "QualityGate auto_retry: attempt=%d/%d style=%s",
                attempt_idx,
                max_attempts,
                attempt_style,
            )

            try:
                comment = await generate_fn(style=attempt_style)
            except Exception as exc:
                log.warning(
                    "QualityGate auto_retry: generate_fn raised on attempt %d: %s",
                    attempt_idx,
                    exc,
                )
                comment = None

            if not comment:
                log.debug("QualityGate auto_retry: no comment on attempt %d", attempt_idx)
                continue

            result = self.run_all_checks(
                comment=comment,
                post_text=post_text,
                style=attempt_style,
            )

            # Если прошло — возвращаем сразу
            if result.passed:
                log.info(
                    "QualityGate auto_retry: PASSED on attempt %d style=%s",
                    attempt_idx,
                    attempt_style,
                )
                return comment, attempts_used

            # Иначе запоминаем лучший по score
            if result.score > best_score:
                best_score = result.score
                best_comment = comment

        # Все попытки исчерпаны — возвращаем лучший (по score) или None
        if best_comment:
            log.warning(
                "QualityGate auto_retry: all %d attempts FAILED, returning best (score=%.2f)",
                max_attempts,
                best_score,
            )
        else:
            log.warning(
                "QualityGate auto_retry: all %d attempts produced no comment",
                max_attempts,
            )

        return best_comment, attempts_used


# ---------------------------------------------------------------------------
# Singleton — единственный экземпляр на процесс
# ---------------------------------------------------------------------------

_gate: Optional[QualityGate] = None


def get_quality_gate() -> QualityGate:
    """Вернуть/создать singleton QualityGate."""
    global _gate
    if _gate is None:
        _gate = QualityGate()
    return _gate
