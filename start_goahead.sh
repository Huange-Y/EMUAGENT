#!/bin/bash
ROOTFS=/tmp/emulation_agent/rootfs/dir816
kill  2>/dev/null
cp /lib/libnvram-0.9.28.so.orig /lib/libnvram-0.9.28.so
cp /bin/goahead.orig /bin/goahead 2>/dev/null
mkdir -p /tmp/run && echo 1 > /tmp/run/nvramd.pid
echo 'GoAhead starting...'
cd  && setsid qemu-mipsel-static -L .   -E HOME=/ -E PATH=/bin:/sbin:/usr/bin:/usr/sbin   ./bin/goahead
