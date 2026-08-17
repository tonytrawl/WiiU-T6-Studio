#!/usr/bin/env python3
"""STATIC PRE-BOOT CHECKER for a compiled T6 Wii U MP script.

Retail prints only `**** N script error(s)` and compiles the detail out, so a single bad
import costs a full boot cycle to find. The engine has exactly two counted error classes at
script-load time -- an import it cannot resolve, and an arity it cannot satisfy -- and both
are decidable statically against the scripts that actually ship in the loaded zones.

This re-creates the six checks from HANDOFF_survival_gamemode.md section 6:

  1. unresolved namespace imports   -- ns script not in the corpus, or fn not exported by it
  2. unknown builtins               -- name never used as a builtin anywhere in the MP corpus.
                                       THE ONE THAT BITES: `adsbuttonpressed` is a real T6
                                       builtin but ZM-only; the MP engine cannot resolve it and
                                       errors at level load even if the call never runs.
  3. builtins called with MORE params than any corpus call site
  4. dev-only builtins called outside a dev block (dev-only == every corpus occurrence has 0x10)
  5. script-fn arity: call-site params > the target export's declared params
  6. fixed-arity builtins called with an arity the corpus never uses -- FEWER IS ALSO FATAL.
     `setscoreboardcolumns` takes exactly 5 in all genuine scripts; passing 4 produced an
     invisible "1 script error" that cost ~15 boots.

Corpus = MP zones only (common_mp + patch_mp + optional MP map zones). Never mix ZM zones in:
that is precisely what would make check 2 pass on a ZM-only builtin.

Usage:
    python tools/gsc_preboot_check.py <compiled.gsc> [--corpus DIR ...] [--strict]

    --corpus may be repeated; defaults to the extracted common_mp + patch_mp SPT dirs written
    by tools/gsc_corpus_build.py.
"""
import sys, os, json, argparse, collections

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gsc_spt as G

# an import record is a CALL (not a `::fn` pointer) when a call-type bit is set
CALL_MASK = 0x06        # 0x02 global call | 0x04 method call
PTR_MASK = 0x01         # `::fn` function pointer reference
DEV_FLAG = 0x10         # call site sits inside /# ... #/


class Corpus:
    """An unqualified call (no `path::` prefix) is resolved by the engine in two different
    ways, and they need different checks:

      * the name is exported by a script the caller #includes  -> a SCRIPT call
      * the name is exported by nothing at all                 -> an ENGINE BUILTIN

    So the discriminator is simply "does any script in the corpus export this name". Lumping
    both into one bucket inflates the builtin count ~3.5x and, worse, lets an unqualified call
    to an include-resolved helper pass without ever checking that the caller includes the
    script that defines it."""

    def __init__(self):
        self.builtin_arities = collections.defaultdict(set)   # name -> {params, ...}
        self.builtin_devonly = {}                             # name -> bool (all sites dev)
        self.builtin_sites = collections.Counter()            # name -> #call sites
        self.exports = {}                                     # scriptpath -> {fn: params}
        self.defs = collections.defaultdict(dict)             # fn -> {scriptpath: params}
        self.scripts = {}

    def add_dir(self, root, kind='gsc'):
        loaded = G.load_dir(root)
        for rel, s in loaded.items():
            # keep .gsc and .csc separate: a CSC builtin is not callable from a GSC and
            # vice versa. `kind` selects which side this corpus dir contributes to.
            if kind == 'gsc' and not rel.endswith('.gsc'):
                continue
            if kind == 'csc' and not rel.endswith('.csc'):
                continue
            self.scripts[rel] = s
            self.exports[rel] = {e.name: e.params for e in s.exports}
            stem = os.path.splitext(rel)[0]
            for e in s.exports:
                self.defs[e.name][stem] = e.params
            for im in s.imports:
                if im.ns or not (im.flags & CALL_MASK):
                    continue
                self.builtin_arities[im.name].add(im.params)
                self.builtin_sites[im.name] += max(1, im.num_addr)
                dev = bool(im.flags & DEV_FLAG)
                if im.name in self.builtin_devonly:
                    self.builtin_devonly[im.name] = self.builtin_devonly[im.name] and dev
                else:
                    self.builtin_devonly[im.name] = dev
        return len(loaded)

    def finalize(self):
        """Drop every unqualified name that some script actually exports -- those are script
        calls, not builtins. What is left is the engine's own vocabulary as used by MP."""
        for name in list(self.builtin_arities):
            if name in self.defs:
                del self.builtin_arities[name]
        return self

    def save(self, path):
        json.dump({
            'builtin_arities': {k: sorted(v) for k, v in self.builtin_arities.items()},
            'builtin_devonly': self.builtin_devonly,
            'builtin_sites': dict(self.builtin_sites),
            'exports': self.exports,
            'defs': {k: v for k, v in self.defs.items()},
        }, open(path, 'w'), indent=0)

    @classmethod
    def load(cls, path):
        d = json.load(open(path))
        c = cls()
        c.builtin_arities = {k: set(v) for k, v in d['builtin_arities'].items()}
        c.builtin_devonly = d['builtin_devonly']
        c.builtin_sites = collections.Counter(d['builtin_sites'])
        c.exports = d['exports']
        c.defs = collections.defaultdict(dict, d['defs'])
        return c


def check(target, corpus, self_path=None, extra_exports=None):
    """Returns a list of (severity, code, message)."""
    out = []
    err = lambda c, m: out.append(('ERROR', c, m))
    warn = lambda c, m: out.append(('WARN', c, m))

    own = {e.name: e.params for e in target.exports}
    # a companion script we ship alongside (e.g. a library in another cut-mode slot)
    extra_exports = extra_exports or {}
    inc_stems = set(os.path.splitext(i)[0] for i in target.includes)

    def resolve_ns(ns):
        # Companions FIRST. We are overwriting cut-gametype script slots that still exist in
        # the retail corpus, so the stock res.gsc/bwars.gsc export tables must not shadow the
        # replacements we are actually shipping into those slots.
        for cand in (ns, ns + '.gsc', ns + '.csc'):
            if cand in extra_exports:
                return extra_exports[cand]
        for cand in (ns + '.gsc', ns + '.csc', ns):
            if cand in corpus.exports:
                return corpus.exports[cand]
        if self_path and ns in (self_path, os.path.splitext(self_path)[0]):
            return own
        return None

    for im in target.imports:
        # ---- `::fn` pointer references -------------------------------------------------
        if im.flags & PTR_MASK:
            if im.ns:
                tbl = resolve_ns(im.ns)
                if tbl is None:
                    err('E1-ptr-ns', '::%s::%s -- namespace not in corpus' % (im.ns, im.name))
                elif im.name not in tbl:
                    err('E1-ptr-fn', '::%s::%s -- not exported by that script' % (im.ns, im.name))
            elif im.name not in own:
                err('E1-ptr-self', '::%s -- no such function in this script '
                                   '(a `::name` typo is a load-time error)' % im.name)
            continue

        if not (im.flags & CALL_MASK):
            continue

        # ---- namespaced script call ----------------------------------------------------
        if im.ns:
            tbl = resolve_ns(im.ns)
            if tbl is None:
                err('E1-ns', '%s::%s() -- namespace script not present in any loaded zone'
                    % (im.ns, im.name))
            elif im.name not in tbl:
                err('E1-fn', '%s::%s() -- not exported by that script' % (im.ns, im.name))
            elif im.params > tbl[im.name]:
                err('E5-arity', '%s::%s() called with %d args, declares %d'
                    % (im.ns, im.name, im.params, tbl[im.name]))
            continue

        # ---- unqualified call: script function reached through #include, or a builtin ----
        if im.name in own:
            if im.params > own[im.name]:
                err('E5-arity', '%s() called with %d args, this script declares %d'
                    % (im.name, im.params, own[im.name]))
            continue

        defs = corpus.defs.get(im.name)
        if defs:
            # resolvable only if the target #includes a script that exports it
            vis = [s for s in defs if s in inc_stems]
            if not vis:
                err('E1-include', '%s() resolves to %s but this script #includes none of them'
                    % (im.name, ', '.join(sorted(defs)[:4]) + ('...' if len(defs) > 4 else '')))
            else:
                worst = min(defs[s] for s in vis)
                if im.params > worst:
                    err('E5-arity', '%s() called with %d args; %s declares %d'
                        % (im.name, im.params, vis[0], worst))
            continue

        arities = corpus.builtin_arities.get(im.name)
        if arities is None:
            err('E2-unknown', '%s() -- builtin never used anywhere in the MP corpus. '
                              'If it is ZM-only the MP engine cannot resolve it and the level '
                              'errors at load.' % im.name)
            continue

        if im.params > max(arities):
            err('E3-overmax', '%s() called with %d args; corpus max is %d %s'
                % (im.name, im.params, max(arities), sorted(arities)))
        elif im.params not in arities:
            # check 6 -- FEWER is just as fatal for a fixed-arity builtin
            sites = corpus.builtin_sites.get(im.name, 0)
            sev = err if len(arities) == 1 else warn
            sev('E6-arity', '%s() called with %d args; corpus only ever uses %s '
                            '(%d call sites). For a fixed-arity builtin, too FEW args is fatal.'
                % (im.name, im.params, sorted(arities), sites))

        if corpus.builtin_devonly.get(im.name) and not (im.flags & DEV_FLAG):
            # "dev-only" is an INFERENCE from where the corpus happens to call it, not a fact
            # about the engine's builtin table. With one or two call sites that inference is
            # weak -- setexpfog has exactly one, inside _art.gsc's dev block, and is a perfectly
            # real retail builtin. Grade it by how much evidence there actually is.
            sites = corpus.builtin_sites.get(im.name, 0)
            sev = err if sites >= 4 else warn
            sev('E4-devonly', '%s() -- all %d corpus call site(s) sit inside dev blocks. %s'
                % (im.name, sites,
                   'Strong evidence it is dev-only; a retail build cannot resolve it.' if sites >= 4
                   else 'Weak evidence (too few sites to conclude) -- prefer a non-dev alternative '
                        'if one exists, otherwise this needs a boot to settle.'))

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('target')
    ap.add_argument('--corpus', action='append', default=[])
    ap.add_argument('--db', default=None, help='prebuilt corpus json')
    ap.add_argument('--self-path', default=None,
                    help='the in-zone path the target will occupy, e.g. maps/mp/gametypes/bwars.gsc')
    ap.add_argument('--list-builtin', default=None, help='print what the corpus knows about a builtin')
    args = ap.parse_args()

    corpus = Corpus()
    if args.db and os.path.exists(args.db):
        corpus = Corpus.load(args.db)
    else:
        if not args.corpus:
            ap.error('need --corpus DIR (or --db)')
        for d in args.corpus:
            n = corpus.add_dir(d)
            print('corpus: %-60s %d scripts' % (d, n))
        if args.db:
            corpus.save(args.db)
    print('corpus knows %d builtins, %d scripts' % (len(corpus.builtin_arities), len(corpus.exports)))

    if args.list_builtin:
        name = args.list_builtin.lower()
        a = corpus.builtin_arities.get(name)
        if a is None:
            print('%s: NOT IN CORPUS' % name)
        else:
            print('%s: arities=%s sites=%d devonly=%s'
                  % (name, sorted(a), corpus.builtin_sites.get(name, 0), corpus.builtin_devonly.get(name)))
        return 0

    target = G.Script(args.target, open(args.target, 'rb').read())
    print('target: %s  (%d exports, %d imports, embedded name %r)'
          % (args.target, len(target.exports), len(target.imports), target.name))

    findings = check(target, corpus, self_path=args.self_path)
    errors = [f for f in findings if f[0] == 'ERROR']
    for sev, code, msg in findings:
        print('  %-5s %-12s %s' % (sev, code, msg))
    if not findings:
        print('  CLEAN -- no static script errors predicted')
    print('%d error(s), %d warning(s)' % (len(errors), len(findings) - len(errors)))
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
