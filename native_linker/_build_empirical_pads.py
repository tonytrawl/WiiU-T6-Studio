"""Build empirical runtime-pad dataset for the WiiU BO2 zone loader.

Pairs dump-measured b5 runtime offsets (_zmnuked_realmap.pkl) against
loader_sim's modeled layout for zm_nuked_authored.zone. Delta of cumulative
error between consecutive measured asset starts = pad bytes the real console
loader inserted within that gap that the sim does not model.

Conventions (verified):
  - realmap 'real' keys  : stream_b5 = file_off - 64 (asset start)
  - em.omap keys          : same stream_b5 convention; every measured key is
                            a registered allocation point (2815/2815 direct hits)
  - spans file_start/end  : raw file offsets (stream_b5 + 64)
"""
import json, pickle, bisect, collections
import loader_sim as LS

ZONE = 'zm_nuked_authored.zone'
OUT = '_empirical_pads.json'

em, spans, _ = LS.simulate(ZONE, verbose=False, policy=dict(gfx_skip=0))
with open('_zmnuked_realmap.pkl', 'rb') as f:
    rm = pickle.load(f)
real = rm['real']

nz = [s for s in spans if s[4] > s[3]]           # (idx, name, root, fstart, fend)
span_by_start = {s[3]: s for s in nz}            # file_start -> span
span_starts = sorted(span_by_start)
omap_keys = sorted(em.omap)

# sanity
missing = [k for k in real if k not in em.omap]
assert not missing, f'{len(missing)} measured keys absent from omap'
assert all(k + 64 in span_by_start for k in real), 'measured key not an asset start'

records = []
prev_err = None
prev_foff = None
rkeys = sorted(real)
for k in rkeys:
    foff = k + 64
    sp = span_by_start[foff]
    sim_rt = em.omap[k]
    real_rt = real[k]
    err = real_rt - sim_rt
    delta = None if prev_err is None else err - prev_err

    # assets fully contained in the gap (prev measured start, this start]:
    # the pad delta accumulated across these spans' allocations.
    gap_assets = []
    n_interior_allocs = 0
    if prev_foff is not None:
        i0 = bisect.bisect_left(span_starts, prev_foff)
        i1 = bisect.bisect_left(span_starts, foff)
        gap_assets = [span_by_start[s] for s in span_starts[i0:i1]]
        j0 = bisect.bisect_left(omap_keys, prev_foff - 64)
        j1 = bisect.bisect_left(omap_keys, k)
        n_interior_allocs = j1 - j0
    records.append(dict(
        asset_idx=sp[0], name=sp[1], root=sp[2], file_start=foff,
        sim_rt=sim_rt, real_rt=real_rt, err=err, delta_err=delta,
        gap_roots=[g[2] for g in gap_assets],
        gap_n_allocs=n_interior_allocs,
    ))
    prev_err, prev_foff = err, foff

# ---- outlier flagging ----
# isolated spike = big jump that immediately reverts (>95%) at the next point:
# the single measured real_rt is bogus/relocated, neighbors are on-trend.
errs = [r['err'] for r in records]
for i in range(1, len(records) - 1):
    d1 = errs[i] - errs[i - 1]
    d2 = errs[i + 1] - errs[i]
    if abs(d1) > 5000 and abs(d1 + d2) < abs(d1) * 0.05:
        records[i]['flag'] = 'spike_outlier'
for r in records:
    if 'flag' not in r:
        r['flag'] = ('big_step' if r['delta_err'] is not None
                     and abs(r['delta_err']) > 5000 else 'ok')

# ---- aggregation ----
# Attribute a gap's delta to its asset types. Clean attribution only when the
# gap contains a single asset type (possibly repeated).
import statistics
span_size = {s[3]: s[4] - s[3] for s in nz}
pure = collections.defaultdict(list)          # root -> list of dicts (clean only)
hist = collections.Counter()                  # coarse histogram, clean deltas
for i, r in enumerate(records):
    d = r['delta_err']
    if d is None or r['flag'] == 'spike_outlier':
        continue
    # skip deltas measured against an outlier predecessor
    if i and records[i - 1]['flag'] == 'spike_outlier':
        continue
    # coarse bucket
    if abs(d) <= 16:      b = str(d)
    elif abs(d) <= 256:   b = '17..256' if d > 0 else '-256..-17'
    elif abs(d) <= 5000:  b = '257..5000' if d > 0 else '-5000..-257'
    else:                 b = '>5000' if d > 0 else '<-5000'
    hist[b] += 1
    if len(set(r['gap_roots'])) == 1:
        pure[r['gap_roots'][0]].append(
            dict(d=d, n=len(r['gap_roots']),
                 size=span_size.get(r['file_start'], 0)))

per_type = {}
for root, lst in sorted(pure.items()):
    ds = [x['d'] for x in lst]
    small = [x['d'] for x in lst if abs(x['d']) < 5000]
    per_type[root] = dict(
        n_gaps=len(lst),
        total_pad=sum(ds),
        median=statistics.median(ds),
        n_big_steps=sum(1 for x in ds if abs(x) >= 5000),
        small_median=statistics.median(small) if small else None,
        small_q1_q3=([statistics.quantiles(small, n=4)[0],
                      statistics.quantiles(small, n=4)[2]]
                     if len(small) > 2 else None),
        frac_mult4=round(sum(1 for x in ds if x % 4 == 0) / len(ds), 2),
        frac_mult8=round(sum(1 for x in ds if x % 8 == 0) / len(ds), 2),
        frac_mult16=round(sum(1 for x in ds if x % 16 == 0) / len(ds), 2),
    )

total_drift = records[-1]['err'] - records[0]['err']
summary = dict(
    zone=ZONE, n_pairs=len(records),
    n_spike_outliers=sum(1 for r in records if r['flag'] == 'spike_outlier'),
    n_big_steps=sum(1 for r in records if r['flag'] == 'big_step'),
    first_err=records[0]['err'], last_err=records[-1]['err'],
    total_drift=total_drift,
    n_nonzero_deltas=sum(1 for r in records if r['delta_err']),
    pad_histogram_coarse={k: v for k, v in sorted(
        hist.items(), key=lambda kv: (isinstance(kv[0], str), str(kv[0])))},
    per_type_pure_gaps=per_type,
    notes=[
        'err = real_rt - sim_rt (b5 runtime offsets, stream_b5 = file_off-64 convention)',
        'delta_err = pad bytes real loader inserted (or removed) vs sim within gap since previous measured asset',
        'spike_outlier = single-point measurement anomaly (jump reverts at next point); exclude from fits',
        'big_step = persistent structural divergence >5000B inside one asset (sub-buffer modeled wrong / allocated out of order), not an alignment pad',
    ],
)

with open(OUT, 'w') as f:
    json.dump(dict(summary=summary, records=records), f, indent=1)

print(json.dumps(summary, indent=1)[:6000])
print('wrote', OUT, 'records:', len(records))
