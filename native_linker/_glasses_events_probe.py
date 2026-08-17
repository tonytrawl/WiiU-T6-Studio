#!/usr/bin/env python3
"""GLASSES EVENT MODEL — probe build (NOT landed, NOT wired into loader_sim).

The Glasses span is currently modelled by a LINEAR copy: loader_sim's delim path
does `w.write_bytes(CO[cur:end])` with only a 56-byte TEMP root, so inline
Material/GfxImage roots are charged to block 5 (they should be TEMP) and the
0x2000 pixel pads are not charged at all. Measured consequence: the model runs
−20,864 low from the Glasses span onward, which mis-bakes raid's clipMap sides
aliases.

⭐ NOT A NEW WALKER FOR MATERIALS/IMAGES/FX — it composes the existing, validated
models: `rt_events_gfxworld._material` (which calls `_image`, 8 KB pixel pads)
and `rt_events_fx.fx_events`. Only the Glasses container itself is new; its
structure mirrors `raid_oracle_control._console_glasses_end` exactly so file
consumption cannot drift from the delimiter that defines the span.

PRE-REGISTERED PREDICTIONS (written before running):
 P0 FILE CLOSURE — the walker's end offset == `_console_glasses_end`'s, exactly,
    on genuine AND ours. Any drift and nothing else is reported.
 P1 THREE pixel pads inside the span (the three ~8 KB steps measured at file
    82,480,651→82,489,210→82,497,755→82,506,287).
 P2 block-5 consumption MINUS the current linear consumption == **+20,864**
    (the measured band displacement). Residual reported exactly, not rounded;
    small container-field alignments I cannot derive from the RPL are the
    expected source of any residue and are declared as such up front.
"""
import struct, sys

sys.path.insert(0, '.')
sys.path.insert(0, '../wiiu_ref')
import alloc_events as AE
import rt_events_gfxworld as GW
import rt_events_fx as RF
import raid_oracle_control as RC
import loader_sim as LS
import rt_events_exact as RTX

PTRS = (0xFFFFFFFF, 0xFFFFFFFE)
GLASS_ROOT = 56
GLASS_ENTRY = 140
GLASSDEF = 60


def glasses_events(d, off, e='>'):
    """Events for one console Glasses asset. Returns (events, end_offset).
    Mirrors _console_glasses_end's traversal exactly."""
    c = AE.Ev(d, off, e)
    u32 = lambda o: struct.unpack_from(e + 'I', d, o)[0]
    c.temp(GLASS_ROOT)                       # the asset root -> TEMP
    if u32(off) in PTRS:
        c.cstr()                             # name
    num = u32(off + 4)
    if u32(off + 8) in PTRS:
        gbase = c.o
        c.seg(num * GLASS_ENTRY, 4)          # glasses[num]
        for i in range(num):
            gb = gbase + i * GLASS_ENTRY
            if u32(gb + 16) in PTRS:
                gd = c.o
                c.seg(GLASSDEF, 4)           # inline GlassDef
                if u32(gd) in PTRS:
                    c.cstr()
                for mo in (28, 32, 36):      # up to 3 inline Materials
                    if u32(gd + mo) in PTRS:
                        GW._material(c)      # <- reused model (images + pads)
                for so in (40, 44, 48):      # 3 strings
                    if u32(gd + so) in PTRS:
                        c.cstr()
                for fo in (52, 56):          # 2 inline FX
                    if u32(gd + fo) in PTRS:
                        ev, end = RF.fx_events(d, c.o, e)
                        rel0 = c.o - c.base
                        for it in ev:
                            if it[0] == 'seg':
                                c.events.append(('seg', rel0 + it[1], it[2], it[3]))
                            elif it[0] == 'temp':
                                c.events.append(('temp', rel0 + it[1], it[2]))
                            else:
                                c.events.append(it)
                        c.o = end
            if u32(gb + 80) in PTRS:
                c.seg(d[gb + 77] * 8, 4)
    return c.events, c.o


def block5(events, start_cursor=0):
    """block-5 bytes consumed, applying alignment the way replay_events does.
    ⚠ `start_cursor` is REQUIRED for correctness: 0x2000 alignment depends on the
    ABSOLUTE block-5 phase at the span start, not a span-relative one. Running it
    from 0 gave the wrong pad sizes and hid one pad entirely."""
    cur = start_cursor
    pads = []
    for ev in events:
        if ev[0] == 'seg':
            _, rel, size, align = ev
            if align > 1 and cur % align:
                pad = align - (cur % align)
                cur += pad
                if pad >= 4096:
                    pads.append((rel, pad))
            cur += size
        # 'temp' consumes file bytes but NO block-5 space
    return cur, pads


for tag, path in (('genuine', '../wiiu_ref/mp_raid_genuine.zone'),
                  ('ours(b34)',
                   'C:/Users/TONY-M~1/AppData/Local/Temp/claude/'
                   'C--Users-Tony---Main-Rig-Downloads-Testing-enviroment/'
                   '0051b778-2677-4dc3-8a40-7121416703c0/scratchpad/'
                   'mp_raid_boot34.zone')):
    Z = open(path, 'rb').read()
    em_, spans, _ = LS.simulate(path, verbose=False, policy=RTX.policy())
    rt_ = LS.RuntimeMap(em_.omap)
    # ⚠ genuine carries TWO Glasses spans: a ZERO-LENGTH stub (idx 1) and the
    # real asset (idx 851). Taking [0] grabbed the stub and walked garbage —
    # select by non-empty extent, not by order.
    gs = [(s, e) for (i, nm, root, s, e) in spans if root == 'Glasses' and e > s]
    if not gs:
        print('%s: no non-empty Glasses span' % tag)
        continue
    s, e_ = max(gs, key=lambda t: t[1] - t[0])
    delim_end = RC._console_glasses_end(Z, s)
    ev, my_end = glasses_events(Z, s)
    linear = (e_ - s) - GLASS_ROOT
    span_b5 = rt_.rt(s - 64)
    b5, pads = block5(ev, span_b5)
    b5 -= span_b5                       # consumption, not absolute cursor
    print('  span block-5 start (model): %d  (phase %% 8192 = %d)'
          % (span_b5, span_b5 % 8192))
    print('\n%s  span %d..%d (%d B)' % (tag, s, e_, e_ - s))
    print('  P0 file closure: delim_end %d · walker_end %d · MATCH %s'
          % (delim_end, my_end, delim_end == my_end))
    print('  P1 pixel pads >=4096: %d  at file %s'
          % (len(pads), [s + r for (r, p) in pads][:6]))
    print('     pad sizes: %s' % [p for (_, p) in pads][:6])
    print('  P2 block-5: walker %d · current-linear %d · DELTA %+d  (predicted +20864)'
          % (b5, linear, b5 - linear))
