"""Two-phase GDB capture that does NOT block the boot:
  phase 1: connect + plain continue -> game boots to menu freely (no watchpoint).
           loops draining the socket AND polling for a trigger file.
  phase 2: when the trigger file appears (created right before you hit START),
           interrupt the PPC, arm a write-watchpoint in the skate-load overrun
           zone, continue. The first write there during skate load = the culprit;
           capture full stack/regs -> _gdb_capture.txt, then detach.

Run in BACKGROUND. Trigger: create native_linker/_ARM_NOW to fire phase 2.
Usage: python _gdb_arm.py [hex_addr]   (default 0x10ec0000)"""
import socket, sys, struct, time, os

HOST, PORT = '127.0.0.1', 1337
TRIGGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_ARM_NOW')
CAPFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_gdb_capture.txt')

class RSP:
    def __init__(s, h, p):
        s.s = socket.create_connection((h, p), timeout=20); s.s.settimeout(0.5); s.buf = b''
    def _read(s):
        try:
            d = s.s.recv(8192); s.buf += d; return d
        except socket.timeout:
            return b''
    @staticmethod
    def _decode(body):
        out = []; i = 0
        while i < len(body):
            c = body[i]
            if c == '}' and i+1 < len(body): out.append(chr(ord(body[i+1]) ^ 0x20)); i += 2
            elif c == '*' and out and i+1 < len(body): out.append(out[-1]*(ord(body[i+1])-29)); i += 2
            else: out.append(c); i += 1
        return ''.join(out)
    def _try_pkt(s):
        i = s.buf.find(b'$')
        if i < 0: return None
        k = s.buf.find(b'#', i+1)
        if k < 0 or len(s.buf) < k+3: return None
        body = s.buf[i+1:k].decode('latin-1'); s.buf = s.buf[k+3:]
        s.s.sendall(b'+'); return s._decode(body)
    def send(s, body):
        cs = sum(body.encode()) & 0xFF
        s.s.sendall(b'$' + body.encode() + b'#' + ('%02x' % cs).encode())
        t = time.time()
        while b'+' not in s.buf and time.time()-t < 8:
            if not s._read(): time.sleep(0.01)
        i = s.buf.find(b'+')
        if i >= 0: s.buf = s.buf[i+1:]
    def cmd(s, body, to=30):
        s.send(body); t = time.time()
        while time.time()-t < to:
            p = s._try_pkt()
            if p is not None: return p
            if not s._read(): time.sleep(0.02)
        return None
    def interrupt(s):
        s.s.sendall(b'\x03')

def main():
    addr = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x10ec0000
    if os.path.exists(TRIGGER): os.remove(TRIGGER)
    r = RSP(HOST, PORT); r.buf = b''
    print('connected. qSupported ->', r.cmd('qSupported', 8))
    print('? ->', r.cmd('?', 8))
    # PHASE 1: plain continue, game boots to menu; poll for trigger
    r.send('c')
    print('game running freely (boot to menu, navigate MP->skate). waiting for _ARM_NOW...')
    stop = None
    while True:
        p = r._try_pkt()
        if p and p[0] in 'TSW':      # target stopped/crashed on its own
            stop = p; print('target stopped on its own:', p); break
        r._read()
        if os.path.exists(TRIGGER):
            print('TRIGGER seen -> interrupting PPC to arm watchpoint')
            r.interrupt()
            time.sleep(0.3)
            # consume the interrupt stop
            t = time.time()
            while time.time()-t < 5:
                q = r._try_pkt()
                if q: print('halted:', q); break
                r._read(); time.sleep(0.02)
            resp = r.cmd('Z2,%x,4' % addr, 8)
            print('armed Z2,%x,4 -> %r' % (addr, resp))
            if resp != 'OK':
                print('Z4 fallback ->', r.cmd('Z4,%x,4' % addr, 8))
            print('continue -> click START now; waiting for the culprit write...')
            stop = r.cmd('c', 600)
            print('STOP:', stop)
            break
        time.sleep(0.05)
    # capture
    cap = ['STOP %s' % stop]
    g = r.cmd('g', 30) or ''; cap.append('G %s' % g)
    print('g: %d regs' % (len(g)//8))
    raw = bytes.fromhex(g) if g and len(g) % 2 == 0 else b''; n = len(raw)//4
    W = lambda i: struct.unpack_from('>I', raw, i*4)[0] if i < n else 0
    for i in range(0, min(32, n), 4):
        print('  r%-2d %08x  r%-2d %08x  r%-2d %08x  r%-2d %08x' % (i,W(i),i+1,W(i+1),i+2,W(i+2),i+3,W(i+3)))
    for i in range(32, n):
        v = W(i); print('  g[%d]=0x%08x%s' % (i, v, ' <CODE?>' if 0x02000000 <= v < 0x04000000 else ''))
    xml = r.cmd('qXfer:features:read:target.xml:0,1000', 15) or ''; cap.append('XML %s' % xml[:1500])
    sp = W(1)
    stk = r.cmd('m%x,200' % sp, 15) or ''; cap.append('STACK@%x %s' % (sp, stk))
    sb = bytes.fromhex(stk) if stk and len(stk) % 2 == 0 else b''
    print('--- stack @0x%08x: return addrs into code (0x02xxxxxx) ---' % sp)
    for off in range(0, len(sb)-3, 4):
        v = struct.unpack_from('>I', sb, off)[0]
        if 0x02000000 <= v < 0x04000000:
            print('  sp+0x%03x = 0x%08x' % (off, v))
    open(CAPFILE, 'w').write('\n'.join(cap) + '\n')
    print('saved', CAPFILE)
    r.cmd('D', 8)

if __name__ == '__main__':
    try: main()
    except Exception as e:
        import traceback; traceback.print_exc()
