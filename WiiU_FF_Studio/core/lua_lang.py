"""core.lua_lang -- Lua 5.1 lexer and parser (front half of the HavokScript compiler).

HavokScript's SOURCE language is Lua 5.1; the divergence is all in the bytecode. So this is a
straight Lua 5.1 front-end, kept separate from `core.hks_codegen` so it can be tested against
real game Lua on its own.

Covers: all statements (do/while/repeat/if/for-num/for-in/function/local function/local/assign/
call/return/break), full expression grammar with correct precedence and right-associative `..`
and `^`, table constructors, method calls, varargs, long strings/comments.
"""
import re

KEYWORDS = {
    'and', 'break', 'do', 'else', 'elseif', 'end', 'false', 'for', 'function',
    'if', 'in', 'local', 'nil', 'not', 'or', 'repeat', 'return', 'then', 'true',
    'until', 'while',
}

PUNCT = sorted([
    '...', '..', '==', '~=', '<=', '>=', '::',
    '+', '-', '*', '/', '%', '^', '#', '<', '>', '=', '(', ')', '{', '}',
    '[', ']', ';', ':', ',', '.',
], key=len, reverse=True)

NUM_RE = re.compile(r'0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?')
NAME_RE = re.compile(r'[A-Za-z_]\w*')


class LuaSyntaxError(Exception):
    def __init__(self, msg, line):
        super().__init__('line %d: %s' % (line, msg))
        self.line = line


class Tok(object):
    __slots__ = ('kind', 'val', 'line')

    def __init__(self, kind, val, line):
        self.kind, self.val, self.line = kind, val, line

    def __repr__(self):
        return '%s(%r)@%d' % (self.kind, self.val, self.line)


def _long_bracket(src, i, line):
    """[[ ... ]] / [=[ ... ]=]. Returns (text, new_i, new_line) or None."""
    m = re.compile(r'\[(=*)\[').match(src, i)
    if not m:
        return None
    close = ']' + '=' * len(m.group(1)) + ']'
    j = src.find(close, m.end())
    if j < 0:
        raise LuaSyntaxError('unterminated long bracket', line)
    body = src[m.end():j]
    if body.startswith('\n'):
        body = body[1:]
    return body, j + len(close), line + src.count('\n', i, j + len(close))


def lex(src):
    toks = []
    i, line, n = 0, 1, len(src)
    while i < n:
        c = src[i]
        if c == '\n':
            line += 1
            i += 1
            continue
        if c in ' \t\r':
            i += 1
            continue
        if src.startswith('--', i):
            lb = None
            if src.startswith('--[', i):
                try:
                    lb = _long_bracket(src, i + 2, line)
                except LuaSyntaxError:
                    lb = None
            if lb:
                _txt, i, line = lb
            else:
                j = src.find('\n', i)
                i = n if j < 0 else j
            continue
        if c == '[' and re.compile(r'\[=*\[').match(src, i):
            txt, i, line = _long_bracket(src, i, line)
            toks.append(Tok('str', txt, line))
            continue
        if c in '"\'':
            j, out = i + 1, []
            while j < n and src[j] != c:
                if src[j] == '\\':
                    e = src[j + 1]
                    out.append({'n': '\n', 't': '\t', 'r': '\r', 'a': '\a', 'b': '\b',
                                'f': '\f', 'v': '\v', '\\': '\\', '"': '"', "'": "'",
                                '\n': '\n'}.get(e, e))
                    j += 2
                    continue
                if src[j] == '\n':
                    raise LuaSyntaxError('unterminated string', line)
                out.append(src[j])
                j += 1
            if j >= n:
                raise LuaSyntaxError('unterminated string', line)
            toks.append(Tok('str', ''.join(out), line))
            i = j + 1
            continue
        m = NUM_RE.match(src, i)
        if m and (c.isdigit() or (c == '.' and i + 1 < n and src[i + 1].isdigit())):
            toks.append(Tok('num', m.group(0), line))
            i = m.end()
            continue
        m = NAME_RE.match(src, i)
        if m:
            w = m.group(0)
            toks.append(Tok('kw' if w in KEYWORDS else 'name', w, line))
            i = m.end()
            continue
        for p in PUNCT:
            if src.startswith(p, i):
                toks.append(Tok('punct', p, line))
                i += len(p)
                break
        else:
            raise LuaSyntaxError('unexpected character %r' % c, line)
    toks.append(Tok('eof', None, line))
    return toks


# ---------------------------------------------------------------------------- AST

class N(object):
    __slots__ = ('k', 'a', 'b', 'c', 'line')

    def __init__(self, k, a=None, b=None, c=None, line=0):
        self.k, self.a, self.b, self.c, self.line = k, a, b, c, line

    def __repr__(self):
        return '%s(%s)' % (self.k, ', '.join(repr(x) for x in (self.a, self.b, self.c)
                                             if x is not None))


# binary operator precedence: (left, right); right < left => right-associative
BINPRI = {
    'or': (1, 1), 'and': (2, 2),
    '<': (3, 3), '>': (3, 3), '<=': (3, 3), '>=': (3, 3), '~=': (3, 3), '==': (3, 3),
    '..': (9, 8),                      # right-assoc
    '+': (6, 6), '-': (6, 6),
    '*': (7, 7), '/': (7, 7), '%': (7, 7),
    '^': (10, 9),                      # right-assoc
}
UNARY_PRI = 8


class Parser(object):
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    @property
    def cur(self):
        return self.t[self.i]

    def peek(self, k=1):
        return self.t[min(self.i + k, len(self.t) - 1)]

    def at(self, val):
        return self.cur.val == val

    def accept(self, val):
        if self.cur.val == val:
            self.i += 1
            return True
        return False

    def expect(self, val):
        if not self.accept(val):
            raise LuaSyntaxError('expected %r, got %r' % (val, self.cur.val), self.cur.line)

    def name(self):
        if self.cur.kind != 'name':
            raise LuaSyntaxError('expected a name, got %r' % (self.cur.val,), self.cur.line)
        v = self.cur.val
        self.i += 1
        return v

    # -- entry ------------------------------------------------------------
    def chunk(self):
        b = self.block()
        if self.cur.kind != 'eof':
            raise LuaSyntaxError('unexpected %r' % (self.cur.val,), self.cur.line)
        return b

    BLOCK_END = {'end', 'else', 'elseif', 'until'}

    def block(self):
        stmts = []
        while True:
            c = self.cur
            if c.kind == 'eof' or (c.kind == 'kw' and c.val in self.BLOCK_END):
                break
            if c.kind == 'kw' and c.val == 'return':
                self.i += 1
                exprs = []
                if not (self.cur.kind == 'eof'
                        or (self.cur.kind == 'kw' and self.cur.val in self.BLOCK_END)
                        or self.at(';')):
                    exprs = self.exprlist()
                self.accept(';')
                stmts.append(N('return', exprs, line=c.line))
                break
            if c.kind == 'kw' and c.val == 'break':
                self.i += 1
                self.accept(';')
                stmts.append(N('break', line=c.line))
                break
            s = self.statement()
            if s is not None:
                stmts.append(s)
        return N('block', stmts)

    def statement(self):
        c = self.cur
        ln = c.line
        if self.accept(';'):
            return None
        if c.kind == 'kw':
            if c.val == 'do':
                self.i += 1
                b = self.block()
                self.expect('end')
                return N('do', b, line=ln)
            if c.val == 'while':
                self.i += 1
                cond = self.expr()
                self.expect('do')
                b = self.block()
                self.expect('end')
                return N('while', cond, b, line=ln)
            if c.val == 'repeat':
                self.i += 1
                b = self.block()
                self.expect('until')
                return N('repeat', b, self.expr(), line=ln)
            if c.val == 'if':
                self.i += 1
                arms = []
                cond = self.expr()
                self.expect('then')
                arms.append((cond, self.block()))
                while self.at('elseif'):
                    self.i += 1
                    cnd = self.expr()
                    self.expect('then')
                    arms.append((cnd, self.block()))
                els = None
                if self.accept('else'):
                    els = self.block()
                self.expect('end')
                return N('if', arms, els, line=ln)
            if c.val == 'for':
                self.i += 1
                n1 = self.name()
                if self.accept('='):
                    e1 = self.expr()
                    self.expect(',')
                    e2 = self.expr()
                    e3 = self.expr() if self.accept(',') else None
                    self.expect('do')
                    b = self.block()
                    self.expect('end')
                    return N('fornum', (n1, e1, e2, e3), b, line=ln)
                names = [n1]
                while self.accept(','):
                    names.append(self.name())
                self.expect('in')
                exprs = self.exprlist()
                self.expect('do')
                b = self.block()
                self.expect('end')
                return N('forin', (names, exprs), b, line=ln)
            if c.val == 'function':
                self.i += 1
                path = [self.name()]
                is_method = False
                while self.accept('.'):
                    path.append(self.name())
                if self.accept(':'):
                    path.append(self.name())
                    is_method = True
                body = self.funcbody(is_method, ln)
                return N('funcstat', path, body, is_method, line=ln)
            if c.val == 'local':
                self.i += 1
                if self.accept('function'):
                    nm = self.name()
                    return N('localfunc', nm, self.funcbody(False, ln), line=ln)
                names = [self.name()]
                while self.accept(','):
                    names.append(self.name())
                exprs = self.exprlist() if self.accept('=') else []
                return N('local', names, exprs, line=ln)
        # expression statement: assignment or call
        e = self.suffixedexp()
        if self.at('=') or self.at(','):
            targets = [e]
            while self.accept(','):
                targets.append(self.suffixedexp())
            self.expect('=')
            return N('assign', targets, self.exprlist(), line=ln)
        if e.k not in ('call', 'methcall'):
            raise LuaSyntaxError('syntax error near %r' % (self.cur.val,), ln)
        return N('callstat', e, line=ln)

    def funcbody(self, is_method, ln):
        self.expect('(')
        params = ['self'] if is_method else []
        vararg = False
        if not self.at(')'):
            while True:
                if self.accept('...'):
                    vararg = True
                    break
                params.append(self.name())
                if not self.accept(','):
                    break
        self.expect(')')
        b = self.block()
        self.expect('end')
        return N('function', params, b, vararg, line=ln)

    def exprlist(self):
        out = [self.expr()]
        while self.accept(','):
            out.append(self.expr())
        return out

    def expr(self, limit=0):
        c = self.cur
        # ⚠ The unary test MUST check the token kind. Comparing `.val` alone makes the string
        # literal '#' (as in `select('#', ...)`) parse as the unary length operator.
        if ((c.kind == 'kw' and c.val == 'not')
                or (c.kind == 'punct' and c.val in ('-', '#'))):
            op = c.val
            self.i += 1
            e = N('unop', op, self.expr(UNARY_PRI), line=c.line)
        else:
            e = self.simpleexp()
        while True:
            o = self.cur.val
            pri = BINPRI.get(o)
            if not pri or pri[0] <= limit:
                break
            self.i += 1
            rhs = self.expr(pri[1])
            e = N('binop', o, (e, rhs), line=c.line)
        return e

    def simpleexp(self):
        c = self.cur
        ln = c.line
        if c.kind == 'num':
            self.i += 1
            return N('num', c.val, line=ln)
        if c.kind == 'str':
            self.i += 1
            return N('str', c.val, line=ln)
        if c.kind == 'kw' and c.val in ('nil', 'true', 'false'):
            self.i += 1
            return N(c.val, line=ln)
        if c.val == '...':
            self.i += 1
            return N('vararg', line=ln)
        if c.val == '{':
            return self.tablector()
        if c.kind == 'kw' and c.val == 'function':
            self.i += 1
            return self.funcbody(False, ln)
        return self.suffixedexp()

    def primaryexp(self):
        c = self.cur
        if c.val == '(':
            self.i += 1
            e = self.expr()
            self.expect(')')
            return N('paren', e, line=c.line)
        if c.kind == 'name':
            self.i += 1
            return N('name', c.val, line=c.line)
        raise LuaSyntaxError('unexpected symbol %r' % (c.val,), c.line)

    def suffixedexp(self):
        e = self.primaryexp()
        while True:
            c = self.cur
            if c.val == '.':
                self.i += 1
                e = N('index', e, N('str', self.name(), line=c.line), line=c.line)
            elif c.val == '[':
                self.i += 1
                k = self.expr()
                self.expect(']')
                e = N('index', e, k, line=c.line)
            elif c.val == ':':
                self.i += 1
                m = self.name()
                e = N('methcall', e, m, self.callargs(), line=c.line)
            elif c.val in ('(', '{') or c.kind == 'str':
                e = N('call', e, self.callargs(), line=c.line)
            else:
                return e

    def callargs(self):
        c = self.cur
        if c.kind == 'str':
            self.i += 1
            return [N('str', c.val, line=c.line)]
        if c.val == '{':
            return [self.tablector()]
        self.expect('(')
        args = [] if self.at(')') else self.exprlist()
        self.expect(')')
        return args

    def tablector(self):
        ln = self.cur.line
        self.expect('{')
        array, hash_ = [], []
        while not self.at('}'):
            if self.at('['):
                self.i += 1
                k = self.expr()
                self.expect(']')
                self.expect('=')
                hash_.append((k, self.expr()))
            elif self.cur.kind == 'name' and self.peek().val == '=':
                k = N('str', self.name(), line=ln)
                self.expect('=')
                hash_.append((k, self.expr()))
            else:
                array.append(self.expr())
            if not (self.accept(',') or self.accept(';')):
                break
        self.expect('}')
        return N('table', array, hash_, line=ln)


def parse(src):
    return Parser(lex(src)).chunk()
