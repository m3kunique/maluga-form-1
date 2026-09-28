FROM alpine:3.21 AS tools-builder
RUN apk add --no-cache git make build-base linux-headers
WORKDIR /build
RUN git clone https://github.com/amnezia-vpn/amneziawg-tools.git . --depth=1 \
    && cd src \
    && make -j$(nproc) \
    && WITH_WGQUICK=yes make install

FROM ghcr.io/zeozeozeo/amneziawg-client:latest
COPY --from=tools-builder /usr/bin/awg /usr/bin/awg
COPY --from=tools-builder /usr/bin/awg-quick /usr/bin/awg-quick

# Replace /init.sh with daemonized version that does not exit when stdin closes
RUN cat << 'INITEOF' > /init.sh
#!/bin/bash
ip link delete dev wg0 2>/dev/null || true
find /etc/amnezia/amneziawg -mindepth 1 -delete

COUNTER=0
for s in $(find /config -name "*.conf")
do
  if test -f ${s}
  then
    COUNTER=$(( COUNTER + 1 ))
    basename=$(basename ${s})
    name=${basename%.conf}
    echo awg interface "${name}" will be created from config file "${basename}"
    cp ${s} /etc/amnezia/amneziawg/${name}.conf
    chmod 600 /etc/amnezia/amneziawg/${name}.conf
    awg-quick up ${name}
    iptables -A FORWARD -i ${name} -j ACCEPT 2>/dev/null || true
    iptables -A FORWARD -o ${name} -j ACCEPT 2>/dev/null || true
    iptables -A FORWARD -i ${name} -o ${name} -j ACCEPT 2>/dev/null || true
  fi
done

if [[ $COUNTER -lt 1 ]]
then
  echo "There are no config files in the /config folder"
  exit 1
fi

echo "AmneziaWG tunnel is UP! Holding process open..."
trap "awg-quick down wg0; exit 0" SIGTERM SIGINT
sleep infinity &
wait $!
INITEOF
RUN chmod +x /init.sh
