# Архитектура менеджера VPN Chain v2

Документ описывает текущую архитектуру SQLite-менеджер, локальный веб-интерфейс, генерацию клиентских конфигов и безопасную работу с runtime-файлами.

## Цели

- Хранить публичное состояние VPN и метаданные в одном источнике истины.
- Не допускать случайной публикации ключей, конфигов и runtime-файлов в Git.
- Дать повторяемый путь установки, управления профилями, диагностики и отката.
- Не хранить приватные ключи клиентов после одноразовой генерации.
- Поддерживать разные профили экспорта для Android, iOS, роутеров и настольных клиентов.

## Границы текущей версии

- Веб-интерфейс предназначен для локального администрирования, а не для публичного размещения.
- Репликация базы между несколькими управляющими узлами не используется.
- Приватные ключи клиентов не сохраняются для повторного экспорта.
- Пользовательские пиры применяются к Docker runtime через слоты переменных окружения `CLIENT_PUB_KEY`, `CLIENT2_PUB_KEY`, … .

## Источник истины

SQLite хранит публичное состояние VPN и метаданные профилей.

Runtime-файлы находятся вне репозитория, например:

```text
/var/lib/vpnchain/vpnchain.sqlite
/var/lib/vpnchain/backups/
/var/lib/vpnchain/generated/
/var/lib/vpnchain/tmp/
/etc/vpnchain/config.toml
/etc/amnezia/amneziawg/awg0.conf
/etc/amnezia/amneziawg/awg1.conf
```

В Git-репозитории должны находиться только:

```text
bin/ или vpnchain/ — исходный код
templates/ — шаблоны
docs/ — документация
examples/ — безопасные примеры
tests/ — тесты
```

Сгенерированные конфиги, `.env`, реальные клиентские конфиги, приватные ключи, SQLite-файлы и backup-файлы не должны попадать в рабочее дерево Git.

## Модель SQLite

Основные таблицы:

- `nodes`
  - `id`
  - `name`
  - `role` (`ru`, `am`)
  - `tunnel_address`
  - `listen_port`
  - `public_endpoint_ref`
  - `public_key`
  - timestamps — временные метки

Для uplink-цепочки RU представлен на AM как обычный клиентский peer с адресом `10.9.0.2/32`. Сгенерированный на AM клиентский конфиг устанавливается на RU как `awg1.conf`.

- `peers`
  - `id`
  - `name`
  - `public_key`
  - `address`
  - `enabled`
  - `client_type`
  - `platform`
  - `export_profile`
  - `created_at`
  - `disabled_at`
  - `notes`

- `config_versions`
  - `id`
  - `action`
  - `rendered_hash`
  - `backup_path`
  - `created_at`
  - `applied_at`
  - `status`

- `cleanup_jobs`
  - `id`
  - `path`
  - `delete_after`
  - `status`
  - `created_at`
  - `deleted_at`

Поля `client_type`, `platform` и `export_profile` нужны, чтобы один и тот же профиль можно было экспортировать в форматах для iOS, Android, роутеров, настольных клиентов и совместимых резервных режимов.

## Работа с ключами

Приватные ключи клиентов не хранятся ни в SQLite, ни в репозитории.

`vpnchain peer add`:

1. Генерирует пару ключей клиента.
2. Сохраняет в SQLite только public key и метаданные peer'а.
3. Создаёт клиентский конфиг один раз.
4. Печатает конфиг в stdout или записывает в указанный файл.
5. Регистрирует временный файл на удаление после TTL по умолчанию 15 минут.
6. Отказывается писать клиентский конфиг внутрь Git-репозитория.

Если клиентский конфиг потерян, используется ротация peer'а:

```bash
vpnchain peer rotate <name> --output /secure/path/client-new.conf
```

Приватные ключи серверов остаются защищёнными runtime-файлами с ограниченными правами. Они не являются строками базы данных и не попадают в репозиторий.

## Render/apply и безопасность применения

Сгенерированные конфиги — это результат рендера, а не источник истины.

Поток применения:

1. Сформировать конфиг из SQLite и шаблонов.
2. Посчитать diff с активным конфигом.
3. Проверить diff на опасные изменения.
4. Сделать backup активного конфига.
5. Применить новый конфиг.
6. Запустить проверку здоровья.
7. Записать версию и результат в SQLite.

Diff скрыт в обычном пользовательском пути, но доступен командами:

```bash
vpnchain render --diff
vpnchain apply --dry-run
```

## Backup и откат

Перед каждым применением текущий рабочий конфиг сохраняется в runtime-каталог backup'ов.

Команды отката:

```bash
vpnchain versions
vpnchain rollback
vpnchain rollback --to <version>
```

Откат восстанавливает выбранную применённую версию и запускает проверку здоровья.

## Проверка здоровья

Проверка здоровья запускается после установки, применения, добавления, удаления, отключения, включения, ротации peer'а и отката.

Ручные команды:

```bash
vpnchain health
vpnchain health --deep
vpnchain status
vpnchain logs
```

Вывод должен быть понятен человеку:

- `OK`: проверка пройдена.
- `WARN`: сервис работает, но требует внимания.
- `FAIL`: действие не выполнено или связность нарушена.
- `Hint`: практический следующий шаг.

Что проверяется:

- наличие интерфейса;
- доступность `awg show`;
- свежий или устаревший handshake;
- наличие peer'а;
- корректность таблиц маршрутизации;
- потери пакетов до tunnel target;
- состояние monitor-сервиса;
- состояние Docker-контейнеров.

## Защита репозитория

Локальные защитные меры:

- runtime-каталоги находятся вне Git-репозитория;
- `.gitignore` закрывает сгенерированные конфиги, базы, ключи, `.env`, backup'и и временные экспорты;
- `vpnchain repo-check` ищет утечки;
- команда генерации отказывается писать клиентский конфиг внутрь репозитория;
- временные клиентские конфиги удаляются по TTL.

`vpnchain repo-check` должен находить очевидные утечки:

- реальные значения `PrivateKey = ...`;
- реальные значения `PresharedKey = ...`;
- `.env`-файлы;
- SQLite/DB-файлы;
- сгенерированные клиентские конфиги;
- файлы приватных ключей.

## Команды

Установка:

```bash
scripts/vpnchain-bootstrap.sh --role am --apply
scripts/vpnchain-bootstrap.sh --role am --generate-ru-uplink --am-endpoint <am-host:51821> --output /etc/vpnchain/ru-awg1.conf --apply
scripts/vpnchain-bootstrap.sh --role ru --ru-uplink-conf /etc/vpnchain/ru-awg1.conf --server-endpoint <ru-host:51820> --apply
scripts/vpnchain-bootstrap.sh --env-file /etc/vpnchain/vpnchain.env --ru-uplink-conf /etc/vpnchain/ru-awg1.conf --server-endpoint <ru-host:51820> --apply
```

Пиры:

```bash
vpnchain peer add <name>
vpnchain peer add <name> --output /secure/path/client.conf
vpnchain peer add <name> --print-once
vpnchain peer list
vpnchain peer show <name>
vpnchain peer disable <name>
vpnchain peer enable <name>
vpnchain peer remove <name>
vpnchain peer rotate <name> --output /secure/path/client-new.conf
```

Конфиг и применение:

```bash
vpnchain render
vpnchain render --diff
vpnchain apply
vpnchain apply --dry-run
vpnchain rollback
vpnchain rollback --to <version>
vpnchain versions
```

Диагностика и обслуживание:

```bash
vpnchain health
vpnchain health --deep
vpnchain status
vpnchain logs
vpnchain monitor install
vpnchain monitor status
vpnchain monitor restart
vpnchain repo-check
```
