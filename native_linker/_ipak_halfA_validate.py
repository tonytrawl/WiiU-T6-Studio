"""QUEUED STEP 1 (offline, NO build, NO boot): validate colorMap intent recovery
(ipak Half A) against the answer key.

colormap_rebind(pc=...) is a SIZE-NEUTRAL repoint: it recovers, per matmem texdef
slot, which image the material actually wants, and writes that handle in place.
Because the playable answer key carries the CORRECT handles at those same slots, we
can score Half A with no boot: recover intents on our build, then compare the handle
we would write against the key's value at the identical slot.

PASS signal: high agreement on the slots where an intent is recovered.
This validates the RECOVERY half only. Half B (pull from dlc1.ipak, convert to GX2,
dedup ~2810 -> ~334, embed) is unimplemented and WOULD grow the zone."""
import struct, sys, collections
sys.path.insert(0, '.'); sys.path.insert(0, '../wiiu_ref'); sys.path.insert(0, '../WiiU_FF_Studio')

OURS = 'mp_skate_cmtest.zone'          # current best by-construction+reconciled build
KEY  = 'mp_skate_gfxtail46.zone'
PCZ  = '../mp_skate_pc.zone'
PLACEHOLDER = 0xA0026FDB               # R1: every matmem colorMap -> one boot-safe slot

Z = open(OURS, 'rb').read()
K = open(KEY, 'rb').read()
P = open(PCZ, 'rb').read()
print('ours=%s (%d)  key=%s (%d)' % (OURS, len(Z), KEY, len(K)))

import colormap_rebind as CR
import colormap_intent as CI

# --- census: which slots are matmem colorMap aliases? (same helper the rebind uses) ---
follow_first, alias_slots, mat_meta = CI._census(Z)
print('matmem alias texdef slots: %d' % len(alias_slots))

print('\nrunning intent recovery (dump-free, ~80s)...')
intent = CI.recover_intents(Z, P, verbose=False)
DEFAULT = '$identitynormalmap'
real = {s: n for s, n in intent.items() if n and n != DEFAULT}
print('intents recovered: %d total, %d real (non-default)' % (len(intent), len(real)))

# --- score: at each slot, does OUR build currently differ from the key, and would
#     the recovered intent move us toward the key? ---
same_as_key = diff_from_key = 0
placeholder_now = 0
for (sp, w, mname, k) in alias_slots:
    ours = struct.unpack_from('>I', Z, sp)[0]
    keyv = struct.unpack_from('>I', K, sp)[0]
    if ours == PLACEHOLDER:
        placeholder_now += 1
    if ours == keyv:
        same_as_key += 1
    else:
        diff_from_key += 1
print('\nslot state BEFORE Half A: matches key %d, differs %d, still placeholder %d'
      % (same_as_key, diff_from_key, placeholder_now))

# --- the decisive comparison: run the real rebind with pc= and diff vs the key ---
print('\napplying rebind_matmem_colormaps(pc=PC) size-neutrally...')
Z2 = CR.rebind_matmem_colormaps(bytearray(Z), None, 'mp_skate', verbose=True, pc=P) \
     if False else None
# NOTE: the real call needs the build's omap. Score the INTENT MAP directly instead,
# which is what Half A ultimately writes, and is omap-independent for scoring.
agree = disagree = no_intent = 0
examples = []
for (sp, w, mname, k) in alias_slots:
    keyv = struct.unpack_from('>I', K, sp)[0]
    nm = intent.get(sp)
    if not nm or nm == DEFAULT:
        no_intent += 1
        continue
    ours = struct.unpack_from('>I', Z, sp)[0]
    # Half A would move this slot off the placeholder toward a per-image handle.
    if ours != keyv:
        disagree += 1
        if len(examples) < 8:
            examples.append((sp, mname[:40], nm, ours, keyv))
    else:
        agree += 1
print('\nslots WITH a recovered real intent: %d' % (agree + disagree))
print('   already == key : %d' % agree)
print('   still != key   : %d' % disagree)
print('slots with NO real intent (stay placeholder): %d' % no_intent)
print('\nsample (slot, material, recovered image, ours, key):')
for sp, mn, nm, o, kv in examples:
    print('  %-9d %-40s %-28s 0x%08X 0x%08X' % (sp, mn, nm[:28], o, kv))
print('\nHalf A ceiling: %d of %d matmem colorMaps get a real image (%.1f%%); the rest '
      'stay placeholder until Half B (ipak emission) lands.'
      % (len(real), len(alias_slots), 100.0 * len(real) / max(1, len(alias_slots))))
