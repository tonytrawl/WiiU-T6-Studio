import socket, sys, struct, time
class RSP:
    def __init__(s,h,p): s.s=socket.create_connection((h,p),timeout=15); s.s.settimeout(15); s.buf=b''
    def _r(s):
        try: d=s.s.recv(4096); s.buf+=d; return d
        except socket.timeout: return b''
    def send(s,b):
        cs=sum(b.encode())&0xFF; s.s.sendall(b'$'+b.encode()+b'#'+('%02x'%cs).encode())
        t=time.time()
        while b'+' not in s.buf and time.time()-t<8:
            if not s._r(): time.sleep(0.01)
        i=s.buf.find(b'+')
        if i>=0: s.buf=s.buf[i+1:]
    def recv(s,to=30):
        t=time.time()
        while time.time()-t<to:
            i=s.buf.find(b'$'); j=s.buf.find(b'#',i+1) if i>=0 else -1
            if i>=0 and j>=0 and len(s.buf)>=j+3:
                body=s.buf[i+1:j].decode('latin-1'); s.buf=s.buf[j+3:]; s.s.sendall(b'+'); return body
            if not s._r(): time.sleep(0.02)
        return None
    def cmd(s,b,to=15): s.send(b); return s.recv(to)
r=RSP('127.0.0.1',1337); r.buf=b''
print('? ->', r.cmd('?'))
g=r.cmd('g'); print('g len=%d bytes'%(len(g)//2 if g else 0))
if g:
    raw=bytes.fromhex(g); n=len(raw)//4
    W=lambda i: struct.unpack_from('>I',raw,i*4)[0]
    for i in range(0,32,4):
        print('  r%-2d %08x  r%-2d %08x  r%-2d %08x  r%-2d %08x'%(i,W(i),i+1,W(i+1),i+2,W(i+2),i+3,W(i+3)))
    for i in range(32,n): print('  g[%d]=0x%08x'%(i,W(i)))
print('--- p reads 32..45 ---')
for idx in range(32,46):
    v=r.cmd('p%x'%idx,10)
    if v and len(v)>=8 and v[0] not in 'E':
        try:
            iv=struct.unpack('>I',bytes.fromhex(v[:8]))[0]
            print('  p%d=0x%08x%s'%(idx,iv,' <CODE?>' if 0x02000000<=iv<0x04000000 else ''))
        except: print('  p%d=%r'%(idx,v))
# leave halted; do NOT detach/continue
