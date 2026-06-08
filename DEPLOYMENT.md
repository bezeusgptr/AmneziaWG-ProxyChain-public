# Руководство по развертыванию AmneziaWG-ProxyChain

> **Примечание по v2:** для новых установок используйте [`docs/v2/quick-start.md`](./docs/v2/quick-start.md) и `scripts/vpnchain-bootstrap.sh`. Этот файл описывает низкоуровневую настройку Docker runtime.

Пошаговое описание ручной сборки двухузловой прокси-цепочки на базе AmneziaWG с использованием Docker и нативного модуля ядра.

## Требования к серверам

- **ОС**: Debian 11 (Bullseye) или Debian 13 (Trixie). Рекомендуется Trixie (ядро 6.12) для выходного узла.
- **Docker**: установлен (`docker.io` или Docker CE).
- **Права**: root или sudo.

---

## Шаг 0. Установка модуля ядра AmneziaWG

> **Обязательный шаг** для производительной (режим ядра) работы. Без него контейнеры автоматически откатятся на медленную реализацию в пользовательском пространстве `amneziawg-go`.

Скопируйте и запустите скрипт `install_kernel_module.sh` **на каждом сервере**:

```bash
# Скопировать скрипт
scp install_kernel_module.sh root@<IP_СЕРВЕРА>:/tmp/

# Выполнить на сервере
ssh root@<IP_СЕРВЕРА> "bash /tmp/install_kernel_module.sh"
```

Скрипт выполняет:
1. Установку `build-essential`, `dkms`, `gnupg2`.
2. Подключение официального Amnezia PPA и установку пакета `amneziawg` (или сборку через DKMS для Trixie).
3. Загрузку модуля в ядро (`modprobe amneziawg`).

**Проверка:**
```bash
lsmod | grep amneziawg
# Ожидаемый вывод: amneziawg   114688  0
```

После успешной установки **требуется перезагрузка сервера** для загрузки нового ядра (если оно было обновлено):
```bash
reboot
```

---

## Шаг 1. Развертывание Выходного узла (Сервер №2)

1. Установите Docker и выполните Шаг 0 на зарубежном сервере.
2. Скопируйте репозиторий на сервер в `/opt/AmneziaWG-ProxyChain`.
3. Запустите контейнер, передав публичный ключ Входного узла:

```bash
cd /opt/AmneziaWG-ProxyChain
RU_PUB_KEY='<публичный_ключ_входного_узла>' docker compose up -d --build server-am
```

4. Дождитесь старта и извлеките публичный ключ этого сервера:
```bash
docker exec awg-am cat /etc/amnezia/amneziawg/server_public_key
```

**Проверка корректного (режим ядра) запуска:**
```bash
docker logs awg-am | grep -i "amneziawg-go"
# Строки об amneziawg-go должны ОТСУТСТВОВАТЬ, либо контейнер создаёт интерфейс напрямую:
#   [#] ip link add awg0 type amneziawg   — без ошибки "Unknown device type"
```

---

## Шаг 2. Развертывание Входного узла (Сервер №1 — РФ)

1. Установите Docker и выполните Шаг 0 на российском сервере.
2. Скопируйте репозиторий в `/opt/AmneziaWG-ProxyChain`.
3. Запустите контейнер, передав ключ и адрес выходного узла:

```bash
cd /opt/AmneziaWG-ProxyChain

CLIENT_PUB_KEY='<ключ_клиента_1>' \
AM_PUB_KEY='<ключ_выходного_узла>' \
AM_ENDPOINT='<ip_выходного_узла>:51821' \
docker compose up -d --build server-ru
```

4. После запуска сервер автоматически скачает список российских IP-подсетей и настроит маршрутизацию.
5. Извлеките публичный ключ входного сервера (нужен для клиентского конфига):
```bash
docker exec awg-ru cat /etc/amnezia/amneziawg/server_public_key
```

---

## Шаг 3. Настройка клиента

Создайте конфигурационный файл и импортируйте его в приложение AmneziaWG:

```ini
[Interface]
PrivateKey = <приватный_ключ_клиента>
Address = 10.8.0.2/32
DNS = 8.8.8.8, 1.1.1.1

# Параметры маскировки (должны совпадать с сервером)
Jc = 4
Jmin = 50
Jmax = 1000
S1 = 80
S2 = 120
H1 = 1
H2 = 2
H3 = 3
H4 = 4

[Peer]
PublicKey = <публичный ключ Входного узла из Шага 2.5>
Endpoint = <IP входного узла>:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

Клиентские IP-адреса для нескольких устройств: `10.8.0.2`, `10.8.0.3`, `10.8.0.4`, ...

---

## Добавление дополнительных клиентов (до 17 слотов)

```bash
CLIENT_PUB_KEY='<ключ1>' \
CLIENT2_PUB_KEY='<ключ2>' \
CLIENT3_PUB_KEY='<ключ3>' \
# ...
CLIENT17_PUB_KEY='<ключ17>' \
AM_PUB_KEY='<ключ_выходного_узла>' \
AM_ENDPOINT='<ip>:51821' \
docker compose up -d server-ru
```

Слоты 1–17 уже описаны в `server-ru/awg0.conf.template` и `docker-compose.yml`; неиспользуемые переменные можно не задавать — пустые `[Peer]`-секции удаляются entrypoint-скриптом при старте.

---

## Шаг 4. Мониторинг и автовосстановление канала

Запустите скрипт мониторинга на RU-сервере, передав IP выходного сервера и интерфейс uplink прокси-цепочки:

```bash
scp monitor_tunnel.sh root@<IP_RU_СЕРВЕРА>:/tmp/
ssh root@<IP_RU_СЕРВЕРА> "nohup bash /opt/AmneziaWG-ProxyChain/monitor_tunnel.sh <IP_AM_СЕРВЕРА> awg1 &>/var/log/awg_monitor.log &"
```

Лог пишется в `/var/log/awg_monitor.log`. При потерях > 20% скрипт делает восстановление с паузами между попытками `0 / 5 / 15` минут, максимум 3 попытки:

- если интерфейс управляется контейнером `awg-ru`, выполняется `docker restart -t 20 awg-ru`;
- если конфиг интерфейса найден на хосте, выполняется `awg-quick down/up`;
- если владелец не найден, пишется `ERROR` без фиктивного рестарта.

Для systemd-запуска можно оформить unit, например `awg-monitor.service`, с `ExecStart=/bin/bash /opt/AmneziaWG-ProxyChain/monitor_tunnel.sh <IP_AM_СЕРВЕРА> awg1`.

---

## Обновление ядра через Backports (рекомендуется для Debian 11)

Если на вашем Debian 11 старое ядро, рекомендуется обновить его до 6.1 LTS через официальные backports:

```bash
echo "deb http://deb.debian.org/debian bullseye-backports main" >> /etc/apt/sources.list
apt update
apt install -t bullseye-backports linux-image-amd64 linux-headers-amd64
reboot
```

После перезагрузки повторно запустите `install_kernel_module.sh`.
