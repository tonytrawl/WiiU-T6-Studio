"""XModel runtime-layout GATE (rt_events_xmodel regression guard).

Replays rt_events_xmodel's allocation events against the 59 dump-measured
absolute block-5 runtime addresses in _xmodel_rt_gate.json (boot-16 zone
zm_nuked.ff.boot16 md5 279fc3f1, Cemu.exe.12984.dmp).  Any change to RULES that
does not reproduce 59/59 is wrong.

Needs the decrypted boot-16 zone; point XM_GATE_ZONE at it (the dump itself is
NOT needed -- the measurements are baked into the json).
"""
import sys, os, json, collections
SCR = os.environ.get('XM_GATE_DIR', os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCR)
sys.path.insert(0, r'C:\Users\Tony - Main Rig\Downloads\Testing enviroment\native_linker')
sys.path.insert(0, r'C:\Users\Tony - Main Rig\Downloads\Testing enviroment\wiiu_ref')
import rt_events_xmodel as RX

ZONE = os.environ.get('XM_GATE_ZONE',
    os.path.join(SCR, '_probe_boot16_xmodel.zone'))   # decrypted zm_nuked.ff.boot16
J = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '_xmodel_rt_gate.json')))
Z = open(ZONE, 'rb').read()

ASSETS = []
for a in J['assets']:
    s = int(a['file_start_hex'], 16)
    meas = [(r['off'], r['size'], r['label'], r['rt_b5'])
            for r in a['segments'] if r.get('rt_b5') is not None]
    ASSETS.append((s, int(a['file_end_hex'], 16), meas))

TRACES = {}
for (s, e, meas) in ASSETS:
    tr = []
    RX.TRACE = tr
    end, _ = RX.xmodel_events(Z, s, '>')
    RX.TRACE = None
    assert end == e, (hex(s), end, e)
    TRACES[s] = [(o, sz, kd) for (o, sz, kd) in tr if sz > 0]

N_MEAS = sum(len(m) for (_, _, m) in ASSETS)


def check(rules, verbose=False):
    ok = 0
    fails = []
    for (s, e, meas) in ASSETS:
        pos = {}
        cur = 0
        for (o, sz, kd) in TRACES[s]:
            mode, al = rules[kd]
            if mode == 't':
                continue
            if al > 1:
                cur = (cur + al - 1) & ~(al - 1)
            pos[o] = cur
            cur += sz
        anchor = None
        for (off, sz, lb, rt) in meas:
            if off not in pos:
                fails.append((hex(off), lb, 'no-alloc'))
                continue
            if anchor is None:
                anchor = rt - pos[off]
            got = pos[off] + anchor
            if got == rt:
                ok += 1
            else:
                fails.append((hex(off), lb, got - rt))
    if verbose:
        for f in fails:
            print('   FAIL %s %-32s %s' % f)
    return ok, N_MEAS


if __name__ == '__main__':
    rules = dict(RX.RULES); rules['root'] = ('t', 0)
    ok, n = check(rules, verbose=True)
    print('GATE %d/%d' % (ok, n))
