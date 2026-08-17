#!/usr/bin/env python3
"""
streamInfo synthesis (Track F, Bucket D): the console-only GfxWorldStreamInfo
aabbTrees + leafRefs (~77 KB on raid). REGISTERED SYNTHESIS, not a byte-exact
conversion — the genuine builder's clustering isn't PC-derivable.

Genuine layout (REPINNED 2026-07-14 vs Load_GfxWorld disasm @0x22239c4 + genuine
mp_raid; supersedes the 2026-07-10 pin):
  region = treeCount x 48B nodes + leafRefCount x u32 leafRefs, NOTHING else.
  The old "16 bytes 0xFF prefix" was WRONG: those FF words are the last 16
  bytes of the 1096-byte GfxWorld STRUCT itself (4 MaterialVertexShader
  handles @1076..1092; the probe's 1076 bodysize under-reads the struct by
  20). Emitting the prefix here fed the handles from the wrong stream spot
  and shifted every node/ref by -4 vs the loader.
  Node 48B (BE), phase pinned by the loader (trees start at struct+1096;
  fi/ic/fc/cc quad partition verified at node+36 on genuine raid):
  {vec3 mins, u32 0, vec3 maxs, u32 0, f32 streamDist2,
  u16 firstItem, u16 itemCount, u16 firstChild, u16 childCount, u32 tail}.
  `tail` is a real field (nonzero on 588/785 genuine raid nodes) but 0 on the
  root and 197 others — synthesized as 0. Leaf item ranges PARTITION
  [0, leafRefCount); interior nodes carry their subtree's (firstItem,
  itemCount); root's children are contiguous (firstChild, childCount) —
  genuine trees are N-ary (2..9 children).
  leafRefs = static-model indices into dpvs.smodelInsts (genuine has a few
  0x80000-flagged entries and duplicates across leaves; the streaming system
  uses the tree to pick which smodel textures to stream by camera distance).

Synthesis: median-split KD build over smodel bounds centers (bounds from the
PC dpvs.smodelInsts region: 36B GfxStaticModelInst = mins vec3 + maxs vec3 +
lightingOrigin vec3), leaf <= LEAF_MAX items, node bounds = union of member
bounds, streamDist2 = a conservative constant (max observed genuine ~5.8e7).
Structurally valid by the same invariants the genuine trees satisfy.
"""
import struct

LEAF_MAX = 16
STREAM_DIST2 = 6.0e7


def synth_streaminfo(pc, sminst_off, smodel_count):
    """Build console streamInfo from the PC smodelInsts region.
    Returns (region_bytes, tree_count, leafref_count)."""
    insts = []
    for i in range(smodel_count):
        o = sminst_off + i * 36
        mn = struct.unpack_from('<3f', pc, o)
        mx = struct.unpack_from('<3f', pc, o + 12)
        insts.append((mn, mx))

    nodes = []      # (mins, maxs, firstItem, itemCount, firstChild, childCount)
    items = []      # leafRefs in leaf order

    def bounds(idxs):
        mn = [min(insts[i][0][k] for i in idxs) for k in range(3)]
        mx = [max(insts[i][1][k] for i in idxs) for k in range(3)]
        return mn, mx

    # DFS with contiguous child-block allocation: children are allocated as a
    # block (so firstChild/childCount work) and recursed in order (so every
    # subtree's items form a contiguous range — the genuine invariant).
    import sys as _s
    _s.setrecursionlimit(10000)
    nodes.append(None)                       # root placeholder

    def build(ni, idxs):
        mn, mx = bounds(idxs)
        if len(idxs) <= LEAF_MAX:
            fi = len(items)
            items.extend(idxs)
            nodes[ni] = (mn, mx, fi, len(idxs), 0, 0)
            return
        axis = max(range(3), key=lambda k: mx[k] - mn[k])
        s = sorted(idxs, key=lambda i: insts[i][0][axis] + insts[i][1][axis])
        half = len(s) // 2
        parts = [s[:half], s[half:]]
        fc = len(nodes)
        for _ in parts:
            nodes.append(None)
        fi = len(items)
        for j, p in enumerate(parts):
            build(fc + j, p)
        nodes[ni] = (mn, mx, fi, len(idxs), fc, len(parts))
    build(0, list(range(smodel_count)))

    out = bytearray()
    for mn, mx, fi, ic, fc, cc in nodes:
        out += struct.pack('>3fI', *(list(mn) + [0]))
        out += struct.pack('>3fI', *(list(mx) + [0]))
        # rule (I), baked 2026-07-30: the node tail is NOT one u32 — it is the u16 pair
        # smodelCount(+44) / surfaceCount(+46) (that is why "tail" was nonzero on 588/785
        # genuine nodes). Genuine invariants, now satisfied BY CONSTRUCTION:
        #   leaf(+44) + leaf(+46) == leaf itemCount(+38)   (leaf refs = [smodels..., surfaces...])
        #   sum(leaf +44) == dpvs.smodelCount              (items partition the smodel set)
        #   interior nodes carry 0/0.
        # Shipping zeros here was the suspected cause of "blank"/low-res models (the
        # texture-streaming distance system reads these counts). ⚠ STILL OWED: world
        # SURFACES are absent from the tree entirely (surfaceCount is honestly 0 until the
        # KD build ingests PC dpvs.surfaces bounds — rule (I) second half).
        sm = ic if cc == 0 else 0
        out += struct.pack('>f6H', STREAM_DIST2, fi, ic, fc, cc, sm, 0)
    for i in items:
        out += struct.pack('>I', i)
    return bytes(out), len(nodes), len(items)


def validate_shape(region, tree_count, leaf_count):
    """Re-check the genuine invariants on a synthesized region."""
    nodes = []
    for i in range(tree_count):
        o = i * 48
        fi, ic, fc, cc = struct.unpack_from('>4H', region, o + 36)
        nodes.append((fi, ic, fc, cc))
    leaves = [(fi, ic) for fi, ic, fc, cc in nodes if cc == 0]
    leaves.sort()
    ok = leaves[0][0] == 0 and all(leaves[i][0] + leaves[i][1] == leaves[i + 1][0]
                                   for i in range(len(leaves) - 1))
    ok = ok and sum(ic for _, ic in leaves) == leaf_count
    ok = ok and nodes[0][0] == 0 and nodes[0][1] == leaf_count
    return ok
