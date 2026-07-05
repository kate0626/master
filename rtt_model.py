"""全時間(RTT) = walk_time + RTT × remote_auth_calls をモデル化。
remote_auth_calls はヒット率が高いほど減る → RTTが大きいほど提案手法が有利になるかを見る。"""
import json, os, tempfile
os.environ.setdefault("MPLCONFIGDIR", tempfile.mkdtemp())
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE="base/proposed_cache/results/alpha0.01_walks_100_capa_100/vldb_nobt"
POLICIES=[("none","none_100_h-0"),("LRU","lru_100_h-0"),("ARC","arc_100_h-0"),
          ("memo","memo_100_h-0"),("Proposed","ppr_demand_cap100_t2_d1_l1_b1_g0_h-0")]

def load(tag):
    import glob
    fs=glob.glob(f"{BASE}/{tag}/start=0_*global_transition.json")
    if not fs: return None
    j=json.loads(open(fs[0]).read())
    walk=float(j.get("walk_time_total",0.0))
    remote=sum(int(e.get("stats",{}).get("remote_auth_calls",0)) for e in j.get("per_server_access_stats",[]))
    local =sum(int(e.get("stats",{}).get("local_auth_calls",0))  for e in j.get("per_server_access_stats",[]))
    miss=int(j.get("cache miss",0)); hit=int(j.get("cache hit",0))
    hr=hit/(hit+miss) if hit+miss else 0
    return dict(walk=walk, remote=remote, local=local, miss=miss, hr=hr)

data={name:load(tag) for name,tag in POLICIES}
data={k:v for k,v in data.items() if v}

# RTT(ms) を振って総時間(s) = walk + RTT_s × remote
RTTs_ms=[0.1,0.5,1,2,5,10,20,50,100]
print(f"{'policy':<10}{'hit':>7}{'remote_calls':>13}{'walk_s':>9}", *[f"{r}ms".rjust(9) for r in RTTs_ms])
for name,d in data.items():
    tot=lambda r: d["walk"]+ (r/1000.0)*d["remote"]
    print(f"{name:<10}{d['hr']:>7.3f}{d['remote']:>13}{d['walk']:>9.1f}",
          *[f"{tot(r):9.1f}" for r in RTTs_ms])

# 図: 総時間 vs RTT
fig,ax=plt.subplots(figsize=(7.5,5))
xs=[0.1,0.5,1,2,5,10,20,50,100]
colors={"none":"#888780","LRU":"#185FA5","ARC":"#1D9E75","memo":"#D85A30","Proposed":"#6D2E46"}
for name,d in data.items():
    ys=[d["walk"]+(r/1000.0)*d["remote"] for r in xs]
    ax.plot(xs,ys,"-o",label=f"{name} (hit={d['hr']:.2f}, remote={d['remote']})",
            color=colors.get(name),lw=2,ms=5)
ax.set_xscale("log"); ax.set_xlabel("RTT per remote auth call (ms, log)")
ax.set_ylabel("modeled total time (s)  = walk + RTT × remote_calls")
ax.set_title("VLDB start=0: total time vs RTT (higher hit → fewer remote calls)")
ax.grid(alpha=.3, which="both"); ax.legend(fontsize=8)
fig.tight_layout(); fig.savefig("fig_rtt_model.png",dpi=140)
print("\n[OUT] fig_rtt_model.png")
