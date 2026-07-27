import sys, os
sys.path.insert(0,'.'); sys.path.insert(0,'../wiiu_ref'); sys.path.insert(0,'../WiiU_FF_Studio')
import alloc_events as AE, clipmap_console as CC
for p in ['mp_skate_final.zone','_p2_skate_ruled.zone']:
    d = open(p,'rb').read()
    try:
        end,_ = AE.clipmap_events(d, 84512493, '>', mat_span=CC._mat_span)
    except Exception as e:
        end = 'ERR %s' % e
    print('%-26s size=%d  clipmap_events end=%s  (must be 89584099)' % (p, len(d), end))
