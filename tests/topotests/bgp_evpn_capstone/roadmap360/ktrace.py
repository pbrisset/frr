#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
"""Kernel kprobe tracing of neighbour updates and ARP receive processing.

Uses a private tracefs instance with ``trace_clock=mono`` so timestamps are
CLOCK_MONOTONIC (microsecond display precision).  Probes:

* ``r360/nupd``     entry of ``__neigh_update`` with old state, new state,
                    update flags, neighbour flags, IPv4 key, device and netns.
* ``r360/nupd_ret`` return value of ``__neigh_update`` (-EPERM = rejected).
* ``r360/arp_rx``   entry of ``arp_process`` with opcode, sender/target IP,
                    sender MAC, device and netns.

The tracing buffer is kernel-global, so every namespace is visible; events
are attributed to topology nodes using the namespace inode number.
"""
import os
import re
import socket
import struct

TRACEFS = '/sys/kernel/tracing'
INSTANCE = 'r360'
PROBES = [
    'p:r360/nupd __neigh_update new=new uflags=flags old=neigh->nud_state:u8 '
    'nflags=neigh->flags:u8 fam=neigh->tbl->family:u16 key=neigh->primary_key:u32 '
    'dev=neigh->dev->name:string inum=neigh->dev->nd_net.net->ns.inum:u32 '
    'cur=neigh->ha:x8[6] nmac=+0(lladdr):x8[6]',
    'r:r360/nupd_ret __neigh_update ret=$retval:s32',
    'p:r360/arp_rx arp_process op=+6(skb->data):u16 sha=+8(skb->data):x8[6] '
    'sip=+14(skb->data):u32 tip=+24(skb->data):u32 dev=skb->dev->name:string '
    'inum=net->ns.inum:u32',
]
EVENTS = ['nupd', 'nupd_ret', 'arp_rx']


def _write(path, value, append=False):
    # Raw O_APPEND write: Python's text 'a' mode lseek()s, which tracefs rejects (EINVAL).
    fd = os.open(path, os.O_WRONLY | (os.O_APPEND if append else os.O_TRUNC))
    try:
        os.write(fd, (value + '\n').encode())
    finally:
        os.close(fd)


def ensure_mounted():
    if not os.path.exists(os.path.join(TRACEFS, 'kprobe_events')):
        os.system('mount -t tracefs nodev {} 2>/dev/null'.format(TRACEFS))
    return os.path.exists(os.path.join(TRACEFS, 'kprobe_events'))


def teardown():
    inst = os.path.join(TRACEFS, 'instances', INSTANCE)
    if os.path.isdir(inst):
        for event in EVENTS:
            enable = os.path.join(inst, 'events', 'r360', event, 'enable')
            if os.path.exists(enable):
                _write(enable, '0')
        try:
            os.rmdir(inst)
        except OSError:
            pass
    for event in EVENTS:
        enable = os.path.join(TRACEFS, 'events', 'r360', event, 'enable')
        if os.path.exists(enable):
            _write(enable, '0')
        try:
            _write(os.path.join(TRACEFS, 'kprobe_events'), '-:r360/' + event, append=True)
        except OSError:
            pass


def setup(buffer_kb=32768):
    """Create the probes; return dict with status/errors.  Idempotent."""
    if not ensure_mounted():
        return dict(enabled=False, error='tracefs unavailable')
    teardown()
    errors = []
    for probe in PROBES:
        try:
            _write(os.path.join(TRACEFS, 'kprobe_events'), probe, append=True)
        except OSError as exc:
            errors.append(dict(probe=probe, error=str(exc)))
    inst = os.path.join(TRACEFS, 'instances', INSTANCE)
    os.makedirs(inst, exist_ok=True)
    _write(os.path.join(inst, 'trace_clock'), 'mono')
    _write(os.path.join(inst, 'buffer_size_kb'), str(buffer_kb))
    _write(os.path.join(inst, 'trace'), '')
    filt = os.path.join(inst, 'events', 'r360', 'nupd', 'filter')
    if os.path.exists(filt):
        _write(filt, 'fam == 2')
    for event in EVENTS:
        enable = os.path.join(inst, 'events', 'r360', event, 'enable')
        if os.path.exists(enable):
            _write(enable, '1')
        else:
            errors.append(dict(event=event, error='not created'))
    try:
        with open(os.path.join(TRACEFS, 'error_log')) as handle:
            tail = handle.read()[-2000:]
    except OSError:
        tail = ''
    return dict(enabled=not errors, errors=errors, error_log_tail=tail, probes=PROBES)


def collect(path):
    """Disable, write raw trace text and per-cpu stats to *path*; return stats."""
    inst = os.path.join(TRACEFS, 'instances', INSTANCE)
    stats = {}
    if not os.path.isdir(inst):
        return dict(error='instance missing')
    for event in EVENTS:
        enable = os.path.join(inst, 'events', 'r360', event, 'enable')
        if os.path.exists(enable):
            _write(enable, '0')
    with open(os.path.join(inst, 'trace')) as src, open(path, 'w') as dst:
        for line in src:
            dst.write(line)
    percpu = os.path.join(inst, 'per_cpu')
    for cpu in sorted(os.listdir(percpu)):
        try:
            with open(os.path.join(percpu, cpu, 'stats')) as handle:
                stats[cpu] = dict(line.split(': ', 1) for line in handle.read().strip().splitlines()
                                  if ': ' in line)
        except OSError:
            pass
    return stats


LINE = re.compile(r'^\s*(?P<comm>.+?)-(?P<pid>\d+)\s+\[(?P<cpu>\d+)\]\s+\S+\s+'
                  r'(?P<ts>\d+\.\d+):\s+(?P<event>\w+):\s+\((?P<loc>[^)]*)\)\s*(?P<args>.*)$')
ARG = re.compile(r'(\w+)=("(?:[^"]*)"|\{[^}]*\}|\S+)')
NEIGH_UPDATE_FLAGS = [(1 << 0, 'OVERRIDE'), (1 << 1, 'WEAK_OVERRIDE'), (1 << 2, 'OVERRIDE_ISROUTER'),
                      (1 << 3, 'USE'), (1 << 4, 'MANAGED'), (1 << 5, 'EXT_LEARNED'),
                      (1 << 6, 'ISROUTER'), (1 << 7, 'ADMIN')]
NUD = [(0x01, 'INCOMPLETE'), (0x02, 'REACHABLE'), (0x04, 'STALE'), (0x08, 'DELAY'),
       (0x10, 'PROBE'), (0x20, 'FAILED'), (0x40, 'NOARP'), (0x80, 'PERMANENT')]


def _num(value):
    try:
        return int(value, 0)
    except ValueError:
        return value


def _ipv4_le(value):
    return socket.inet_ntoa(struct.pack('<I', value & 0xffffffff))


def _mac(value):
    parts = value.strip('{}').split(',')
    try:
        return ':'.join('%02x' % int(p, 0) for p in parts)
    except ValueError:
        return value


def _names(value, table):
    return [n for b, n in table if value & b] or ['NONE']


def parse(lines, inum_map):
    """Yield decoded probe records; pair nupd entry with its return per (cpu,pid)."""
    pending = {}
    for raw in lines:
        match = LINE.match(raw)
        if not match:
            continue
        rec = dict(comm=match['comm'].strip(), pid=int(match['pid']), cpu=int(match['cpu']),
                   mono_ns=int(round(float(match['ts']) * 1e9)), event=match['event'])
        for key, value in ARG.findall(match['args']):
            rec[key] = value.strip('"') if value.startswith('"') else value
        if 'inum' in rec:
            rec['inum'] = _num(rec['inum'])
            rec['node'] = inum_map.get(str(rec['inum']), inum_map.get(rec['inum']))
        if rec['event'] == 'nupd':
            for key in ('new', 'uflags', 'old', 'nflags', 'fam', 'key'):
                rec[key] = _num(rec.get(key, '0'))
            rec['ip'] = _ipv4_le(rec['key'])
            rec['old_state'] = _names(rec['old'], NUD)
            rec['new_state'] = _names(rec['new'], NUD)
            rec['update_flags'] = _names(rec['uflags'], NEIGH_UPDATE_FLAGS)
            rec['ext_learned'] = bool(rec['nflags'] & 0x10)
            rec['cur_mac'] = _mac(rec.get('cur', ''))
            rec['new_mac'] = _mac(rec.get('nmac', ''))
            pending[(rec['cpu'], rec['pid'])] = rec
            continue
        if rec['event'] == 'nupd_ret':
            entry = pending.pop((rec['cpu'], rec['pid']), None)
            if entry is None:
                continue
            entry['ret'] = _num(rec.get('ret', '0'))
            entry['ret_mono_ns'] = rec['mono_ns']
            entry['rejected'] = entry['ret'] != 0
            yield entry
            continue
        if rec['event'] == 'arp_rx':
            op = _num(rec.get('op', '0'))
            rec['op'] = {0x0100: 'request', 0x0200: 'reply'}.get(op, op)
            rec['sip'] = _ipv4_le(_num(rec.get('sip', '0')))
            rec['tip'] = _ipv4_le(_num(rec.get('tip', '0')))
            rec['sha'] = _mac(rec.get('sha', ''))
            yield rec
    for entry in pending.values():
        entry['ret'] = None
        yield entry
