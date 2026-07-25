FROM golang:1.24-alpine AS builder

# Установка зависимостей для сборки
RUN apk add --no-cache make gcc musl-dev git linux-headers

# Сборка amneziawg-go (userspace реализация) — пинним конкретный коммит для воспроизводимости
RUN git clone https://github.com/amnezia-vpn/amneziawg-go.git /awg-go && \
    cd /awg-go && \
    git checkout 449d7cffd4adf86971bd679d0be5384b443e8be5 && \
    make && \
    cp amneziawg-go /usr/bin/amneziawg-go

# Сборка amneziawg-tools (утилиты awg и awg-quick) — пинним тег
RUN git clone --branch v1.0.20260223 --depth 1 https://github.com/amnezia-vpn/amneziawg-tools.git /awg-tools && \
    cd /awg-tools/src && \
    make && \
    make WITH_WGQUICK=yes install

# Финальный легковесный образ
FROM alpine:3.19

# Установка iptables, сетевых утилит, curl и ipset для выборочной маршрутизации
RUN apk add --no-cache \
    bash \
    curl \
    dnsmasq \
    gettext \
    iproute2 \
    ipset \
    iptables \
    openresolv

# Перенос бинарных файлов из builder'а
COPY --from=builder /usr/bin/amneziawg-go /usr/bin/amneziawg-go
COPY --from=builder /usr/bin/awg /usr/bin/awg
COPY --from=builder /usr/bin/awg-quick /usr/bin/awg-quick

# Рабочая директория для конфигураций
WORKDIR /etc/amnezia/amneziawg

# entrypoint.sh монтируется через volume в docker-compose.yml
ENTRYPOINT ["/entrypoint.sh"]
