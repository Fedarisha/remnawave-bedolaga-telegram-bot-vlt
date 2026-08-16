-- Оценка риска ПЕРЕД деплоем мёржа v4.0.0 (Remnawave 3.0.0).
-- Только чтение: ничего не меняет.
--
-- Запуск (подставьте свои креды/имя контейнера):
--   docker compose exec -T postgres psql -U <user> -d <db> -f - < scripts/check_panel_identity_exposure.sql
--   psql "$DATABASE_URL" -f scripts/check_panel_identity_exposure.sql
--
-- Колонка remnawave_id заполняется отдельным ручным бэкфилом
-- (`make backfill-remnawave-ids` → `--apply`), миграция 0104 её только создаёт.
-- Пока она пуста, панельную идентичность строки приходится восстанавливать по
-- запасным ключам. Запрос показывает, скольким строкам и чем именно это грозит.

\echo '== 1. Подписки без числового panel id (их и должен закрыть бэкфил) =='
SELECT
    count(*) FILTER (WHERE remnawave_id IS NULL)                                  AS no_panel_id,
    count(*) FILTER (WHERE remnawave_id IS NOT NULL)                              AS with_panel_id,
    count(*)                                                                      AS total
FROM subscriptions;

\echo ''
\echo '== 2. Из строк без panel id — чем восстанавливается связь =='
\echo '   short_uuid          → чинится сама, в рантайме (adopt by short_uuid)'
\echo '   only_username       → чинится по username (per-subscription суффикс)'
\echo '   nothing_to_match_on → рантайм бессилен, только бэкфил по telegram/email'
SELECT
    count(*) FILTER (WHERE remnawave_short_uuid IS NOT NULL)                      AS short_uuid,
    count(*) FILTER (WHERE remnawave_short_uuid IS NULL
                       AND coalesce(remnawave_short_id, '') <> '')                AS only_username,
    count(*) FILTER (WHERE remnawave_short_uuid IS NULL
                       AND coalesce(remnawave_short_id, '') = '')                 AS nothing_to_match_on
FROM subscriptions
WHERE remnawave_id IS NULL;

\echo ''
\echo '== 3. То же, но только по ЖИВЫМ подпискам — это и есть реальный риск =='
SELECT
    status,
    count(*)                                                                      AS rows,
    count(*) FILTER (WHERE remnawave_short_uuid IS NOT NULL)                      AS short_uuid,
    count(*) FILTER (WHERE remnawave_short_uuid IS NULL
                       AND coalesce(remnawave_short_id, '') <> '')                AS only_username,
    count(*) FILTER (WHERE remnawave_short_uuid IS NULL
                       AND coalesce(remnawave_short_id, '') = '')                 AS nothing_to_match_on
FROM subscriptions
WHERE remnawave_id IS NULL
  AND status IN ('active', 'trial', 'limited')
GROUP BY status
ORDER BY status;

\echo ''
\echo '== 4. Пользователи без числового panel id (важно для single-tariff режима) =='
SELECT
    count(*) FILTER (WHERE remnawave_id IS NULL AND remnawave_uuid IS NOT NULL)   AS legacy_only,
    count(*) FILTER (WHERE remnawave_id IS NOT NULL)                              AS with_panel_id
FROM users;

\echo ''
\echo '== 5. Дубли подписок на один тариф (что удалила бы апстримовая чистка) =='
\echo '   В форке это штатная история покупок; автозапуск чистки отключён.'
-- Чистка оставляет одну строку-выжившую (самую «живую»), поэтому если в группе
-- нет ни одной живой подписки, из истёкших удалились бы все, кроме одной.
SELECT
    count(*)                                                                      AS pairs_user_tariff_with_duplicates,
    coalesce(sum(CASE WHEN alive > 0 THEN dead ELSE greatest(dead - 1, 0) END), 0) AS rows_upstream_would_delete
FROM (
    SELECT
        count(*) FILTER (WHERE status IN ('active', 'trial', 'limited')) AS alive,
        count(*) FILTER (WHERE status IN ('expired', 'disabled'))        AS dead
    FROM subscriptions
    WHERE tariff_id IS NOT NULL
      AND is_trial IS FALSE
    GROUP BY user_id, tariff_id
    HAVING count(*) > 1
) dup;
