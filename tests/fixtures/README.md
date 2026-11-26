# Снимки RSS-лент

Реальные снимки лент по умолчанию — то, что сервер отдаёт на запрос с
`User-Agent: SecurityFeedBot/1.0`. Сняты 2026-11-26. Переводы строк приведены к LF,
остальное — байт в байт как в ответе сервера.

Тесты в `tests/test_collectors.py` рассчитаны на точное содержимое этих файлов.
При обновлении снимка правьте ожидаемые заголовки, даты и счётчики осознанно:
так видно, что именно изменилось в ленте.

| Файл | Источник | Формат | Записей |
| :--- | :--- | :--- | ---: |
| `hnr_feed.xml` | <https://feeds.feedburner.com/TheHackersNews> | RSS 2.0 | 50 |
| `bleepingcomputer_feed.xml` | <https://www.bleepingcomputer.com/feed/> | RSS 2.0 | 15 |
| `php_net_feed.xml` | <https://www.php.net/releases/feed.php> | Atom 1.0 | 5 |
| `node_feed.xml` | <https://nodejs.org/en/feed/vulnerability.xml> | RSS 2.0, без описаний | 78 |

Что снимки фиксируют помимо разбора:

- php.net отдаёт Atom, а не RSS;
- у фида Node.js нет `<description>` — новость можно отобрать только по заголовку;
- ленты упорядочены от новых к старым — на это опирается `MAX_ITEMS_PER_FEED`;
- покрытие ключевых слов по умолчанию (`test_default_keywords_cover_real_feeds`).
