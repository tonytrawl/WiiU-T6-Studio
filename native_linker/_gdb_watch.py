"""Minimal GDB Remote Serial Protocol client for Cemu's GDB stub (Espresso/PPC).
Connects to localhost:1337, sets a WRITE watchpoint on a guest address, continues,
and on the trapping write dumps PC/LR + GPRs so the culprit guest function is named.

Usage: python _gdb_watch.py <hex_addr> [len] [--sw]
Default target = 0x10ec0000 (deep in skate's overrun zone; untouched in a working boot,
so the FIRST write there is the runaway fill/copy that stomps the thread pool)."""
import socket, sys, time

HOST, PORT = '127.0.0.1', 1337

class RSP:
    def __init__(self, host, port):
        self.s = socket.create_connection((host, port), timeout=30)
        self.s.settimeout(30)
        self.buf = b''
    def _read(self, n=4096):
        try:
            d = self.s.recv(n)
            if d: self.buf += d
            return d
        except socket.timeout:
            return b''
    def send(self, body):
        cs = sum(body.encode()) & 0xFF
        pkt = b'$' + body.encode() + b'#' + ('%02x' % cs).encode()
        self.s.sendall(pkt)
        # wait for ack '+'
        t = time.time()
        while b'+' not in self.buf and time.time() - t < 10:
            if not self._read(): time.sleep(0.01)
        i = self.buf.find(b'+')
        if i >= 0: self.buf = self.buf[i+1:]
    @staticmethod
    def _decode(body):
        # GDB RSP: un-escape '}x'->chr(x^0x20); expand RLE 'c*n'->c repeated (n-29+1)
        out = []
        i = 0
        while i < len(body):
            c = body[i]
            if c == '}' and i+1 < len(body):
                out.append(chr(ord(body[i+1]) ^ 0x20)); i += 2
            elif c == '*' and out and i+1 < len(body):
                cnt = ord(body[i+1]) - 29
                out.append(out[-1] * cnt); i += 2
            else:
                out.append(c); i += 1
        return ''.join(out)

    def recv_pkt(self, timeout=120):
        t = time.time()
        while time.time() - t < timeout:
            i = self.buf.find(b'$')
            # find the '#' that is the real terminator (2 hex chars follow)
            j = -1
            if i >= 0:
                k = i + 1
                while True:
                    k = self.buf.find(b'#', k)
                    if k < 0: break
                    if len(self.buf) >= k + 3:
                        j = k; break
                    else:
                        break
            if i >= 0 and j >= 0:
                body = self.buf[i+1:j].decode('latin-1')
                self.buf = self.buf[j+3:]
                self.s.sendall(b'+')
                return self._decode(body)
            if not self._read(): time.sleep(0.02)
        return None
    def cmd(self, body, timeout=30):
        self.send(body)
        return self.recv_pkt(timeout)

def main():
    addr = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x10ec0000
    ln = int(sys.argv[2]) if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else 4
    kind = '2'  # Z2 = write watchpoint (Z4=access). --sw forces sw bp fallback
    r = RSP(HOST, PORT)
    # drain any initial stop packet
    r.buf = b''
    print('qSupported ->', r.cmd('qSupported'))
    print('? (halt reason) ->', r.cmd('?'))
    # set write watchpoint Z2,addr,len
    resp = r.cmd('Z2,%x,%x' % (addr, ln))
    print('Z2,%x,%x -> %r' % (addr, ln, resp))
    if resp != 'OK':
        resp = r.cmd('Z4,%x,%x' % (addr, ln))
        print('Z4 (access) fallback -> %r' % resp)
    import struct
    # FILTER LOOP: early boot ZEROES this region (OS heap init). The skate overrun
    # writes NON-ZERO garbage. Continue past every zero-write; stop only when a
    # non-zero value lands at the watched address = the actual runaway fill.
    print('continuing + filtering zero-writes (waiting for the non-zero overrun)...')
    hits = 0
    stop = None
    while True:
        stop = r.cmd('c', timeout=600)
        if stop is None:
            print('no stop within timeout; aborting'); break
        hits += 1
        val = r.cmd('m%x,4' % addr, timeout=10) or ''
        nz = any(ch != '0' for ch in val[:8]) if len(val) >= 8 else False
        if hits % 50 == 0 or nz:
            print('  hit #%d @%s value=%s%s' % (hits, sys.argv[1] if len(sys.argv)>1 else hex(addr),
                                                val[:8], '  <-- NON-ZERO (overrun!)' if nz else ''))
        if nz:
            print('=> caught NON-ZERO write after %d zero-writes' % (hits-1))
            break
        if hits > 20000:
            print('=> 20000 zero-writes, giving up (region only ever zeroed?)'); break
    print('STOP packet:', stop)
    # capture EVERYTHING crash-proof: raw g first, then parse, then p-reads, always detach
    cap = ['STOP %s' % stop]
    try:
        g = r.cmd('g', timeout=30) or ''
        cap.append('G %s' % g)
        print('full g (%d regs / %d bytes)' % (len(g)//8, len(g)//2))
        raw = bytes.fromhex(g) if len(g) % 2 == 0 else b''
        n = len(raw)//4
        def w(i): return struct.unpack_from('>I', raw, i*4)[0] if i < n else 0
        for i in range(0, min(32, n), 4):
            print('  r%-2d %08x  r%-2d %08x  r%-2d %08x  r%-2d %08x'
                  % (i,w(i),i+1,w(i+1),i+2,w(i+2),i+3,w(i+3)))
        # anything past r31 = PC/MSR/CR/LR/CTR/XER... flag code-looking words
        for i in range(32, n):
            v = w(i); tag = ' <CODE?>' if 0x02000000 <= v < 0x04000000 else ''
            print('  g[%d] = 0x%08x%s' % (i, v, tag))
    except Exception as e:
        print('g parse error:', e); import traceback; traceback.print_exc()
    # register map (names -> which g index is pc/lr)
    try:
        xml = r.cmd('qXfer:features:read:target.xml:0,1000', timeout=15) or ''
        cap.append('XML %s' % xml[:1200])
        import re
        names = re.findall(r'name="([a-z0-9_]+)"', xml)
        if names: print('reg names (first 40):', names[:40])
    except Exception as e:
        print('target.xml error:', e)
    # STACK WALK: read 256B at r1 (sp) to find return addrs into game code 0x02xxxxxx
    try:
        sp = w(1)
        m = r.cmd('m%x,100' % sp, timeout=15) or ''
        cap.append('STACK@%x %s' % (sp, m))
        sb = bytes.fromhex(m) if len(m) % 2 == 0 else b''
        print('--- stack @0x%08x, words looking like game code (0x02xxxxxx) ---' % sp)
        for off in range(0, len(sb)-3, 4):
            v = struct.unpack_from('>I', sb, off)[0]
            if 0x02000000 <= v < 0x04000000:
                print('  sp+0x%02x = 0x%08x  <-- return addr into game/loader code' % (off, v))
    except Exception as e:
        print('stack read error:', e)
    open('_gdb_capture.txt', 'w').write('\n'.join(cap) + '\n')
    print('capture saved to _gdb_capture.txt')
    r.cmd('D')  # detach so Cemu keeps running

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        import traceback; traceback.print_exc()
        print('NOTE: capture (if any) saved to _gdb_capture.txt')
