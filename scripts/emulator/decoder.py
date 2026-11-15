import pathlib,struct,hashlib
from elftools.elf.elffile import ELFFile
from unicorn import Uc,UC_ARCH_ARM64,UC_MODE_ARM,UC_HOOK_CODE
from unicorn.arm64_const import *
SUPPORTED_SHA256='22af7e85f09f27e428642fb8d001cc8b2218dcfa728841f7221b1e4a7f6a70bf'
class Decoder:
 def __init__(self,library):
  library=pathlib.Path(library)
  if hashlib.sha256(library.read_bytes()).hexdigest()!=SUPPORTED_SHA256:
   raise ValueError('Unsupported game library. Review decoder offsets before using a new APK; existing published data is untouched.')
  self.u=Uc(UC_ARCH_ARM64,UC_MODE_ARM); u=self.u
  u.mem_map(0,0x2000000); u.mem_map(0x4000000,0x4000000); u.mem_map(0x9000000,0x200000)
  self.heap=0x4000000; self.plt={}; self.symbols={}; self.hashes={}
  with library.open('rb') as f:
   e=ELFFile(f)
   for seg in e.iter_segments():
    if seg['p_type']=='PT_LOAD': u.mem_write(seg['p_vaddr'],seg.data())
   dyn=e.get_section_by_name('.dynsym'); self.symbols={s.name:s['st_value'] for s in dyn.iter_symbols() if s['st_value']}
   plt=e.get_section_by_name('.plt')
   for i,r in enumerate(e.get_section_by_name('.rela.plt').iter_relocations()): self.plt[plt['sh_addr']+32+i*16]=dyn.get_symbol(r['r_info_sym']).name
   for r in e.get_section_by_name('.rela.dyn').iter_relocations():
    if r['r_info_type']==1027: u.mem_write(r['r_offset'],struct.pack('<Q',r['r_addend']))
    elif r['r_info_type'] in (257,1025):
     s=dyn.get_symbol(r['r_info_sym']); u.mem_write(r['r_offset'],struct.pack('<Q',s['st_value']+r['r_addend']))
  u.reg_write(UC_ARM64_REG_TPIDR_EL0,0x9000000)
  u.hook_add(UC_HOOK_CODE,self.hook,begin=plt['sh_addr'],end=plt['sh_addr']+plt['sh_size'])
 def alloc(self,n):
  a=self.heap; self.heap+=(n+15)//16*16
  if self.heap>=0x8000000: raise ValueError('heap exhausted')
  return a
 def cstr(self,a):
  return bytes(self.u.mem_read(a,4096)).split(b'\0')[0]
 def string(self,a,s):
  p=self.alloc(len(s)+1); self.u.mem_write(p,s+b'\0'); self.u.mem_write(a,struct.pack('<QQQ',(len(s)+16)|1,len(s),p))
 def getstring(self,a):
  b=bytes(self.u.mem_read(a,24))
  return bytes(self.u.mem_read(struct.unpack_from('<Q',b,16)[0],struct.unpack_from('<Q',b,8)[0])) if b[0]&1 else b[1:1+b[0]//2]
 def hook(self,u,addr,size,data):
  name=self.plt.get(addr)
  if not name: raise RuntimeError(f'unknown PLT {addr:x}')
  a,b,c=[u.reg_read(UC_ARM64_REG_X0+i) for i in range(3)]
  if name.startswith('_ZN3MD5C1ERK'):
   self.hashes[a]=hashlib.md5(self.getstring(b)).hexdigest().encode();u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR));return
  if name=='_ZN3MD58toStringEv' and a in self.hashes:
   self.string(u.reg_read(UC_ARM64_REG_X8),self.hashes[a]);u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR));return
  if name in self.symbols:
   u.reg_write(UC_ARM64_REG_PC,self.symbols[name]); return
  a,b,c=[u.reg_read(UC_ARM64_REG_X0+i) for i in range(3)]
  result=a
  if name in ('_Znwm','_Znam','malloc'): result=self.alloc(a)
  elif name=='calloc': result=self.alloc(a*b); u.mem_write(result,bytes(a*b))
  elif name in ('_ZdlPv','_ZdaPv','free'): pass
  elif name in ('memcpy','memmove','__memcpy_chk','__memmove_chk'): u.mem_write(a,bytes(u.mem_read(b,c)))
  elif name in ('memset','__memset_chk'): u.mem_write(a,bytes([b&255])*c)
  elif name=='strlen': result=len(self.cstr(a))
  elif name in ('strcpy','__strcpy_chk'): u.mem_write(a,self.cstr(b)+b'\0')
  elif name=='memcmp':
   x,y=bytes(u.mem_read(a,c)),bytes(u.mem_read(b,c)); result=(x>y)-(x<y)
  elif name=='__vsprintf_chk':
   fmt=self.cstr(u.reg_read(UC_ARM64_REG_X3)); va=u.reg_read(UC_ARM64_REG_X4)
   stack,gr,vr,go,vo=struct.unpack('<QQQii',u.mem_read(va,32))
   arg=struct.unpack('<Q',u.mem_read(gr+go if go<0 else stack,8))[0]
   if fmt==b'%1.2x':fmt=b'%02x'
   if fmt not in (b'%02x',b'%02X',b'%x',b'%d'):raise RuntimeError(f'format {fmt!r}')
   text=(fmt.decode()%arg).encode();u.mem_write(a,text+b'\0');result=len(text)
  else: raise RuntimeError(f'unhandled {name} at {addr:x}')
  u.reg_write(UC_ARM64_REG_X0,result&((1<<64)-1)); u.reg_write(UC_ARM64_REG_PC,u.reg_read(UC_ARM64_REG_LR))
 def call(self,addr,*args,result=None):
  u=self.u; u.reg_write(UC_ARM64_REG_SP,0x91ff000); u.reg_write(UC_ARM64_REG_LR,0x1fff000)
  for i,a in enumerate(args):u.reg_write(UC_ARM64_REG_X0+i,a)
  if result is not None:u.reg_write(UC_ARM64_REG_X8,result)
  u.emu_start(addr,0x1fff000,timeout=60000000,count=200000000)
  if u.reg_read(UC_ARM64_REG_PC)!=0x1fff000: raise RuntimeError('emulation limit')
  return u.reg_read(UC_ARM64_REG_X0)
 def decode(self,blob):
  self.heap=0x4000000
  u=self.u; code=blob[-5]
  if 97<=code<=122:
   table=0x43f89c; ptr=table+struct.unpack('<i',u.mem_read(table+(code-97)*4,4))[0]
  else:ptr=0x3fceb3
  a,b,key=self.alloc(24),self.alloc(24),self.alloc(24)
  self.string(a,b'[ALERT ERROR]:req data error,need retry!');self.string(b,self.cstr(ptr))
  self.call(0x6cc9bc,a,b,blob[-6],blob[-5],result=key)
  src=self.alloc(len(blob)+8);u.mem_write(src,blob);size=self.alloc(8);u.mem_write(size,struct.pack('<Q',len(blob)))
  dst=self.call(0x6df03c,src,size,key,1)
  n=struct.unpack('<Q',u.mem_read(size,8))[0]
  if not 0<n<=len(blob):raise ValueError('Invalid decoded length')
  decoded=bytes(u.mem_read(dst,n))
  decoded.decode('utf-8',errors='strict')
  if not decoded.startswith((b'if not ',b'local gd')):raise ValueError('Unexpected static table format')
  return decoded
