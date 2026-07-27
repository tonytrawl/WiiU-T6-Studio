#!/usr/bin/env python3
"""Census the XModel materialHandles ALIAS class (boot-28 front).

An XModel's materialHandles array precedes its run of inline material bodies:
[h0 h1 ... hn-1][inline body for each FOLLOW handle]...  A FOLLOW handle's body
is consumed inline; an ALIAS handle must point at the RUNTIME b5 offset of a
material body emitted elsewhere (first occurrence). Boot 28 proved one such
alias lands in vertex data -> engine fetches garbage 'Material*' -> AV in
R_SkinStaticModelsCameraForLod.

Census: collect every alias word that sits in a maximal run of FOLLOW/alias
words immediately preceding a known material body; test whether its payload
equals the runtime offset of some material body.

RAID CONTROL FIRST: on the genuine oracle zone (byte-exact, boots), with the
loader-sim runtime map, ~every such alias must land on a material body — that
validates the census logic before the skate number means anything.
"""
import bisect
import struct
import sys

sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')
from _nullct_oracle import scan

FOLLOW, INSERT = 0xFFFFFFFF, 0xFFFFFFFE
AL = lambda v: 0xA0000000 <= v < 0xC0000000


def census(zone_path, rt_of_file, tag):
    Z = open(zone_path, 'rb').read()
    _, mats, _, _, _, _ = scan(zone_path)
    offs = sorted(m['_off'] for m in mats)
    names = {m['_off']: m.get('name', '?') for m in mats}
    # collect handle runs; legal alias targets = runtime offsets of HANDLE SLOTS
    # (the loader rewrites a FOLLOW slot to the registered Material*; a dedup
    # alias points at that slot so the engine reads a valid pointer through it)
    aliases = []
    slots = []
    for o in offs:
        p = o - 4
        run = []
        while p > 0:
            w = struct.unpack_from('>I', Z, p)[0]
            if w in (FOLLOW, INSERT) or AL(w):
                run.append((p, w))
                p -= 4
            else:
                break
        for (wp, w) in run:
            slots.append(wp)
            if AL(w):
                aliases.append((wp, w, o))
    tgt = {}
    for wp in slots:
        r = rt_of_file(wp)
        if r is not None:
            tgt[int(r)] = wp
    print('%s: %d material bodies, %d handle slots with runtime offsets'
          % (tag, len(offs), len(tgt)))

    good = bad = 0
    bads = []
    seen = set()
    for (wp, w, body) in aliases:
        if wp in seen:
            continue
        seen.add(wp)
        pay = (w - 1) & 0x1FFFFFFF
        if pay in tgt:
            good += 1
        else:
            bad += 1
            bads.append((wp, w, pay, body))
    print('%s: handle-alias census: %d aliases, %d -> a material body, %d BROKEN'
          % (tag, len(seen), good, bad))
    for (wp, w, pay, body) in bads[:12]:
        print('   @file %9d alias 0x%08x payload %9d (preceding body %d %s)'
              % (wp, w, pay, body, names.get(body, '?')[:36]))
    return bads, tgt, names


if __name__ == '__main__':
    which = sys.argv[1] if len(sys.argv) > 1 else 'both'

    if which in ('raid', 'both'):
        import _alias_decode as AD
        dec, CO, spans = AD.build('../wiiu_ref/mp_raid_genuine.zone')
        census('../wiiu_ref/mp_raid_genuine.zone',
               lambda o: dec.rt(o - 64), 'RAID (oracle)')

    if which in ('skate', 'both'):
        import pickle
        from measured_rtmap import MeasuredRuntimeMap
        m = MeasuredRuntimeMap('_skate6_simmap.pkl', '_skate6_realmap.pkl')
        ae = pickle.load(open('_skate6_realmap.pkl', 'rb'))['ae']
        census('mp_skate_gfxtail23.zone',
               lambda o: m.rt(o - ae), 'SKATE gfxtail23')
