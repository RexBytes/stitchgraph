import ast, os, numpy as np, coverage, collections
FLASK="/tmp/bigrepo/flask"
def func_ranges(path):
    out=[]
    try: t=ast.parse(open(path).read())
    except Exception: return out
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): out.append((n.name,n.lineno,n.end_lineno))
    return out
cov=coverage.Coverage(data_file=os.path.join(FLASK,".coverage")); cov.load(); data=cov.get_data()
files=[f for f in data.measured_files() if "/src/flask/" in f and f.endswith(".py")]
funcs=[]; fk={}
for f in files:
    for (nm,lo,hi) in func_ranges(f):
        key=f.split("/src/flask/")[-1]+"::"+nm
        if key not in fk: fk[key]=len(funcs); funcs.append(key)
tests=set(); activ=collections.defaultdict(set)
for f in files:
    cbl=data.contexts_by_lineno(f); fr=[(fk[f.split("/src/flask/")[-1]+"::"+nm],lo,hi) for (nm,lo,hi) in func_ranges(f)]
    for ln,ctxs in cbl.items():
        real=[c for c in ctxs if "::" in c]
        for c in real: tests.add(c)
        for (fi,lo,hi) in fr:
            if lo<=ln<=hi:
                for c in real: activ[fi].add(c)
tests=sorted(tests); tidx={t:i for i,t in enumerate(tests)}
nT,nF=len(tests),len(funcs)
M=np.zeros((nT,nF))
for fi,ts in activ.items():
    for t in ts: M[tidx[t],fi]=1.0
nz=M.sum(0)>0; Mnz=M[:,nz]; ncov=int(nz.sum())

# --- USE 1: suite minimization (greedy set cover over the coverage matrix) ---
covered=set(); chosen=[]; rows=[set(np.where(Mnz[i]>0)[0]) for i in range(nT)]
target=set(range(Mnz.shape[1]))
while covered!=target:
    best=max(range(nT), key=lambda i: len(rows[i]-covered))
    gain=len(rows[best]-covered)
    if gain==0: break
    covered|=rows[best]; chosen.append(best)
print(f"USE 1 — suite minimization: {len(chosen)} of {nT} tests cover all {ncov} executed functions "
      f"({len(chosen)/nT*100:.1f}% of the suite). The other {nT-len(chosen)} add no new function coverage.")

# --- USE 2: mode -> module coherence (does each POD mode concentrate in a few files?) ---
Mc=Mnz-Mnz.mean(0,keepdims=True); U,S,Vt=np.linalg.svd(Mc,full_matrices=False)
fnz=[funcs[i] for i in range(nF) if nz[i]]
def modfile(k):
    order=np.argsort(-np.abs(Vt[k]))[:12]
    files_=collections.Counter(fnz[i].split("::")[0] for i in order)
    return files_.most_common(3)
print("\nUSE 2 — each mode concentrates in specific modules (top files among its 12 dominant functions):")
for k in range(6):
    print(f"  mode {k+1}: "+", ".join(f"{fl}×{c}" for fl,c in modfile(k)))
