import json, time, uuid
from pathlib import Path
from datetime import datetime

QDIR = Path("/tmp/vulnagent_msgs")

def log(msg):
    print(f"[TM {datetime.now().strftime(chr(37)+chr(72)+chr(58)+chr(37)+chr(77)+chr(58)+chr(37)+chr(83))}] {msg}", flush=True)

class Task:
    def __init__(s,t,v="",m=""):
        s.tid=t;s.vendor=v;s.model=m;s.phase="init"
        s.fofa=[];s.fw_url="";s.arch="";s.services=[];s.endpoints=[]
        s.findings=[];s.report="";s.error="";s.ts=time.time()
    def st(s):
        mp={"init":"init","fofa":"FOFA","dl":"dl","emu":"emu","scan":"scan","verify":"verify","done":"done"}
        return mp.get(s.phase,s.phase)

TASKS={}

def ec(a,**k):
    QDIR.mkdir(exist_ok=True)
    m=uuid.uuid1().hex[:8];k.update({"type":a,"mid":m,"ts":time.time()})
    (QDIR/f"cmd.emu.{m}.json").write_text(json.dumps(k,indent=2,ensure_ascii=False))
    log(f"ec:{a} to cmd.emu.{m}.json")

def vc(a,**k):
    QDIR.mkdir(exist_ok=True)
    m=uuid.uuid1().hex[:8];k.update({"type":a,"mid":m,"ts":time.time()})
    (QDIR/f"cmd.vuln.{m}.json").write_text(json.dumps(k,indent=2,ensure_ascii=False))
    log(f"vc:{a} to cmd.vuln.{m}.json")

def pr():
    rs=[]
    for pf in ["vulnagent.env_ready.","vulnagent.scan_result.","emu.status.","cmd.response.","emu.log.","vulnagent.report.","vulnagent.status."]:
        for f in QDIR.glob(pf+"*.json"):
            try:
                rs.append(json.loads(f.read_text(encoding="utf-8")))
                f.unlink()
            except:pass
    if rs: log(f"pr:{len(rs)} results")
    return rs

def up(rs):
    for r in rs:
        t=TASKS.get(r.get('task_id',''))
        if not t: continue
        tp=r.get("type","")
        log(f"up:{tp} {r.get('task_id','')[:8]} (phase={t.phase})")
        if "env_ready" in tp:
            t.arch=r.get("architecture","");t.services=r.get("services",[])
            t.endpoints=r.get("scan_endpoints",[]);t.phase="scan"
            vc("scan",task_id=t.tid,vendor=t.vendor,model=t.model,scan_endpoints=t.endpoints,rootfs_id=r.get("rootfs_id",""),arch=t.arch)
            log(f"->scan forwarded")
        elif "scan_result" in tp:
            fnd=r.get("findings",[]);t.findings=fnd
            t.phase="verify" if fnd else "done"
            if fnd:vc("verify",task_id=t.tid,findings=fnd);log("->verify forwarded")
            else:log("->done (no findings)")
        elif "status" in tp:
            t.phase=r.get("phase",t.phase);t.error=r.get("error","")
            log(f"->{t.phase}")
        elif "report" in tp:
            t.report=r.get("report","");t.phase="done"
            log("->done (report)")
        t.ts=time.time()
