import ast, os, numpy as np, coverage, collections
FLASK="/tmp/bigrepo/flask"
# 1) function line ranges per source file (AST)
def func_ranges(path):
    out=[]
    try: t=ast.parse(open(path).read())
    except Exception: return out
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)):
            out.append((n.name, n.lineno, n.end_lineno))
    return out
# 2) coverage contexts
cov=coverage.Coverage(data_file=os.path.join(FLASK,".coverage")); cov.load(); data=cov.get_data()
files=[f for f in data.measured_files() if "/src/flask/" in f and f.endswith(".py")]
# build test set + function set + activations
funcs=[]; func_key={}
for f in files:
    for (name,lo,hi) in func_ranges(f):
        key=f.split("/src/flask/")[-1]+"::"+name
        func_key.setdefault(key, len(funcs)); 
        if func_key[key]==len(funcs): funcs.append((key,f,lo,hi))
tests=set()
activ=collections.defaultdict(set)  # func_idx -> set(test)
for f in files:
    cbl=data.contexts_by_lineno(f)
    franges=[(func_key[f.split("/src/flask/")[-1]+"::"+nm],lo,hi) for (nm,lo,hi) in func_ranges(f)]
    for ln,ctxs in cbl.items():
        real=[c for c in ctxs if "::" in c]
        for c in real: tests.add(c)
        for (fi,lo,hi) in franges:
            if lo<=ln<=hi:
                for c in real: activ[fi].add(c)
tests=sorted(tests); tidx={t:i for i,t in enumerate(tests)}
nT,nF=len(tests),len(funcs)
M=np.zeros((nT,nF))
for fi,ts in activ.items():
    for t in ts: M[tidx[t],fi]=1.0
# drop all-zero cols (never-executed funcs) for the decomposition
nz=M.sum(0)>0
Mnz=M[:,nz]; fnz=[funcs[i][0] for i in range(nF) if nz[i]]
print(f"co-activation matrix: {nT} tests x {nF} functions ({int(nz.sum())} ever executed); density={Mnz.mean():.3f}")
# 3) POD = SVD (mean-center columns, the PCA form)
Mc = Mnz - Mnz.mean(0, keepdims=True)
U,S,Vt = np.linalg.svd(Mc, full_matrices=False)
energy=(S**2)/ (S**2).sum()
cum=np.cumsum(energy)
print("\nsingular-value energy (top 12):")
for k in range(min(12,len(S))):
    print(f"  mode {k+1:2d}: sv={S[k]:7.2f}  energy={energy[k]*100:5.1f}%  cum={cum[k]*100:5.1f}%")
k90=int(np.searchsorted(cum,0.90))+1
print(f"\nintrinsic dimensionality: {k90} modes capture 90% of behavioural variance (of {nF} functions / {nT} tests)")
# 4) name each of the top modes by its dominant functions
print("\n=== top behavioural modes (dominant functions by |loading|) ===")
for k in range(min(6,Vt.shape[0])):
    load=Vt[k]; order=np.argsort(-np.abs(load))[:8]
    toks=[fnz[i].split("::")[-1]+f"({'+' if load[i]>0 else '-'})" for i in order]
    print(f"  mode {k+1}: "+", ".join(toks))
# 5) test redundancy: near-duplicate activation rows
from numpy.linalg import norm
norms=norm(Mnz,axis=1,keepdims=True); norms[norms==0]=1
R=Mnz/norms
dup=0; seen=[]
sims=R@R.T
np.fill_diagonal(sims,0)
pairs=int((sims>0.999).sum()//2)
print(f"\ntest redundancy: {pairs} test-pairs with ~identical activation profile (cosine>0.999) out of {nT} tests")
