#!/usr/bin/env bash
# ============================================================
# smoke_test_vps.sh — E2E smoke test для живого VPS
#
# Использование:
#   ./scripts/smoke_test_vps.sh [BASE_URL]
#
# По умолчанию: http://localhost:8081
#
# Тестирует все основные эндпоинты API и выводит результат
# с цветовой кодировкой. Завершается с кодом 0 если все
# тесты прошли, кодом 1 если хотя бы один упал.
# ============================================================

set -euo pipefail

BASE_URL="${1:-http://localhost:8081}"
TIMESTAMP=$(date +%s)
TEST_EMAIL="smoke_${TIMESTAMP}@test.com"
TEST_PASSWORD="SmokeTest123!"
ACCESS_TOKEN=""
REFRESH_COOKIE=""

# Счётчики
PASSED=0
FAILED=0
SKIPPED=0

# ============================================================
# Цвета
# ============================================================
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ============================================================
# Вспомогательные функции
# ============================================================

log_section() {
    echo ""
    echo -e "${CYAN}══════════════════════════════════════════${NC}"
    echo -e "${CYAN}  $1${NC}"
    echo -e "${CYAN}══════════════════════════════════════════${NC}"
}

check() {
    local description="$1"
    local expected_status="$2"
    local actual_status="$3"
    local body="$4"

    if [[ "$actual_status" == "$expected_status" ]] || [[ "$expected_status" == "2xx" && "$actual_status" =~ ^2 ]]; then
        echo -e "  ${GREEN}PASS${NC}  [$actual_status] $description"
        PASSED=$((PASSED + 1))
    else
        echo -e "  ${RED}FAIL${NC}  [$actual_status] $description (ожидался $expected_status)"
        if [[ -n "$body" ]]; then
            echo "         ответ: $(echo "$body" | head -c 200)"
        fi
        FAILED=$((FAILED + 1))
    fi
}

check_range() {
    local description="$1"
    local allowed_statuses="$2"   # напр. "200 404" или "200 201"
    local actual_status="$3"
    local body="$4"

    for s in $allowed_statuses; do
        if [[ "$actual_status" == "$s" ]]; then
            echo -e "  ${GREEN}PASS${NC}  [$actual_status] $description"
            PASSED=$((PASSED + 1))
            return
        fi
    done

    echo -e "  ${RED}FAIL${NC}  [$actual_status] $description (допустимые: $allowed_statuses)"
    if [[ -n "$body" ]]; then
        echo "         ответ: $(echo "$body" | head -c 200)"
    fi
    FAILED=$((FAILED + 1))
}

skip() {
    local description="$1"
    local reason="$2"
    echo -e "  ${YELLOW}SKIP${NC}  $description ($reason)"
    SKIPPED=$((SKIPPED + 1))
}

# Выполнить GET запрос и вернуть HTTP-статус
get_status() {
    local path="$1"
    local headers="${2:-}"
    local status_code
    if [[ -n "$headers" ]]; then
        status_code=$(curl -s -o /dev/null -w "%{http_code}" \
            -H "$headers" \
            "${BASE_URL}${path}" 2>/dev/null) || status_code="000"
    else
        status_code=$(curl -s -o /dev/null -w "%{http_code}" \
            "${BASE_URL}${path}" 2>/dev/null) || status_code="000"
    fi
    echo "$status_code"
}

# Выполнить GET запрос и вернуть тело ответа
get_body() {
    local path="$1"
    local headers="${2:-}"
    if [[ -n "$headers" ]]; then
        curl -s -H "$headers" "${BASE_URL}${path}" 2>/dev/null || echo ""
    else
        curl -s "${BASE_URL}${path}" 2>/dev/null || echo ""
    fi
}

# Выполнить POST и вернуть тело + статус в формате "STATUS:BODY"
post_json() {
    local path="$1"
    local body="$2"
    local headers="${3:-}"
    local tmp
    tmp=$(mktemp)
    local status_code
    if [[ -n "$headers" ]]; then
        status_code=$(curl -s -o "$tmp" -w "%{http_code}" \
            -X POST \
            -H "Content-Type: application/json" \
            -H "$headers" \
            -d "$body" \
            "${BASE_URL}${path}" 2>/dev/null) || status_code="000"
    else
        status_code=$(curl -s -o "$tmp" -w "%{http_code}" \
            -X POST \
            -H "Content-Type: application/json" \
            -d "$body" \
            "${BASE_URL}${path}" 2>/dev/null) || status_code="000"
    fi
    local resp_body
    resp_body=$(cat "$tmp")
    rm -f "$tmp"
    echo "${status_code}:${resp_body}"
}

# ============================================================
# Начало
# ============================================================

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║   NEURO COMMENTING — VPS Smoke Test      ║${NC}"
echo -e "${CYAN}║   Target: ${BASE_URL}${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"

# ============================================================
# Группа 1: Публичные эндпоинты
# ============================================================

log_section "Группа 1: Публичные эндпоинты"

for path in "/healthz" "/" "/ecom" "/edtech" "/saas" "/robots.txt" "/sitemap.xml"; do
    s=$(get_status "$path")
    check "GET $path" "200" "$s"
done

s=$(get_status "/health")
check_range "GET /health" "200 503" "$s"

s=$(get_status "/v1/billing/plans")
check "GET /v1/billing/plans" "200" "$s"

s=$(get_status "/v1/channel-map/category-tree")
check "GET /v1/channel-map/category-tree" "200" "$s"

# ============================================================
# Группа 2: Регистрация
# ============================================================

log_section "Группа 2: Регистрация и логин"

REGISTER_PAYLOAD="{\"email\":\"${TEST_EMAIL}\",\"password\":\"${TEST_PASSWORD}\",\"first_name\":\"Smoke\",\"company\":\"SmokeTestCo\"}"
result=$(post_json "/auth/register" "$REGISTER_PAYLOAD")
REG_STATUS="${result%%:*}"
REG_BODY="${result#*:}"

check "POST /auth/register" "201" "$REG_STATUS" "$REG_BODY"

if [[ "$REG_STATUS" == "201" ]]; then
    # Извлекаем access_token (базовый grep/awk, не требует jq)
    ACCESS_TOKEN=$(echo "$REG_BODY" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)
    if [[ -z "$ACCESS_TOKEN" ]]; then
        echo -e "  ${RED}ERROR${NC}  Не удалось извлечь access_token из ответа регистрации"
        FAILED=$((FAILED + 1))
    else
        echo "         access_token получен (${#ACCESS_TOKEN} символов)"
    fi
else
    echo -e "  ${YELLOW}WARN${NC}   Регистрация не прошла — аутентифицированные тесты будут пропущены"
fi

# Тест логина
LOGIN_PAYLOAD="{\"email\":\"${TEST_EMAIL}\",\"password\":\"${TEST_PASSWORD}\"}"
result=$(post_json "/auth/login" "$LOGIN_PAYLOAD")
LOGIN_STATUS="${result%%:*}"
LOGIN_BODY="${result#*:}"
check "POST /auth/login" "200" "$LOGIN_STATUS" "$LOGIN_BODY"

# Тест неверного пароля
WRONG_PAYLOAD="{\"email\":\"${TEST_EMAIL}\",\"password\":\"WrongPass123!\"}"
result=$(post_json "/auth/login" "$WRONG_PAYLOAD")
WRONG_STATUS="${result%%:*}"
check "POST /auth/login (неверный пароль)" "401" "$WRONG_STATUS"

# ============================================================
# Группа 3: Аутентифицированные эндпоинты
# ============================================================

log_section "Группа 3: Аутентифицированные GET-эндпоинты"

if [[ -z "$ACCESS_TOKEN" ]]; then
    skip "Все аутентифицированные тесты" "access_token не получен"
else
    AUTH_HEADER="Authorization: Bearer ${ACCESS_TOKEN}"

    # /auth/me
    s=$(get_status "/auth/me" "$AUTH_HEADER")
    check "GET /auth/me" "200" "$s"

    # /auth/sessions
    s=$(get_status "/auth/sessions" "$AUTH_HEADER")
    check "GET /auth/sessions" "200" "$s"

    # /v1/me/workspace
    s=$(get_status "/v1/me/workspace" "$AUTH_HEADER")
    check "GET /v1/me/workspace" "200" "$s"

    # /v1/me/team
    s=$(get_status "/v1/me/team" "$AUTH_HEADER")
    check "GET /v1/me/team" "200" "$s"

    # Фермы
    s=$(get_status "/v1/farm" "$AUTH_HEADER")
    check "GET /v1/farm" "200" "$s"

    s=$(get_status "/v1/farm/stats/live" "$AUTH_HEADER")
    check "GET /v1/farm/stats/live" "200" "$s"

    s=$(get_status "/v1/farm/comment-quality" "$AUTH_HEADER")
    check "GET /v1/farm/comment-quality" "200" "$s"

    # Парсер
    s=$(get_status "/v1/parser/jobs" "$AUTH_HEADER")
    check "GET /v1/parser/jobs" "200" "$s"

    # Прогрев
    s=$(get_status "/v1/warmup" "$AUTH_HEADER")
    check "GET /v1/warmup" "200" "$s"

    # Здоровье аккаунтов
    s=$(get_status "/v1/health/scores" "$AUTH_HEADER")
    check "GET /v1/health/scores" "200" "$s"

    s=$(get_status "/v1/health/quarantine" "$AUTH_HEADER")
    check "GET /v1/health/quarantine" "200" "$s"

    # Аккаунты
    s=$(get_status "/v1/web/accounts" "$AUTH_HEADER")
    check "GET /v1/web/accounts" "200" "$s"

    s=$(get_status "/v1/accounts/stats" "$AUTH_HEADER")
    check "GET /v1/accounts/stats" "200" "$s"

    # Прокси
    s=$(get_status "/v1/proxies" "$AUTH_HEADER")
    check "GET /v1/proxies" "200" "$s"

    # Папки (Telegram)
    s=$(get_status "/v1/folders" "$AUTH_HEADER")
    check "GET /v1/folders" "200" "$s"

    # Парсинг участников
    s=$(get_status "/v1/user-parser/results" "$AUTH_HEADER")
    check "GET /v1/user-parser/results" "200" "$s"

    # Комментарии
    s=$(get_status "/v1/comments/styles" "$AUTH_HEADER")
    check "GET /v1/comments/styles" "200" "$s"

    s=$(get_status "/v1/comments/custom-styles" "$AUTH_HEADER")
    check "GET /v1/comments/custom-styles" "200" "$s"

    s=$(get_status "/v1/comments/feed" "$AUTH_HEADER")
    check "GET /v1/comments/feed" "200" "$s"

    # Аналитика
    s=$(get_status "/v1/analytics/heatmap" "$AUTH_HEADER")
    check "GET /v1/analytics/heatmap" "200" "$s"

    s=$(get_status "/v1/analytics/dashboard" "$AUTH_HEADER")
    check "GET /v1/analytics/dashboard" "200" "$s"

    # Биллинг
    s=$(get_status "/v1/billing/subscription" "$AUTH_HEADER")
    check_range "GET /v1/billing/subscription" "200 404" "$s"

    s=$(get_status "/v1/billing/payments" "$AUTH_HEADER")
    check "GET /v1/billing/payments" "200" "$s"

    # Channel Map
    s=$(get_status "/v1/channel-map" "$AUTH_HEADER")
    check "GET /v1/channel-map" "200" "$s"

    s=$(get_status "/v1/channel-map/clusters?zoom=3" "$AUTH_HEADER")
    check "GET /v1/channel-map/clusters?zoom=3" "200" "$s"

    s=$(get_status "/v1/channel-map/stats" "$AUTH_HEADER")
    check "GET /v1/channel-map/stats" "200" "$s"

    s=$(get_status "/v1/channel-map/categories" "$AUTH_HEADER")
    check "GET /v1/channel-map/categories" "200" "$s"

    # Кампании
    s=$(get_status "/v1/campaigns" "$AUTH_HEADER")
    check "GET /v1/campaigns" "200" "$s"

    # Ассистент
    s=$(get_status "/v1/assistant/thread" "$AUTH_HEADER")
    check "GET /v1/assistant/thread" "200" "$s"

    s=$(get_status "/v1/creative/drafts" "$AUTH_HEADER")
    check "GET /v1/creative/drafts" "200" "$s"

    s=$(get_status "/v1/context" "$AUTH_HEADER")
    check "GET /v1/context" "200" "$s"

    # AI качество
    s=$(get_status "/v1/ai/quality-summary" "$AUTH_HEADER")
    check "GET /v1/ai/quality-summary" "200" "$s"

    # Реакции, чатинг, диалоги
    s=$(get_status "/v1/reactions" "$AUTH_HEADER")
    check "GET /v1/reactions" "200" "$s"

    s=$(get_status "/v1/chatting" "$AUTH_HEADER")
    check "GET /v1/chatting" "200" "$s"

    s=$(get_status "/v1/dialogs" "$AUTH_HEADER")
    check "GET /v1/dialogs" "200" "$s"

    # Профили
    s=$(get_status "/v1/profiles/templates" "$AUTH_HEADER")
    check "GET /v1/profiles/templates" "200" "$s"

    # Channel DB
    s=$(get_status "/v1/channel-db" "$AUTH_HEADER")
    check "GET /v1/channel-db" "200" "$s"

    # Отчёты
    s=$(get_status "/v1/reports/weekly" "$AUTH_HEADER")
    check "GET /v1/reports/weekly" "200" "$s"

    # Self-Healing
    s=$(get_status "/v1/healing/log" "$AUTH_HEADER")
    check "GET /v1/healing/log" "200" "$s"

    # Закупки
    s=$(get_status "/v1/purchases/requests" "$AUTH_HEADER")
    check "GET /v1/purchases/requests" "200" "$s"

    # Платформа
    s=$(get_status "/v1/platform/alerts" "$AUTH_HEADER")
    check "GET /v1/platform/alerts" "200" "$s"

    # Системные
    s=$(get_status "/v1/system/resource-estimate" "$AUTH_HEADER")
    check "GET /v1/system/resource-estimate" "200" "$s"

    # Стратегии комментирования
    s=$(get_status "/v1/commenting/strategies" "$AUTH_HEADER")
    check "GET /v1/commenting/strategies" "200" "$s"
fi

# ============================================================
# Группа 4: Проверка 401 без токена
# ============================================================

log_section "Группа 4: Защищённые эндпоинты без токена -> 401"

PROTECTED_PATHS=(
    "/v1/farm"
    "/v1/parser/jobs"
    "/v1/warmup"
    "/v1/health/scores"
    "/v1/web/accounts"
    "/v1/proxies"
    "/v1/comments/styles"
    "/v1/analytics/heatmap"
    "/v1/campaigns"
    "/v1/assistant/thread"
    "/v1/creative/drafts"
    "/v1/me/workspace"
)

for path in "${PROTECTED_PATHS[@]}"; do
    s=$(get_status "$path")
    check "GET $path (без токена)" "401" "$s"
done

# ============================================================
# Группа 5: Rate limiting (в production)
# ============================================================

log_section "Группа 5: Rate limiting"

# Делаем 12 быстрых запросов с неверными данными
# На VPS (production) начиная с 11-го должен вернуться 429
echo "  Проверка rate limiting на POST /auth/login (12 запросов)..."
RATE_HIT=false
for i in $(seq 1 12); do
    s=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"ratelimit_test@test.com\",\"password\":\"wrong\"}" \
        "${BASE_URL}/auth/login" 2>/dev/null) || s="000"
    if [[ "$s" == "429" ]]; then
        RATE_HIT=true
        echo -e "  ${GREEN}PASS${NC}  Rate limit сработал на запросе $i (429)"
        PASSED=$((PASSED + 1))
        break
    fi
done

if [[ "$RATE_HIT" == "false" ]]; then
    echo -e "  ${YELLOW}INFO${NC}  Rate limit не сработал за 12 запросов (возможно APP_ENV=development/test)"
    SKIPPED=$((SKIPPED + 1))
fi

# ============================================================
# Logout (очистка)
# ============================================================

if [[ -n "$ACCESS_TOKEN" ]]; then
    log_section "Очистка: logout"
    result=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST \
        -H "Authorization: Bearer ${ACCESS_TOKEN}" \
        "${BASE_URL}/auth/logout" 2>/dev/null) || result="000"
    check "POST /auth/logout" "200" "$result"
fi

# ============================================================
# Итог
# ============================================================

echo ""
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo -e "  Итог:"
echo -e "  ${GREEN}PASS: $PASSED${NC}"
echo -e "  ${RED}FAIL: $FAILED${NC}"
echo -e "  ${YELLOW}SKIP: $SKIPPED${NC}"
echo -e "${CYAN}══════════════════════════════════════════${NC}"
echo ""

if [[ "$FAILED" -gt 0 ]]; then
    echo -e "${RED}ПРОВАЛ: $FAILED тест(ов) не прошли.${NC}"
    exit 1
else
    echo -e "${GREEN}УСПЕХ: Все тесты прошли.${NC}"
    exit 0
fi
