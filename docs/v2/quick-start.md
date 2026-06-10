# Быстрый старт v2: установка на два сервера

Эта инструкция для русскоязычного пользователя, который хочет поднять свою двухузловую цепочку AmneziaWG ProxyChain из текущей версии проекта.

В инструкции используются placeholders вида `<AM_PUBLIC_HOST_OR_IP>`. Их нужно заменить на реальные значения.

## Что мы устанавливаем

Цепочка состоит из двух серверов:

1. **AM/выходной сервер** — зарубежный сервер, который выпускает трафик в интернет.
2. **RU-сервер** — входной сервер, к которому подключаются пользовательские устройства. Российские направления он выпускает напрямую, остальной трафик отправляет через AM/exit.

Для AM-сервера RU-сервер является обычным клиентом. Поэтому AM-сервер генерирует для RU готовый клиентский uplink-конфиг `awg1.conf`, включая private key для RU→AM туннеля. Это такой же подход, как с пользовательскими клиентскими конфигами.

## Важное про текущее состояние v2

В v2 уже есть SQLite manager, генерация клиентских конфигов, WebUI, мониторинг активности и DNS-assisted routing для `.ru`/2ip-подобных доменов.

Для применения пользовательских peer'ов в Docker runtime используйте слоты переменных окружения (`CLIENT_PUB_KEY`, `CLIENT2_PUB_KEY`, ...). В инструкции явно отмечено, когда нужно выполнить команду, а когда — записать значение в `/etc/vpnchain/vpnchain.env`.

## Требования

На **обоих** серверах должны быть:

- Debian/Ubuntu-подобный Linux;
- root или sudo;
- Docker CLI и `docker compose`/`docker-compose`;
- доступный `/dev/net/tun`;
- открытые UDP-порты:
  - на AM/выходной сервере: `51821/udp`;
  - на RU-сервере: `51820/udp`.

Минимальные Docker-пакеты для Debian-подобных систем:

```bash
sudo apt-get update
sudo apt-get install -y docker.io docker-compose docker-cli
```

На некоторых образах Debian 13 пакет `docker.io` + `docker-compose` не ставит саму команду `docker`; в этом случае обязателен отдельный пакет `docker-cli`.

На обоих серверах попробуйте установить модуль AmneziaWG:

```bash
sudo bash install_kernel_module.sh
lsmod | grep amneziawg || sudo modprobe amneziawg
```

Если установка обновила ядро — перезагрузите сервер и повторите проверку.

Если на свежем Debian 13 нет подходящих `linux-headers-*` или пакетов для сборки модуля, установка может продолжать работать через userspace fallback `amneziawg-go` внутри контейнеров. Это хуже для производительности, но не блокирует функциональную проверку chain при наличии `/dev/net/tun`.

## 1. Склонируйте репозиторий на оба сервера

Выполнить **на AM/выходной сервере и на RU-сервере**:

```bash
git clone <your-repo-url> /opt/AmneziaWG-ProxyChain
cd /opt/AmneziaWG-ProxyChain
# при необходимости выберите нужный release/tag
```

Дальше все команды выполняются из каталога:

```bash
cd /opt/AmneziaWG-ProxyChain
```

## 2. На AM/выходной сервере запустите обычный AWG-сервер

На этом шаге AM/выходной сервер создаёт свой приватный и публичный ключи сервера и запускает `awg0` как обычный AWG-сервер. uplink-конфиг RU генерируется на следующем шаге как обычный клиентский конфиг для AM.

Выполнить **на AM/выходной сервере**:

```bash
sudo scripts/vpnchain-bootstrap.sh \
  --role am \
  --apply
```

Проверьте, что AM public key существует, если нужен ручной контроль:

```bash
sudo docker exec awg-am cat /etc/amnezia/amneziawg/server_public_key
```

## 3. На AM/выходной сервере сгенерируйте uplink-конфиг для RU

Теперь AM/выходной сервер должен создать готовый клиентский конфиг для RU-сервера. Этот файл будет использоваться на RU как `/etc/amnezia/amneziawg/awg1.conf`.

Выполнить **на AM/выходной сервере**:

```bash
sudo scripts/vpnchain-bootstrap.sh \
  --role am \
  --generate-ru-uplink \
  --am-endpoint '<AM_PUBLIC_HOST_OR_IP>:51821' \
  --output /etc/vpnchain/ru-awg1.conf \
  --apply
```

Что делает команда:

- генерирует обычную пару ключей клиента для uplink RU→AM;
- добавляет public key этого клиентского конфига как peer на AM-сервер;
- записывает готовый RU-конфиг в файл `/etc/vpnchain/ru-awg1.conf`;
- выставляет файлу права `0600`.

Файл `/etc/vpnchain/ru-awg1.conf` содержит private key. Передавайте его на RU-сервер только по защищённому каналу.

Скопируйте файл с AM/выходной сервера на RU-сервер. Файл содержит private key, поэтому не вставляйте его в чат/тикеты/логи.

Если копируете **с AM-сервера на RU-сервер**, заменив `<RU_PUBLIC_HOST_OR_IP>`:

```bash
ssh root@<RU_PUBLIC_HOST_OR_IP> 'mkdir -p /etc/vpnchain && chmod 700 /etc/vpnchain'
scp /etc/vpnchain/ru-awg1.conf root@<RU_PUBLIC_HOST_OR_IP>:/etc/vpnchain/ru-awg1.conf
ssh root@<RU_PUBLIC_HOST_OR_IP> 'chmod 600 /etc/vpnchain/ru-awg1.conf'
```

Если копируете с обычного компьютера, сначала скачайте файл с AM, потом загрузите его на RU. Главное: итоговый файл должен лежать на RU-сервере здесь:

```text
/etc/vpnchain/ru-awg1.conf
```

## 4. На RU-сервере запустите входной узел с готовым uplink-конфигом

На этом шаге RU-сервер использует файл `/etc/vpnchain/ru-awg1.conf`, который был создан на AM/выходной сервере.

Выполнить **на RU-сервере**:

```bash
sudo scripts/vpnchain-bootstrap.sh \
  --role ru \
  --ru-uplink-conf /etc/vpnchain/ru-awg1.conf \
  --server-endpoint <RU_PUBLIC_HOST_OR_IP>:51820 \
  --apply
```

Что делает команда:

- запускает/обновляет `awg-ru`;
- копирует `/etc/vpnchain/ru-awg1.conf` внутрь контейнера как `/etc/amnezia/amneziawg/awg1.conf`;
- перезапускает `awg-ru`, чтобы поднялся второй hop `awg1`;
- читает `server_public_key` из `awg-ru` и сохраняет его вместе с `--server-endpoint` в `/etc/vpnchain/vpnchain.env`, чтобы пользовательские клиентский конфигs сразу генерировались без placeholders.

Проверить **на RU-сервере**:

```bash
sudo docker exec awg-ru awg show awg0
sudo docker exec awg-ru awg show awg1
```

Проверить **на AM/выходной сервере**:

```bash
sudo docker exec awg-am awg show awg0
```

В `awg show` должен появиться обычный client peer с адресом `10.9.0.2/32` и свежим handshake после запуска RU.

### MTU клиентских профилей

Пользовательские клиентские конфиги, которые генерирует v2 manager/WebUI, по умолчанию содержат `MTU = 1280`. Это намеренно: в роутерных и вложенных VPN-сценариях больший MTU, например `1420`, может приводить к ситуации, когда TCP-соединение устанавливается, но часть сайтов зависает на TLS из-за PMTU/фрагментации. Если точно известно, что путь выдерживает больший MTU, его можно поднять вручную в конкретном клиентском профиле.

## 5. Где лежит env-файл RU-сервера

Bootstrap-скрипт создаёт файл:

```text
/etc/vpnchain/vpnchain.env
```

Это env-файл с правами `0600`. Старый файл перед заменой сохраняется в backup.

Для RU-сервера в новом uplink-flow этот файл может быть минимальным:

```dotenv
VPNCHAIN_ROLE=ru
VPNCHAIN_SERVER_PUBLIC_KEY=<auto-filled-by-bootstrap>
VPNCHAIN_SERVER_ENDPOINT=<RU_PUBLIC_HOST_OR_IP>:51820
```

Если вы добавляете пользовательских клиентов через слоты переменных окружения, публичный ключ клиента нужно записать туда же:

```dotenv
CLIENT_PUB_KEY=<CLIENT_PUBLIC_KEY>
CLIENT2_PUB_KEY=<CLIENT_PUBLIC_KEY>
```

После изменения `/etc/vpnchain/vpnchain.env` примените его командой **на RU-сервере**:

```bash
sudo scripts/vpnchain-bootstrap.sh \
  --env-file /etc/vpnchain/vpnchain.env \
  --ru-uplink-conf /etc/vpnchain/ru-awg1.conf \
  --server-endpoint <RU_PUBLIC_HOST_OR_IP>:51820 \
  --apply
```

## 6. DNS-assisted routing для российских доменов

RU-контейнер использует два набора направлений:

- `ru_subnets` — российские GeoIP-префиксы;
- `ru_domains` — IP-адреса доменов из `server-ru/ru-domains.txt`, которые dnsmasq узнаёт через DNS.

Это нужно для случаев, когда российский домен размещён на non-RU CDN/IP. Например, `2ip.ru` может резолвиться в адрес, который не попадает в российские GeoIP-сети, но мы всё равно хотим выпускать его через RU-сервер.

Чтобы добавить домен, выполните **на RU-сервере**:

```bash
sudo nano /opt/AmneziaWG-ProxyChain/server-ru/ru-domains.txt
```

Добавьте домен отдельной строкой, например:

```text
example.ru
www.example.ru
```

Затем перезапустите RU-контейнер:

```bash
cd /opt/AmneziaWG-ProxyChain
sudo docker compose --profile ru up -d --build server-ru
```

Entrypoint удаляет только свои iptables-правила и не сбрасывает весь Docker NAT. Это важно: полный flush `POSTROUTING` ломает `docker build` и обычные Docker containers.

## 7. Создание пользовательского клиентского профиля через v2 manager

Сначала загрузите env-файл RU-сервера. Bootstrap записал туда `VPNCHAIN_SERVER_PUBLIC_KEY` из `awg-ru` и `VPNCHAIN_SERVER_ENDPOINT` из параметра `--server-endpoint`, поэтому клиентский конфиг генерируется сразу готовым — без ручной замены `PublicKey` и `Endpoint`.

```bash
set -a
. /etc/vpnchain/vpnchain.env
set +a
```

Инициализируйте SQLite DB **на RU-сервере**:

```bash
sudo python3 -m vpnchain.cli \
  --db /var/lib/vpnchain/vpnchain.sqlite \
  init-db
```

Создайте профиль и сохраните одноразовый клиентский конфиг вне Git checkout:

```bash
sudo --preserve-env=VPNCHAIN_SERVER_PUBLIC_KEY,VPNCHAIN_SERVER_ENDPOINT \
  python3 -m vpnchain.cli \
  --db /var/lib/vpnchain/vpnchain.sqlite \
  peer add phone \
  --platform android \
  --output /var/lib/vpnchain/generated/phone.conf
```

Для iOS:

```bash
sudo --preserve-env=VPNCHAIN_SERVER_PUBLIC_KEY,VPNCHAIN_SERVER_ENDPOINT \
  python3 -m vpnchain.cli \
  --db /var/lib/vpnchain/vpnchain.sqlite \
  peer add iphone \
  --platform ios \
  --export-profile amneziawg-ios \
  --output /var/lib/vpnchain/generated/iphone.conf
```

Private key клиента показывается/записывается один раз и не хранится в SQLite. Ручная замена `PublicKey`/`Endpoint` не нужна.

По умолчанию клиентские адреса выдаются из подсети RU `awg0`: `10.8.0.0/24` (`awg0` сервера — `10.8.0.1/24`, первые клиенты — `10.8.0.3/32`, `10.8.0.4/32`, ...). Если задаёте `--address` вручную, он тоже должен быть из этой подсети, иначе handshake может быть, но интернет у клиента не появится из-за неверной обратной маршрутизации.

Если env не загружен, те же значения можно передать явно:

```bash
sudo python3 -m vpnchain.cli \
  --db /var/lib/vpnchain/vpnchain.sqlite \
  peer add phone \
  --platform android \
  --server-public-key "$(sudo docker exec awg-ru cat /etc/amnezia/amneziawg/server_public_key)" \
  --server-endpoint <RU_PUBLIC_HOST_OR_IP>:51820 \
  --output /var/lib/vpnchain/generated/phone.conf
```

## 8. Как применить пользовательского клиента к live Docker runtime

Создание профиля в SQLite генерирует клиентский конфиг и сохраняет публичную часть профиля. Чтобы клиент подключался к live `awg0`, запишите его public key в `/etc/vpnchain/vpnchain.env` на RU-сервере.

Пример:

```dotenv
CLIENT_PUB_KEY=<CLIENT_PUBLIC_KEY>
CLIENT2_PUB_KEY=<CLIENT_PUBLIC_KEY>
```

После редактирования env-файла выполните **на RU-сервере**:

```bash
sudo scripts/vpnchain-bootstrap.sh \
  --env-file /etc/vpnchain/vpnchain.env \
  --ru-uplink-conf /etc/vpnchain/ru-awg1.conf \
  --server-endpoint <RU_PUBLIC_HOST_OR_IP>:51820 \
  --apply
```

Проверить peer'ы:

```bash
sudo docker exec awg-ru awg show awg0
```

## 9. Веб-интерфейс

Локальный WebUI на RU-сервере:

```bash
sudo python3 -m vpnchain.cli \
  --db /var/lib/vpnchain/vpnchain.sqlite \
  webui \
  --host 127.0.0.1 \
  --port 8080 \
  --interface awg0 \
  --activity-tool awg
```

Откройте его через SSH forwarding со своего компьютера:

```bash
ssh -L 8080:127.0.0.1:8080 root@<RU_PUBLIC_HOST_OR_IP>
```

Затем откройте в браузере:

```text
http://127.0.0.1:8080
```

Не публикуйте WebUI наружу без TLS, firewall и авторизации. Если всё-таки открываете его через reverse proxy, используйте минимум:

```bash
--basic-auth user:password
```

## 10. Мониторинг

На RU-сервере можно мониторить межсерверный туннель `awg1`:

```bash
sudo nohup bash monitor_tunnel.sh <AM_PUBLIC_HOST_OR_IP> awg1 >/var/log/awg_monitor.log 2>&1 &
tail -f /var/log/awg_monitor.log
```

## 11. Проверка установки

На RU-сервере:

```bash
sudo docker ps
sudo docker logs awg-ru --tail=100
sudo docker exec awg-ru awg show awg0
sudo docker exec awg-ru awg show awg1
```

На AM/выходной сервере:

```bash
sudo docker ps
sudo docker logs awg-am --tail=100
sudo docker exec awg-am awg show awg0
```

С подключённого клиента:

```bash
curl https://ifconfig.me
curl https://www.cloudflare.com/cdn-cgi/trace | grep loc=
```

Ожидаемое поведение:

- обычные non-RU направления выходят через AM/exit;
- российские GeoIP и настроенные RU-domain направления выходят напрямую через RU-сервер;
- `awg show` показывает свежие handshakes и растущие counters.

Если handshake устарел:

1. проверьте, что UDP-порты открыты;
2. проверьте, что RU использует файл `/etc/vpnchain/ru-awg1.conf`;
3. проверьте, что пользовательский клиентский конфиг содержит правильный `Endpoint`;
4. проверьте, что public key пользователя записан в `/etc/vpnchain/vpnchain.env` на RU-сервере;
5. проверьте, что `Address` в клиентском конфиге находится в подсети RU `awg0` (`10.8.0.0/24` по умолчанию);
6. выполните `sudo docker exec awg-ru awg show awg0` и посмотрите, есть ли peer клиента.
