#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Event-driven rtnetlink listener for one network namespace.

Subscribes to link, neighbour (IPv4 neighbours *and* AF_BRIDGE FDB, i.e. the
same notifications ``ip monitor neigh`` and ``bridge monitor fdb`` print),
IPv4 route and nexthop-object groups.  Every message is stamped with
CLOCK_MONOTONIC and CLOCK_REALTIME at userspace receipt (not kernel event
time).  An initial dump of the same tables is taken once, before the
observation window, and tagged ``src=dump``.  Socket overruns (ENOBUFS) and
the kernel's per-socket drop counter (/proc/net/netlink) are recorded so that
capture loss can be accounted for.
"""
import argparse
import json
import signal
import socket
import struct
import time

NETLINK_ROUTE = 0
SOL_NETLINK = 270
NETLINK_ADD_MEMBERSHIP = 1
SO_RCVBUFFORCE = 33

RTMGRP_LINK = 0x1
RTMGRP_NEIGH = 0x4
RTMGRP_IPV4_ROUTE = 0x40
RTNLGRP_NEXTHOP = 32

NLMSG_ERROR, NLMSG_DONE = 2, 3
NLM_F_REQUEST, NLM_F_DUMP = 0x1, 0x300

MSG = {16: 'NEWLINK', 17: 'DELLINK', 24: 'NEWROUTE', 25: 'DELROUTE',
       28: 'NEWNEIGH', 29: 'DELNEIGH', 104: 'NEWNEXTHOP', 105: 'DELNEXTHOP'}
GET = {'link': 18, 'route': 26, 'neigh': 30, 'nexthop': 106}

NUD = [(0x01, 'INCOMPLETE'), (0x02, 'REACHABLE'), (0x04, 'STALE'), (0x08, 'DELAY'),
       (0x10, 'PROBE'), (0x20, 'FAILED'), (0x40, 'NOARP'), (0x80, 'PERMANENT')]
NTF = [(0x01, 'use'), (0x02, 'self'), (0x04, 'master'), (0x08, 'proxy'),
       (0x10, 'extern_learn'), (0x20, 'offloaded'), (0x40, 'sticky'), (0x80, 'router')]
IFF = [(0x1, 'UP'), (0x40, 'RUNNING'), (0x10000, 'LOWER_UP')]


def bits(value, table):
    names = [name for bit, name in table if value & bit]
    return names or (['NONE'] if value == 0 else [])


def attrs(payload, offset):
    out = {}
    while offset + 4 <= len(payload):
        length, kind = struct.unpack_from('=HH', payload, offset)
        if length < 4 or offset + length > len(payload):
            break
        out.setdefault(kind & 0x3fff, payload[offset + 4:offset + length])
        offset += (length + 3) & ~3
    return out


def u32(raw):
    return struct.unpack('=I', raw[:4])[0] if raw and len(raw) >= 4 else None


def ip_of(raw, family=None):
    if raw is None:
        return None
    if len(raw) == 4:
        return socket.inet_ntop(socket.AF_INET, raw)
    if len(raw) == 16:
        return socket.inet_ntop(socket.AF_INET6, raw)
    return raw.hex()


def mac_of(raw):
    return ':'.join('%02x' % b for b in raw) if raw and len(raw) == 6 else (raw.hex() if raw else None)


class Decoder:
    def __init__(self):
        self.names = {}

    def name(self, index):
        if index in self.names:
            return self.names[index]
        try:
            return socket.if_indextoname(index)
        except OSError:
            return None

    def link(self, payload, rec):
        _, _, _, index, flags, _ = struct.unpack_from('=BBHiII', payload)
        a = attrs(payload, 16)
        name = a.get(3, b'').rstrip(b'\0').decode(errors='replace') or None
        if rec['msg'] == 'NEWLINK' and name:
            self.names[index] = name
        rec.update(ifindex=index, ifname=name or self.name(index), flags=bits(flags, IFF),
                   master=self.name(u32(a.get(10))) if a.get(10) else None,
                   master_ifindex=u32(a.get(10)), mac=mac_of(a.get(1)),
                   operstate=a[16][0] if a.get(16) else None)
        if rec['msg'] == 'DELLINK':
            self.names.pop(index, None)

    def neigh(self, payload, rec):
        family, _, _, index, state, flags, ntype = struct.unpack_from('=BBHiHBB', payload)
        a = attrs(payload, 12)
        rec.update(family={2: 'inet', 10: 'inet6', 7: 'bridge'}.get(family, family),
                   ifindex=index, ifname=self.name(index), state=bits(state, NUD),
                   state_raw=state, flags=bits(flags, NTF), flags_raw=flags, ndm_type=ntype,
                   lladdr=mac_of(a.get(2)), protocol=a[12][0] if a.get(12) else None)
        if family == 7:
            rec['mac'] = rec.pop('lladdr')
            rec['vtep'] = ip_of(a.get(1))
            rec['vni'] = u32(a.get(7))
            rec['master'] = self.name(u32(a.get(9))) if a.get(9) else None
            rec['nhid'] = u32(a.get(13))
        else:
            rec['ip'] = ip_of(a.get(1))
            rec['mac'] = rec.pop('lladdr')

    def route(self, payload, rec):
        family, dst_len, _, _, table, proto, scope, rtype, _ = struct.unpack_from('=BBBBBBBBI', payload)
        a = attrs(payload, 12)
        rec.update(family=family, dst=(ip_of(a.get(1)) or ('0.0.0.0' if family == 2 else '::')),
                   dst_len=dst_len, table=u32(a.get(15)) or table, protocol=proto,
                   scope=scope, rtype=rtype, gateway=ip_of(a.get(5)),
                   oif=self.name(u32(a.get(4))) if a.get(4) else None, nhid=u32(a.get(30)),
                   metric=u32(a.get(6)))
        if a.get(9):
            hops, raw, pos = [], a[9], 0
            while pos + 8 <= len(raw):
                length, hflags, _, index = struct.unpack_from('=HBBi', raw, pos)
                if length < 8:
                    break
                sub = attrs(raw[pos:pos + length], 8)
                hops.append(dict(oif=self.name(index), gateway=ip_of(sub.get(5)), flags=hflags))
                pos += (length + 3) & ~3
            rec['multipath'] = hops

    def nexthop(self, payload, rec):
        family, scope, proto, _, flags = struct.unpack_from('=BBBBI', payload)
        a = attrs(payload, 8)
        rec.update(family=family, nhid=u32(a.get(1)), gateway=ip_of(a.get(6)),
                   oif=self.name(u32(a.get(5))) if a.get(5) else None, protocol=proto,
                   fdb=11 in a)
        if a.get(2):
            raw = a[2]
            rec['group'] = [dict(id=struct.unpack_from('=I', raw, i)[0], weight=raw[i + 4] + 1)
                            for i in range(0, len(raw) - 7, 8)]

    def decode(self, data, mono, wall, src, portid=None):
        out, pos = [], 0
        while pos + 16 <= len(data):
            length, mtype, nflags, seq, pid = struct.unpack_from('=IHHII', data, pos)
            if length < 16 or pos + length > len(data):
                break
            payload = data[pos + 16:pos + length]
            pos += (length + 3) & ~3
            if mtype in (NLMSG_DONE,):
                out.append(dict(kind='dump_done', seq=seq))
                continue
            if mtype == NLMSG_ERROR:
                err = struct.unpack_from('=i', payload)[0] if len(payload) >= 4 else None
                out.append(dict(kind='nlmsg_error', error=err, seq=seq))
                continue
            name = MSG.get(mtype)
            if not name:
                continue
            rec = dict(kind='netlink', mono_ns=mono, wall_ns=wall, src=src, msg=name,
                       nl_seq=seq, nl_pid=pid)
            try:
                if mtype in (16, 17):
                    self.link(payload, rec)
                elif mtype in (28, 29):
                    self.neigh(payload, rec)
                elif mtype in (24, 25):
                    self.route(payload, rec)
                    if rec['family'] != 2:
                        continue
                else:
                    self.nexthop(payload, rec)
            except struct.error as exc:
                rec['decode_error'] = str(exc)
            out.append(rec)
        return out


def netlink_drops(portid):
    try:
        with open('/proc/net/netlink') as handle:
            lines = handle.read().splitlines()
        header = lines[0].split()
        for line in lines[1:]:
            cols = line.split()
            if cols[1] == str(NETLINK_ROUTE) and cols[2] == str(portid):
                return dict(zip(header, cols))
    except (OSError, IndexError):
        pass
    return None


def dump(decoder, write, kinds):
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE)
    sock.bind((0, 0))
    sock.settimeout(2)
    seq = 1000
    for kind, family in kinds:
        seq += 1
        if kind == 'link':
            body = struct.pack('=BBHiII', family, 0, 0, 0, 0, 0)
        elif kind == 'neigh':
            body = struct.pack('=BBHiHBB', family, 0, 0, 0, 0, 0, 0)
        elif kind == 'route':
            body = struct.pack('=BBBBBBBBI', family, 0, 0, 0, 0, 0, 0, 0, 0)
        else:
            body = struct.pack('=BBBBI', family, 0, 0, 0, 0)
        sock.send(struct.pack('=IHHII', 16 + len(body), GET[kind], NLM_F_REQUEST | NLM_F_DUMP, seq, 0) + body)
        done = False
        while not done:
            try:
                data = sock.recv(1 << 20)
            except socket.timeout:
                write(dict(kind='dump_timeout', table=kind))
                break
            for rec in decoder.decode(data, time.monotonic_ns(), time.time_ns(), 'dump'):
                if rec['kind'] in ('dump_done', 'nlmsg_error'):
                    done = True
                    if rec['kind'] == 'nlmsg_error':
                        write(dict(rec, table=kind))
                else:
                    rec['table_dump'] = kind
                    write(rec)
    sock.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--node', required=True)
    args = parser.parse_args()
    running = [True]

    def stop(*_):
        running[0] = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_ROUTE)
    try:
        sock.setsockopt(socket.SOL_SOCKET, SO_RCVBUFFORCE, 16 << 20)
    except OSError:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 << 20)
    sock.bind((0, RTMGRP_LINK | RTMGRP_NEIGH | RTMGRP_IPV4_ROUTE))
    try:
        sock.setsockopt(SOL_NETLINK, NETLINK_ADD_MEMBERSHIP, RTNLGRP_NEXTHOP)
    except OSError:
        pass
    sock.settimeout(0.2)
    portid = sock.getsockname()[0]
    decoder = Decoder()
    overruns = 0
    with open(args.output, 'x', buffering=1 << 16) as out:
        def write(rec):
            rec.setdefault('node', args.node)
            out.write(json.dumps(rec, separators=(',', ':')) + '\n')

        write(dict(kind='ready', node=args.node, portid=portid, mono_ns=time.monotonic_ns(),
                   wall_ns=time.time_ns(), drops=netlink_drops(portid),
                   rcvbuf=sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)))
        dump(decoder, write, [('link', 0), ('neigh', 2), ('neigh', 7), ('route', 2), ('nexthop', 0)])
        write(dict(kind='dump_complete', mono_ns=time.monotonic_ns(), wall_ns=time.time_ns()))
        out.flush()
        last_flush = time.monotonic()
        while running[0]:
            try:
                data = sock.recv(1 << 20)
            except socket.timeout:
                data = None
            except OSError as exc:
                overruns += 1
                write(dict(kind='socket_error', errno=exc.errno, error=str(exc),
                           mono_ns=time.monotonic_ns(), wall_ns=time.time_ns()))
                continue
            if data:
                mono, wall = time.monotonic_ns(), time.time_ns()
                for rec in decoder.decode(data, mono, wall, 'event'):
                    write(rec)
            if time.monotonic() - last_flush > 0.5:
                out.flush()
                last_flush = time.monotonic()
        write(dict(kind='final', mono_ns=time.monotonic_ns(), wall_ns=time.time_ns(),
                   overruns=overruns, drops=netlink_drops(portid)))


if __name__ == '__main__':
    main()
