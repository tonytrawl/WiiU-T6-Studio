"""core.gsc_lang -- lexer and parser for the T6 GSC/CSC dialect.

Produces an AST for `core.gsc_codegen`. Deliberately separate from code generation so the
front-end can be tested on its own against real decompiled sources.

DIALECT NOTES (T6 specifically -- it is not "just C")
-----------------------------------------------------
  * `#include maps\\mp\\_utility;`  -- backslash path, no quotes, semicolon-terminated.
  * `#using_animtree("generic_human");`
  * namespaced call    `maps\\mp\\_utility::func(a, b)`
  * method call        `player giveWeapon("x")`   -- juxtaposition, no dot
  * thread call        `thread f()`, `player thread f()`
  * function pointer   `::func` or `maps\\mp\\x::func`
  * pointer call       `[[f]](args)`, `obj [[f]]->method(args)` style
  * dev block          `/# ... #/`  -- contents compile only in dev builds
  * istring literal    `&"MENU_TEXT"`
  * vector literal     `(0, 0, 1)`  -- disambiguated from parens by comma at depth 1
  * `wait 0.05;` is a statement keyword, not a call.
  * animation ref      `%anim_name`

Identifiers are case-insensitive in T6; they are lowercased at lex time so that
`SetDvar` and `setdvar` intern to one string, which is what the engine does.
"""
import re


class GscSyntaxError(Exception):
    def __init__(self, msg, line, col, text=''):
        super().__init__('line %d col %d: %s%s' % (line, col, msg,
                                                   ('\n    ' + text) if text else ''))
        self.line, self.col = line, col


# ---------------------------------------------------------------------------- lexer

KEYWORDS = {
    'if', 'else', 'while', 'do', 'for', 'foreach', 'in', 'switch', 'case', 'default',
    'break', 'continue', 'return', 'wait', 'waittill', 'waittillframeend',
    'thread', 'true', 'false', 'undefined', 'self', 'level', 'game', 'anim',
    'endon', 'notify', 'waittillmatch',
}

# Longest first so '<<=' beats '<<' beats '<'.
# ⚠ '[[' and ']]' are NOT lexed as single tokens. They would be ambiguous with nested
# subscripts: `colors[ent.v["type"]][idx]` ends an inner index with `]` immediately followed by
# another `]`, which a greedy ']]' rule swallows as a pointer-call bracket. Measured: that alone
# broke 60 of 1469 reference sources. The parser instead detects two ADJACENT '[' / ']' tokens.
PUNCT = sorted([
    '<<=', '>>=', '...', '::', '++', '--', '&&', '||', '==', '!=', '<=', '>=',
    '+=', '-=', '*=', '/=', '%=', '|=', '&=', '^=', '<<', '>>',
    '{', '}', '(', ')', '[', ']', ';', ',', '.', '+', '-', '*', '/', '%',
    '=', '<', '>', '!', '~', '|', '&', '^', '?', ':', '#', '\\',
], key=len, reverse=True)

NUM_RE = re.compile(r'(?:0[xX][0-9a-fA-F]+)|(?:\d+\.\d*(?:[eE][+-]?\d+)?)|(?:\.\d+)|(?:\d+)')
ID_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')


class Tok(object):
    __slots__ = ('kind', 'val', 'line', 'col')

    def __init__(self, kind, val, line, col):
        self.kind, self.val, self.line, self.col = kind, val, line, col

    def __repr__(self):
        return '%s(%r)@%d:%d' % (self.kind, self.val, self.line, self.col)


def lex(src):
    """Return a list of Tok. Kinds: id, kw, num, str, istr, anim, punct, eof."""
    toks = []
    i, line, col = 0, 1, 1
    n = len(src)

    def adv(k):
        nonlocal i, line, col
        for c in src[i:i + k]:
            if c == '\n':
                line += 1
                col = 1
            else:
                col += 1
        i += k

    while i < n:
        c = src[i]
        if c in ' \t\r\n':
            adv(1)
            continue
        # comments -- but /# ... #/ is a DEV BLOCK, not a comment
        if src.startswith('/#', i):
            toks.append(Tok('punct', '/#', line, col))
            adv(2)
            continue
        if src.startswith('#/', i):
            toks.append(Tok('punct', '#/', line, col))
            adv(2)
            continue
        if src.startswith('//', i):
            j = src.find('\n', i)
            adv((n if j < 0 else j) - i)
            continue
        if src.startswith('/*', i):
            j = src.find('*/', i + 2)
            if j < 0:
                raise GscSyntaxError('unterminated /* comment', line, col)
            adv(j + 2 - i)
            continue
        # istring &"..."
        if c == '&' and i + 1 < n and src[i + 1] == '"':
            sl, sc = line, col
            adv(1)
            s, k = _string(src, i, line, col)
            toks.append(Tok('istr', s, sl, sc))
            adv(k)
            continue
        if c == '"':
            sl, sc = line, col
            s, k = _string(src, i, line, col)
            toks.append(Tok('str', s, sl, sc))
            adv(k)
            continue
        # animation reference %name
        if c == '%':
            m = ID_RE.match(src, i + 1)
            if m:
                toks.append(Tok('anim', m.group(0).lower(), line, col))
                adv(1 + len(m.group(0)))
                continue
        m = NUM_RE.match(src, i)
        if m and (c.isdigit() or (c == '.' and i + 1 < n and src[i + 1].isdigit())):
            toks.append(Tok('num', m.group(0), line, col))
            adv(len(m.group(0)))
            continue
        m = ID_RE.match(src, i)
        if m:
            w = m.group(0)
            lw = w.lower()
            toks.append(Tok('kw' if lw in KEYWORDS else 'id', lw, line, col))
            adv(len(w))
            continue
        for p in PUNCT:
            if src.startswith(p, i):
                toks.append(Tok('punct', p, line, col))
                adv(len(p))
                break
        else:
            raise GscSyntaxError('unexpected character %r' % c, line, col)
    toks.append(Tok('eof', None, line, col))
    return toks


def _string(src, i, line, col):
    """Parse a double-quoted string starting at src[i] == '"'. Returns (value, consumed)."""
    j = i + 1
    out = []
    while j < len(src):
        c = src[j]
        if c == '\\':
            if j + 1 >= len(src):
                break
            e = src[j + 1]
            out.append({'n': '\n', 't': '\t', 'r': '\r', '"': '"',
                        '\\': '\\', '0': '\0'}.get(e, e))
            j += 2
            continue
        if c == '"':
            return ''.join(out), j + 1 - i
        if c == '\n':
            break
        out.append(c)
        j += 1
    raise GscSyntaxError('unterminated string', line, col)


# ---------------------------------------------------------------------------- AST

class Node(object):
    __slots__ = ('kind', 'a', 'b', 'c', 'd', 'line')

    def __init__(self, kind, a=None, b=None, c=None, d=None, line=0):
        self.kind, self.a, self.b, self.c, self.d, self.line = kind, a, b, c, d, line

    def __repr__(self):
        bits = [repr(x) for x in (self.a, self.b, self.c, self.d) if x is not None]
        return '%s(%s)' % (self.kind, ', '.join(bits))


class Script(object):
    def __init__(self):
        self.includes = []          # ['maps/mp/_utility', ...]
        self.animtrees = []         # ['generic_human', ...]
        self.functions = []         # Node('func', name, params, body)


# ---------------------------------------------------------------------------- parser

ASSIGN_OPS = {'=', '+=', '-=', '*=', '/=', '%=', '|=', '&=', '^=', '<<=', '>>='}

# (precedence, right_assoc)
BINOPS = {
    '||': (1, False), '&&': (2, False),
    '|': (3, False), '^': (4, False), '&': (5, False),
    '==': (6, False), '!=': (6, False),
    '<': (7, False), '>': (7, False), '<=': (7, False), '>=': (7, False),
    '<<': (8, False), '>>': (8, False),
    '+': (9, False), '-': (9, False),
    '*': (10, False), '/': (10, False), '%': (10, False),
}


class Parser(object):
    def __init__(self, toks, path='<source>'):
        self.t = toks
        self.i = 0
        self.path = path

    # -- helpers ----------------------------------------------------------
    @property
    def cur(self):
        return self.t[self.i]

    def peek(self, k=0):
        j = min(self.i + k, len(self.t) - 1)
        return self.t[j]

    def at(self, kind, val=None):
        c = self.cur
        return c.kind == kind and (val is None or c.val == val)

    def atv(self, val):
        return self.cur.val == val

    def eat(self, kind=None, val=None):
        c = self.cur
        if kind and c.kind != kind:
            self.err('expected %s, got %s %r' % (kind, c.kind, c.val))
        if val is not None and c.val != val:
            self.err('expected %r, got %r' % (val, c.val))
        self.i += 1
        return c

    def accept(self, val):
        if self.cur.val == val:
            self.i += 1
            return True
        return False

    def err(self, msg):
        c = self.cur
        raise GscSyntaxError(msg, c.line, c.col)

    # -- top level --------------------------------------------------------
    def parse(self):
        s = Script()
        while not self.at('eof'):
            if self.atv('#'):
                self.directive(s)
                continue
            s.functions.append(self.function())
        return s

    def directive(self, s):
        self.eat('punct', '#')
        w = self.eat().val
        if w == 'include':
            s.includes.append(self.path_ref())
            self.accept(';')
        elif w in ('using_animtree', 'using'):
            self.eat('punct', '(')
            s.animtrees.append(self.eat('str').val)
            self.eat('punct', ')')
            self.accept(';')
        elif w in ('insert', 'define', 'namespace', 'precache'):
            # tolerated and skipped to end of line/semicolon
            while not self.at('eof') and not self.atv(';'):
                self.i += 1
            self.accept(';')
        else:
            self.err('unknown directive #%s' % w)

    # -- bracket helpers ---------------------------------------------------
    def at_dbl(self, ch):
        """Two ADJACENT '[' or ']' tokens, i.e. a pointer-call bracket."""
        return self.cur.val == ch and self.peek(1).val == ch

    def eat_dbl(self, ch):
        self.eat('punct', ch)
        self.eat('punct', ch)

    def path_ref(self):
        """maps\\mp\\_utility  ->  'maps/mp/_utility'

        ⚠ ONLY backslash separates namespace components. Accepting '/' as well made the parser
        read `deathanimduration / 1000` as a path and demand an identifier after the slash --
        i.e. plain division stopped parsing. That was 30+ of the 1469 reference sources.
        """
        parts = [self.eat('id').val]
        while self.atv('\\'):
            self.i += 1
            parts.append(self.eat('id').val)
        return '/'.join(parts)

    def function(self):
        ln = self.cur.line
        name = self.eat('id').val
        self.eat('punct', '(')
        params = []
        if not self.atv(')'):
            while True:
                pn = self.eat('id').val
                # T6 allows a default value in the DEFINITION:
                #   destructible_barrel_explosion(attacker, physics_explosion = 1)
                dflt = self.expr() if self.accept('=') else None
                params.append(pn if dflt is None else (pn, dflt))
                if not self.accept(','):
                    break
        self.eat('punct', ')')
        body = self.block()
        return Node('func', name, params, body, line=ln)

    def block(self):
        self.eat('punct', '{')
        stmts = []
        while not self.atv('}'):
            if self.at('eof'):
                self.err('unterminated block')
            stmts.append(self.statement())
        self.eat('punct', '}')
        return Node('block', stmts)

    # -- statements -------------------------------------------------------
    def statement(self):
        c = self.cur
        ln = c.line
        if c.val == '{':
            return self.block()
        if c.val == ';':
            self.i += 1
            return Node('empty')
        if c.val == '/#':
            self.i += 1
            stmts = []
            while not self.atv('#/'):
                if self.at('eof'):
                    self.err('unterminated dev block /#')
                stmts.append(self.statement())
            self.eat('punct', '#/')
            return Node('devblock', Node('block', stmts), line=ln)
        if c.kind == 'kw':
            if c.val == 'if':
                return self.if_stmt()
            if c.val == 'while':
                return self.while_stmt()
            if c.val == 'do':
                return self.do_stmt()
            if c.val == 'for':
                return self.for_stmt()
            if c.val == 'foreach':
                return self.foreach_stmt()
            if c.val == 'switch':
                return self.switch_stmt()
            if c.val == 'return':
                self.i += 1
                e = None if self.atv(';') else self.expr()
                self.eat('punct', ';')
                return Node('return', e, line=ln)
            if c.val in ('break', 'continue'):
                self.i += 1
                self.eat('punct', ';')
                return Node(c.val, line=ln)
            if c.val == 'wait':
                self.i += 1
                e = self.expr()
                self.eat('punct', ';')
                return Node('wait', e, line=ln)
            if c.val == 'waittillframeend':
                self.i += 1
                self.eat('punct', ';')
                return Node('waittillframeend', line=ln)
        e = self.expr()
        self.eat('punct', ';')
        return Node('exprstmt', e, line=ln)

    def if_stmt(self):
        ln = self.eat('kw', 'if').line
        self.eat('punct', '(')
        cond = self.expr()
        self.eat('punct', ')')
        then = self.statement()
        els = None
        if self.cur.kind == 'kw' and self.cur.val == 'else':
            self.i += 1
            els = self.statement()
        return Node('if', cond, then, els, line=ln)

    def while_stmt(self):
        ln = self.eat('kw', 'while').line
        self.eat('punct', '(')
        cond = self.expr()
        self.eat('punct', ')')
        return Node('while', cond, self.statement(), line=ln)

    def do_stmt(self):
        """do { ... } while (cond);  -- body runs once before the first test."""
        ln = self.eat('kw', 'do').line
        body = self.statement()
        self.eat('kw', 'while')
        self.eat('punct', '(')
        cond = self.expr()
        self.eat('punct', ')')
        self.eat('punct', ';')
        return Node('dowhile', cond, body, line=ln)

    def for_stmt(self):
        ln = self.eat('kw', 'for').line
        self.eat('punct', '(')
        init = None if self.atv(';') else Node('exprstmt', self.expr())
        self.eat('punct', ';')
        cond = None if self.atv(';') else self.expr()
        self.eat('punct', ';')
        step = None if self.atv(')') else Node('exprstmt', self.expr())
        self.eat('punct', ')')
        return Node('for', init, cond, step, (self.statement(),), line=ln)

    def foreach_stmt(self):
        ln = self.eat('kw', 'foreach').line
        self.eat('punct', '(')
        v1 = self.eat('id').val
        v2 = None
        if self.accept(','):
            v2 = self.eat('id').val
        self.eat('kw', 'in')
        coll = self.expr()
        self.eat('punct', ')')
        # foreach(k, v in c) -> key=v1 value=v2 ; foreach(v in c) -> value=v1
        key, val = (v1, v2) if v2 else (None, v1)
        return Node('foreach', key, val, coll, (self.statement(),), line=ln)

    def switch_stmt(self):
        ln = self.eat('kw', 'switch').line
        self.eat('punct', '(')
        subj = self.expr()
        self.eat('punct', ')')
        self.eat('punct', '{')
        cases = []                     # (value_or_None_for_default, [stmts])
        while not self.atv('}'):
            if self.at('eof'):
                self.err('unterminated switch')
            if self.cur.kind == 'kw' and self.cur.val == 'case':
                self.i += 1
                v = self.expr()
                self.eat('punct', ':')
                cases.append([v, []])
            elif self.cur.kind == 'kw' and self.cur.val == 'default':
                self.i += 1
                self.eat('punct', ':')
                cases.append([None, []])
            else:
                if not cases:
                    self.err('statement before first case in switch')
                cases[-1][1].append(self.statement())
        self.eat('punct', '}')
        return Node('switch', subj, cases, line=ln)

    # -- expressions ------------------------------------------------------
    def expr(self):
        return self.assignment()

    def assignment(self):
        left = self.ternary()
        if self.cur.kind == 'punct' and self.cur.val in ASSIGN_OPS:
            op = self.eat().val
            right = self.assignment()
            return Node('assign', op, left, right, line=left.line)
        return left

    def ternary(self):
        c = self.binary(0)
        if self.atv('?'):
            self.i += 1
            a = self.assignment()
            self.eat('punct', ':')
            b = self.assignment()
            return Node('ternary', c, a, b, line=c.line)
        return c

    def binary(self, minprec):
        left = self.unary()
        while True:
            c = self.cur
            if c.kind != 'punct' or c.val not in BINOPS:
                return left
            prec, right_assoc = BINOPS[c.val]
            if prec < minprec:
                return left
            op = self.eat().val
            right = self.binary(prec if right_assoc else prec + 1)
            left = Node('bin', op, left, right, line=left.line)

    def unary(self):
        c = self.cur
        if c.kind == 'punct' and c.val in ('!', '~', '-', '+', '++', '--'):
            self.i += 1
            e = self.unary()
            if c.val == '+':
                return e
            if c.val in ('++', '--'):
                return Node('preincdec', c.val, e, line=c.line)
            return Node('unary', c.val, e, line=c.line)
        if c.kind == 'kw' and c.val == 'thread':
            self.i += 1
            call = self.unary()
            return Node('thread', call, line=c.line)
        return self.postfix()

    def postfix(self):
        e = self.primary()
        while True:
            c = self.cur
            if c.val == '.':
                self.i += 1
                if self.at('num'):                      # obj.0 style index (rare)
                    e = Node('index', e, Node('num', self.eat().val), line=c.line)
                else:
                    e = Node('field', e, self.eat('id').val, line=c.line)
            elif c.val == '[' and not self.at_dbl('['):
                self.i += 1
                idx = self.expr()
                self.eat('punct', ']')
                e = Node('index', e, idx, line=c.line)
            elif self.at_dbl('['):
                # `e [[ p ]](args)` -- deref call with `e` as the method target
                e = self._call_after_target(e)
            elif c.val == '(':
                e = Node('call', e, self.arglist(), line=c.line)
            elif c.val in ('++', '--'):
                self.i += 1
                e = Node('postincdec', c.val, e, line=c.line)
            elif c.val == '::':
                # ns::name  -- e must be a path built from ids
                self.i += 1
                nm = self.eat('id').val
                e = Node('nsref', self._as_path(e), nm, line=c.line)
            elif c.val == '\\':
                # continue a namespace path: maps\mp\_utility::f
                # ⚠ BACKSLASH ONLY. Treating '/' as a separator too made `x / 1000` parse as a
                # path and demand an identifier after the slash -- plain division stopped
                # working, in 41 of the 1469 reference sources.
                self.i += 1
                nxt = self.eat('id').val
                e = Node('path', self._as_path(e) + '/' + nxt, line=c.line)
            elif c.kind in ('id', 'kw') and self._starts_method_call():
                # juxtaposition method call:  player giveWeapon("x")
                target = e
                e = self._call_after_target(target)
            else:
                return e

    def _as_path(self, node):
        if node.kind == 'path':
            return node.a
        if node.kind == 'name':
            return node.a
        raise GscSyntaxError('expected a namespace path', node.line, 0)

    def _starts_method_call(self):
        """`obj f(...)`, `obj thread f(...)`, `obj ns::f(...)`, `obj [[p]](...)`"""
        c = self.cur
        if c.kind == 'kw' and c.val == 'thread':
            return True
        if c.kind == 'kw' and c.val in ('waittill', 'notify', 'endon', 'waittillmatch'):
            return True
        if self.at_dbl('['):                     # self [[ level.fn ]]()
            return True
        if c.kind != 'id':
            return False
        # look ahead for '(' or '::' or a path continuation
        j = self.i + 1
        depth = 0
        while j < len(self.t):
            v = self.t[j].val
            if v == '\\' and self.t[j + 1].kind == 'id':
                j += 2
                continue
            if v == '::':
                return True
            if v == '(':
                return True
            return False
        return False

    def _call_after_target(self, target):
        ln = self.cur.line
        threaded = False
        if self.cur.kind == 'kw' and self.cur.val == 'thread':
            self.i += 1
            threaded = True
        if self.cur.kind == 'kw' and self.cur.val in ('waittill', 'notify', 'endon',
                                                      'waittillmatch'):
            kw = self.eat().val
            args = self.arglist()
            return Node('builtincall', kw, args, target, line=ln)
        callee = self.primary()
        while self.cur.val in ('\\', '::'):
            c = self.cur
            self.i += 1
            if c.val == '::':
                nm = self.eat('id').val
                callee = Node('nsref', self._as_path(callee), nm, line=c.line)
            else:
                callee = Node('path', self._as_path(callee) + '/' + self.eat('id').val,
                              line=c.line)
        args = self.arglist()
        n = Node('call', callee, args, target, line=ln)
        n.d = 'thread' if threaded else 'method'
        return n

    def arglist(self):
        self.eat('punct', '(')
        args = []
        if not self.atv(')'):
            while True:
                args.append(self.expr())
                if not self.accept(','):
                    break
        self.eat('punct', ')')
        return args

    def primary(self):
        c = self.cur
        ln = c.line
        if c.kind == 'num':
            self.i += 1
            return Node('num', c.val, line=ln)
        if c.kind == 'str':
            self.i += 1
            return Node('str', c.val, line=ln)
        if c.kind == 'istr':
            self.i += 1
            return Node('istr', c.val, line=ln)
        if c.kind == 'anim':
            self.i += 1
            return Node('anim', c.val, line=ln)
        if c.kind == 'kw':
            if c.val in ('true', 'false'):
                self.i += 1
                return Node('bool', c.val == 'true', line=ln)
            if c.val == 'undefined':
                self.i += 1
                return Node('undefined', line=ln)
            if c.val in ('self', 'level', 'game', 'anim'):
                self.i += 1
                return Node('special', c.val, line=ln)
            if c.val in ('waittill', 'notify', 'endon', 'waittillmatch'):
                self.i += 1
                args = self.arglist()
                return Node('builtincall', c.val, args, None, line=ln)
        if c.val == '#':
            # `shield_ent useanimtree( #animtree );` -- a bare animtree reference, which is an
            # EXPRESSION here, not a directive.
            self.i += 1
            w = self.eat().val
            if w != 'animtree':
                self.err('unexpected #%s in an expression' % w)
            return Node('animtreeref', line=ln)
        if c.val == '::':
            self.i += 1
            return Node('funcref', '', self.eat('id').val, line=ln)
        if self.at_dbl('['):
            self.eat_dbl('[')
            e = self.expr()
            self.eat_dbl(']')
            return Node('deref', e, line=ln)
        if c.val == '[':
            # empty array literal []
            self.i += 1
            self.eat('punct', ']')
            return Node('emptyarray', line=ln)
        if c.val == '(':
            self.i += 1
            first = self.expr()
            if self.atv(','):
                comps = [first]
                while self.accept(','):
                    comps.append(self.expr())
                self.eat('punct', ')')
                if len(comps) != 3:
                    self.err('vector literal needs exactly 3 components, got %d' % len(comps))
                return Node('vector', comps, line=ln)
            self.eat('punct', ')')
            return first
        if c.kind == 'id':
            self.i += 1
            return Node('name', c.val, line=ln)
        self.err('unexpected %s %r' % (c.kind, c.val))


def parse(src, path='<source>'):
    return Parser(lex(src), path).parse()
