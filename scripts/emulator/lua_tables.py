"""Read literal Lua data tables without executing Lua code."""
import re
TOKEN=re.compile(r'\s+|--[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|-?\d+(?:\.\d+)?|[A-Za-z_]\w*|.',re.S)
def string(raw):
 def esc(m):
  s=m[1]
  if s.isdigit():return chr(int(s))
  return {'n':'\n','r':'\r','t':'\t','a':'\a','b':'\b','f':'\f','v':'\v','\n':'\n','\\':'\\','"':'"',"'":"'"}.get(s,s)
 return re.sub(r'\\(\d{1,3}|.)',esc,raw[1:-1],flags=re.S)
class Parser:
 def __init__(self,text):self.t=[m[0] for m in TOKEN.finditer(text) if not m[0].isspace() and not m[0].startswith('--')];self.i=0
 def pop(self,want=None):
  t=self.t[self.i];self.i+=1
  if want is not None and t!=want:raise ValueError((want,t,self.i))
  return t
 def val(self):
  t=self.pop()
  if t=='{':
   result={};idx=1
   while self.t[self.i]!='}':
    if self.t[self.i]=='[':
     self.pop('[');key=self.val();self.pop(']');self.pop('=');value=self.val()
    elif self.i+1<len(self.t) and self.t[self.i+1]=='=':key=self.pop();self.pop('=');value=self.val()
    else:key=idx;idx+=1;value=self.val()
    if key in result:raise ValueError(f'duplicate table key {key}')
    result[key]=value
    if self.t[self.i] in (',',';'):self.pop()
    elif self.t[self.i]!='}':raise ValueError('expected comma')
   self.pop('}');return result
  if t[0] in ('"',"'"):return string(t)
  if t in ('true','false','nil'):return {'true':True,'false':False,'nil':None}[t]
  if re.fullmatch(r'-?\d+(?:\.\d+)?',t):return float(t) if '.' in t else int(t)
  raise ValueError(f'nonliteral {t}')
def tables(path):
 p=Parser(path.read_text('utf-8'));out={}
 while p.i<len(p.t)-2:
  if re.fullmatch(r'gd\w+',p.t[p.i]) and p.t[p.i+1:p.i+3]==['=','{']:
   name=p.pop();p.pop('=');out[name]=p.val()
  else:p.i+=1
 return out
