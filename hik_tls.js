// Hook SSL_read/SSL_write in EVERY loaded module that exports them (Hik-Connect
// bundles its own libssl + libopensslwrap + conscrypt). Dump HTTP-looking data.
function looksHttp(s){return typeof s==='string' && /^(GET|POST|PUT|DELETE) |HTTP\/1|\{"|alarm|record|event|message|hik-connect/i.test(s);}
function b2s(p,n){try{return p.readUtf8String(n);}catch(e){return null;}}
const hooked={};
function hookAll(){
  let count=0;
  for(const m of Process.enumerateModules()){
    if(!/ssl|opensslwrap/i.test(m.name)) continue;
    for(const fn of ['SSL_write','SSL_read']){
      const a=m.findExportByName(fn);
      if(!a||hooked[a.toString()]) continue;
      hooked[a.toString()]=1;
      if(fn==='SSL_write'){
        Interceptor.attach(a,{onEnter(args){const n=args[2].toInt32();if(n>0){const s=b2s(args[1],n);if(looksHttp(s))send('\n>>> OUT['+m.name+'] '+n+'B\n'+s.slice(0,2500));}}});
      }else{
        Interceptor.attach(a,{onEnter(args){this.b=args[1];},onLeave(r){const n=r.toInt32();if(n>0){const s=b2s(this.b,n);if(looksHttp(s))send('\n<<< IN['+m.name+'] '+n+'B\n'+s.slice(0,2500));}}});
      }
      count++; send('[+] hooked '+fn+' in '+m.name);
    }
  }
  return count;
}
hookAll();
// keep re-scanning as new SSL libs load
setInterval(hookAll, 500);
send('[*] multi-lib TLS dump armed');
